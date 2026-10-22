"""词语色彩一致性检查后端：判断译文用词色彩是否偏离色彩基准。

仅判定、不产译文：命中句由本后端置 tone_issue 标记（AI 判定说明或固定 "1"），
随后 find_problems 认领并输出「词语色彩不一致」问题（problem 字段），不触碰
主译文与备选译文。

色彩基准为**软依赖**（与批次元数据解绑）：有 pass2_cache 批次级元数据且相交区间带
「用词色彩」标注时以标注（含视角/氛围）为基准；无批次元数据、区间不相交或相交区间
色彩字段全空时回退为「以 src 原文的语气/场景为基准」，不再跳过该文件。
作为完整流水线的后处理阶段（gpt.afterTranslation=tonecheck）使用，也可作为
独立引擎对已翻译文件执行。
引擎标识：ForToneCheck
"""

from typing import Tuple

from GalTransl import LOGGER
from GalTransl.Backend.BaseEngine import register_engine
from GalTransl.Backend.ForSemCheck import ForSemCheck
from GalTransl.Backend.Prompts import (
    FORGAL_JSON_FORWORDTONE_PROMPT,
    FORWORDTONE_GUIDE_FALLBACK,
    FORWORDTONE_SYSTEM,
)


@register_engine("ForToneCheck")
class ForToneCheck(ForSemCheck):
    """词语色彩一致性检查后端。

    向 AI 发送「色彩基准 + 原文 + 当前译文」（h 场景照常检查），AI 仅输出
    用词色彩与基准明显不一致的句子 id（可选 reason），本后端为命中句置
    tone_issue 标记。基准优先取区间的用词色彩标注，无可用标注时回退到该句
    原文语气（软依赖，见模块 docstring）。问题类型统一由 find_problems
    认领为「词语色彩不一致」。

    与主翻译 profile 共用令牌池（与其他后处理后端一致），本引擎额外支持未传
    token_pool 时按主 profile 自建，主池无可用 token 时降级跳过，不发任何请求。

    引擎标识：ForToneCheck
    """

    _log_tag = "[色彩检查]"
    _marker_attr = "tone_issue"
    _issue_label = "色彩不一致"
    _prompt_pair = (FORWORDTONE_SYSTEM, FORGAL_JSON_FORWORDTONE_PROMPT)
    # 色彩检查独立批次：优先 gpt.numPerRequestToneCheck（默认20），未配置时回退
    # 语义检测批次、改进轮批次，再兜底调用方实参。本地小模型批次不宜过大。
    _batch_size_keys = (
        "gpt.numPerRequestToneCheck",
        "gpt.numPerRequestSemCheck",
        "gpt.numPerRequestBetter",
    )
    _noun = "词语色彩一致性检查"
    _done_noun = "色彩检查"
    _input_label = "色彩检查"
    _no_target_noun = "可检查"
    _first_round_hint = "词语色彩检查（ForToneCheck）"
    # 日志用词：首轮「判定用词色彩」/复核轮「复核」（复核轮子类覆盖）
    _tone_mode_verb = "判定用词色彩"
    _tone_mode_verb_cn = "判定"

    # 整批回显（复制输入全部标错）重试时追加的纠正提示（覆盖基类文案）
    # 措辞对两种基准模式都成立：有标注时"色彩基准"即区间色彩标注，无标注时为 src 原文语气
    _echo_retry_hint = (
        "注意：上一轮你的输出疑似把整批输入全部判定为色彩不一致（整批回显）。"
        "请重新逐句对照色彩基准（区间色彩标注，或其原文语气）与 dst，仅输出明显不一致的句子；"
        "若均无明显不一致，输出空代码块。"
    )
    # 回显判定阈值：与 ForSemCheck 同口径（本引擎只做标记不产译文，误杀成本低）
    _echo_hit_ratio = 0.6

    def _prepare_file_context(self, filename: str) -> object:
        """解析批次级元数据（区间色彩标注），无可用值时为软回退，不跳过文件。"""
        # 与批次划分共用同一解析口径（含扁平化名的 basename 兜底）
        batch_metadata, _matched_name = self._resolve_detect_batch_metadata(filename)
        if batch_metadata is None or not getattr(batch_metadata, "batches", None):
            # 放在「有无可检查译文」之后：避免无译文的文件先报基准回退再报跳过
            LOGGER.info(
                f"{self._log_tag} {filename} 无批次级元数据（区间色彩标注），"
                f"按原文口径{self._tone_mode_verb}"
            )
        return batch_metadata

    def _build_batch_user_content(
        self, batch: list, ctx: object, input_src: str, stats: dict
    ) -> Tuple[str, str]:
        """渲染本批色彩基准段并拼接 user 提示词，返回 (内容, 批次日志片段)。"""
        tone_guide, annotated = self._format_tone_guide(batch, ctx)
        if not annotated:
            stats["fallback_batches"] += 1
        note = f"（基准：{'区间标注' if annotated else '原文口径'}）"
        return self._build_tonecheck_user_content(input_src, tone_guide), note

    def _build_tonecheck_user_content(self, input_src: str, tone_guide: str) -> str:
        """单轮拼接色彩检查 user 提示词：替换 [ToneGuide]/[TargetLang]/[Input] 占位符。

        除色彩基准段与任务说明外，不注入术语表/文件级元数据/历史结果/翻译规范。
        固定的任务说明置于最前作为缓存头，每批动态的基准段经 [ToneGuide] 占位符
        注入到任务说明之后（保证 API 前缀缓存可跨文件/批次命中）。
        正常流程下 tone_guide 恒非空（无标注时用回退段）——空值时的移除分支仅为防御。
        """
        prompt_req = self.trans_prompt
        if not tone_guide:
            prompt_req = prompt_req.replace(
                "<tone_guide>\n[ToneGuide]\n</tone_guide>\n\n", ""
            )
        prompt_req = prompt_req.replace("[ToneGuide]", tone_guide)
        prompt_req = prompt_req.replace("[TargetLang]", self.target_lang)
        prompt_req = prompt_req.replace("[Input]", input_src)
        return prompt_req

    def _summary_suffix(self, stats: dict) -> str:
        """汇总附加「按原文口径」批次统计（区间无色彩标注的批次）。"""
        return self._tone_mode_note(stats, stats["total_batches"])

    def _tone_mode_note(self, stats: dict, total_batches: int) -> str:
        """按原文口径批次的统计片段：全量回退与部分回退措辞不同。"""
        fallback = stats["fallback_batches"]
        if not fallback:
            return ""
        if fallback >= total_batches:
            return f"，全部批次按原文口径{self._tone_mode_verb_cn}"
        return f"，其中 {fallback}/{total_batches} 批按原文口径{self._tone_mode_verb_cn}"

    def _format_tone_guide(self, batch: list, batch_metadata) -> Tuple[str, bool]:
        """渲染本批句子的色彩基准段（经 [ToneGuide] 注入），返回 (文本, 是否用区间标注)。

        基准优先级（与批次元数据解绑）：
        1. 有批次元数据且相交区间中至少一条「用词色彩」非空 → 渲染区间色彩标注段
           （文本与解绑前逐字一致，段落口径与翻译轮区间一致）；
        2. 否则（无批次元数据 / 区间不相交 / 相交区间色彩字段全空 / 句子无有效行号）
           → 返回 Prompts.FORWORDTONE_GUIDE_FALLBACK（以 src 原文语气为基准）。

        模板的 `<tone_guide>` 段恒被填充，调用方（含复核轮）可据返回的模式位决定
        是否计入「按原文口径」统计；回退原因记 DEBUG。
        """
        lo = None
        hi = None
        for t in batch:
            gi = getattr(t, "runtime_index", None)
            if not isinstance(gi, int):
                gi = getattr(t, "index", None)
            if not isinstance(gi, int):
                continue
            lo = gi if lo is None else min(lo, gi)
            hi = gi if hi is None else max(hi, gi)
        segments = []
        if lo is None:
            reason = "本批句子无有效行号"
        elif batch_metadata is None:
            reason = "无批次级元数据"
        else:
            segments = batch_metadata.segments_in_range(lo, hi) or []
            reason = f"本批区间 [{lo}-{hi}] 无相交色彩标注"
        lines = []
        has_annotation = False
        dropped = 0
        for b in segments:
            if not isinstance(b, dict):
                dropped += 1
                continue
            rng = b.get("区间") or b.get("interval")
            if not isinstance(rng, (list, tuple)) or len(rng) < 2:
                dropped += 1
                continue
            raw_tone = str(b.get("用词色彩", "") or "").strip()
            if raw_tone:
                has_annotation = True
            view = str(b.get("视角", "") or "").strip() or "未标注"
            atmos = str(b.get("氛围", "") or "").strip() or "未标注"
            lines.append(
                f"- 区间[{rng[0]}-{rng[1]}] 视角:{view} 氛围:{atmos} "
                f"用词色彩:{raw_tone or '未标注'}"
            )
        if not lines or not has_annotation:
            # 无可用区间色彩标注：回退到原文口径（不再移除整段，模板结构不变）
            if segments and lines and not has_annotation:
                reason = f"本批区间 [{lo}-{hi}] 相交区间均无「用词色彩」标注"
            elif segments and not lines:
                reason = f"本批区间 [{lo}-{hi}] 的 {dropped} 条相交标注结构非法（已丢弃）"
            LOGGER.debug(
                f"{self._log_tag} {reason}，已回退为原文口径色彩基准"
            )
            return FORWORDTONE_GUIDE_FALLBACK, False
        return (
            "本文件已按剧情划分为若干翻译区间，本批句子涉及的区间色彩标注如下"
            "（行号为文件内全局行号）：\n" + "\n".join(lines),
            True,
        )
