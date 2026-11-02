"""本地 n-gram 语域检测后端：零 API 成本的译文语域异常标记。

引擎标识：ForNgramCheck（未注册进 BaseEngine.ENGINE_MODULE_PATHS——纯本地
步骤不走令牌池与 API 客户端，故不继承 BaseEngine/BaseDetectRound，避免无
API 配置时被禁用）。

与 ForFluencyCheck 的关系（解耦设计）：规则初筛→AI 复核的通顺检测与本检测
互不调用、互不喂养候选；两者可在 afterTranslation 中单独启用，双开时由
llm_postprocess 收尾做交叉印证统计。

行为：加载 n-gram 分层索引（GalTransl/ngram_rules，role 快照驱动），对每句
译文（proofread_zh 优先，回退 pre_dst，与 fluency_rules 同口径）打语域分，
低于 `gpt.ngramThreshold` 的句子置 ngram_issue（问题类型由 find_problems
认领为「语域异常」）。阈值留空为观察模式：照常打分并输出分布日志，但不写
标记。索引缺失/损坏或目标语言非中文时整体跳过（_disabled_reason，行为与
BaseDetectRound 的禁用语义一致）。
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from GalTransl.ConfigHelper import CProjectConfig
from GalTransl import LANG_SUPPORTED
from GalTransl import ngram_rules as nr

LOGGER = logging.getLogger("GalTransl.ngramcheck")


class ForNgramCheck:
    """n-gram 语域检测：batch_translate 签名与现有后处理分支兼容，纯本地执行。"""

    def __init__(self, projectConfig: CProjectConfig) -> None:
        self.pj_config = projectConfig
        self._log_tag = "[N-gram语域检测]"
        self._disabled_reason = ""
        self._observe_only = False
        self._index: Optional[nr.NgramIndex] = None
        self._threshold: Optional[float] = None
        self._lam = nr.DEFAULT_LAMBDA

        # 目标语言非中文时无从谈中文语域（口径同 ForFluencyCheck._lang_supports_fluency；
        # 键名与 BaseEngine 一致——keyValues 对 common 段拍平一层，真实键是 language）
        val = str(projectConfig.getKey("language", "") or "")
        target = val.split("2")[-1] if "2" in val else "zh-cn"
        target_lang = LANG_SUPPORTED.get(target, target)
        if "Chinese" not in str(target_lang or ""):
            self._disabled_reason = "目标语言非中文，n-gram 语域检测不适用"
            return

        index_dir = str(projectConfig.getKey("gpt.ngramIndexPath", "") or "").strip()
        if not index_dir:
            index_dir = nr.default_index_dir()
        self._index, err = nr.load_index(index_dir)
        if self._index is None:
            self._disabled_reason = err
            return

        lam = projectConfig.getKey("gpt.ngramLambda", nr.DEFAULT_LAMBDA)
        try:
            self._lam = float(lam)
        except (TypeError, ValueError):
            self._lam = nr.DEFAULT_LAMBDA

        threshold_raw = projectConfig.getKey("gpt.ngramThreshold", "")
        # 只把「未配置/空白」当观察模式；数字 0 是合法阈值（低于 0 即标记），
        # 不能因 0 为假值被吞进观察模式
        if threshold_raw is None or str(threshold_raw).strip() == "":
            # 观察模式：只打分日志不标记，供阈值标定前试用
            self._observe_only = True
            LOGGER.info(
                "%s 未配置 gpt.ngramThreshold，进入观察模式（只打分日志，不标记问题）",
                self._log_tag,
            )
        else:
            try:
                self._threshold = float(threshold_raw)
            except (TypeError, ValueError):
                self._observe_only = True
                LOGGER.warning(
                    "%s gpt.ngramThreshold=%r 无法解析为数字，进入观察模式",
                    self._log_tag, threshold_raw,
                )

    @property
    def disabled_reason(self) -> str:
        return self._disabled_reason

    async def batch_translate(
        self, name: str, output: str, trans_list: list, num_better: int, gpt_dic=None
    ) -> None:
        """与 LLM 后处理分支同签名；纯本地打分，不发任何网络请求。"""
        if self._disabled_reason:
            LOGGER.warning("%s %s 跳过：%s", self._log_tag, name, self._disabled_reason)
            return
        started = time.time()

        if self._observe_only:
            # 观察模式零副作用：不清旧标记也不写新标记（阈值清空重跑不抹历史标记）
            scores = []
            for tran in trans_list:
                dst = getattr(tran, "proofread_zh", "") or getattr(tran, "pre_dst", "") or ""
                score = nr.score_text(dst, self._index, self._lam)
                if score is not None:
                    scores.append(score)
            if scores:
                ordered = sorted(scores)
                LOGGER.info(
                    "%s %s 观察模式：可打分 %d 句，语域分 mean=%.2f p10=%.2f p50=%.2f p90=%.2f"
                    "（配置 gpt.ngramThreshold 后开始标记）",
                    self._log_tag, name, len(scores),
                    sum(scores) / len(scores),
                    ordered[int(len(ordered) * 0.1)],
                    ordered[len(ordered) // 2],
                    ordered[int(len(ordered) * 0.9)],
                )
            else:
                LOGGER.info("%s %s 观察模式：无可打分句子", self._log_tag, name)
            return

        # 清旧标记（重跑不残留），与 BaseDetectRound._prepare_round 的清标记口径一致
        for tran in trans_list:
            tran.ngram_issue = ""

        flagged, stats = nr.find_ngram_flagged(trans_list, self._index, self._threshold, self._lam)
        for tran, score in flagged:
            tran.ngram_issue = f"语域分 {score:.2f} 低于阈值 {self._threshold:.2f}"
        LOGGER.info(
            "%s %s 共 %d 句：可打分 %d（过短跳过 %d），标记语域异常 %d 句（阈值 %.2f，λ %.2f，%.1fs）",
            self._log_tag, name, stats["total"], stats["scored"], stats["skipped_short"],
            stats["flagged"], self._threshold, self._lam, time.time() - started,
        )

    async def shutdown(self) -> None:
        """与 LLM 后端的 shutdown 签名对齐；本地步骤无资源需要释放。"""
        return None
