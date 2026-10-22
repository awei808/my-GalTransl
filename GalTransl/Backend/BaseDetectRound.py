"""检测类后端公共基类：单轮判定、不产译文的共享骨架。

检测类后端（语义差异检测 ForSemCheck、词语色彩检查 ForToneCheck、节奏冗长检测
ForVerboseCheck 及各自 Again 复核轮）此前各自实现令牌池降级、单轮批次循环、
命中解析、整批回显重试与预览推送，新增一个后端需抄 300 行左右。本模块把这条
流程收口为一份 batch_translate 模板方法，子类只声明类属性并按需覆盖 5 个钩子。

类属性（子类必填部分）：
- `_log_tag`：日志前缀（同时决定 StandaloneBackendSpec 的展示用词）。
- `_marker_attr` / `_issue_label`：命中写哪个句属性、展示用词。
- `_prompt_pair`：`(系统提示词, 任务模板)`。
- `_batch_size_keys`：批次大小配置链，高优先级在前；末级恒回退调用方实参。
- `_round_kind`：`detect`（首轮，清旧标记后全量判定）或 `review`（复核已标记句）。
- `_parse_mode`：`sparse`（模型只输出命中句）或 `confirm`（每行 keep 结论）；
  复核轮必须同时声明 `_round_kind="review"`、`_parse_mode="confirm"`、
  `_echo_retry_enabled=False`、`_extra_problem_attr=_marker_attr`。
- `_placeholder_reasons` / `_default_marker`：占位 reason 与无原因时的哨兵值。
- `_clear_marker_before_round`：首轮是否先清旧标记（复核轮恒不清）。
- `_noun` / `_done_noun` / `_input_label` / `_no_target_noun` / `_first_round_hint` /
  `_context_noun`：日志措辞，逐字沿用各后端原有文案。

钩子（默认实现覆盖首轮语义，按需覆盖其中 5 个）：
- `_prepare_round(filename, trans_list)`：筛选本轮判定对象（复核轮覆盖点）。
- `_prepare_file_context(filename)`：每文件上下文（文件级/批次级元数据）。
- `_build_batch_user_content(batch, ctx, input_src, stats)`：返回
  `(user_content, 附加日志片段)`；返回 None 表示防御性跳过本批。
- `_log_batch_skipped(...)` / `_summary_suffix(stats)`：跳过留痕与汇总附加片段。

批次划分：首轮默认按批次级元数据区间（`internals.detect.batch_from_metadata`，
单批上限 `internals.detect.batch_from_metadata_max_sentences`），区间外句子按
固定句数切；无元数据、句子无行号或行号与区间不匹配时整体回退 `_batch_size_keys`
固定句数。复核轮恒按固定句数（命中句稀疏，按区间分会炸出大量 1-2 句批）。

异常输出防御（fail-safe，见 `_parse_fix_response` / `_detect_batch_anomaly`）：
- 输出行带 `src`/`dst` 键 → 模型把输入行回显成了判定结果，整行丢弃；
- 开启 `_reason_evidence_required` 的引擎，`reason` 为空或片段在译文中找不到 →
  该条不写标记；本批「无理由命中率」超阈值 → 整批作废并记一次运行态错误。
"""

import os
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

from GalTransl import LOGGER
from GalTransl.COpenAI import COpenAITokenPool
from GalTransl.CSentense import CSentense, CTransList
from GalTransl.ConfigHelper import CProxyPool, CProjectConfig
from GalTransl.Problem import normalize_problem_detail
from GalTransl.Service import JobCancelledError
from GalTransl.Backend.BaseEngine import EmptyTokenPool
from GalTransl.Backend.BaseFixRound import BaseImproveRound
from GalTransl.Backend.utils import (
    coerce_bool,
    decode_json_line_part,
    parse_interval,
    preprocess_jsonline_response,
    strip_chunk_suffix,
)


# reason 片段抽取：提示词口径「形态：「片段1」/「片段2」」。
# 排除「」内的嵌套「：提示词自己的格式说明就是「形态：「A」/「B」」，模型照抄整段时
# 会得到「同义堆叠：「A」，把外壳算进片段必然在译文中找不到 → 整条被误判为无证据。
_FRAGMENT_RE = re.compile(r"「([^「」]*)」")

# 退化日志节流键（(项目目录, 日志前缀)）：实例按文件重建，只能放在模块级
_FALLBACK_LOG_KEYS: set = set()
_FALLBACK_LOG_LOCK = threading.Lock()


class BaseDetectRound(BaseImproveRound):
    """检测/复核轮公共基类：只判定不产译文，命中写句属性标记。"""

    _log_tag = "[检测]"
    # 命中标记的句属性名（suspected_error / tone_issue / verbose_issue）
    _marker_attr = ""
    # 展示用词：预览、日志与 0 命中说明共用（疑似错误 / 色彩不一致 / 节奏冗长）
    _issue_label = ""
    # (系统提示词, 任务模板)
    _prompt_pair: Tuple[str, str] = ("", "")
    # 批次大小配置链：高优先级在前，末级回退 num_pre_request or 20
    _batch_size_keys: Tuple[str, ...] = ("gpt.numPerRequestBetter",)
    # detect=首轮（清旧标记 + 稀疏命中 + 可整批回显重试）；review=复核（保留标记 + keep 结论）
    _round_kind = "detect"
    # sparse=模型只输出命中句；confirm=模型对输入每行输出 keep 结论
    _parse_mode = "sparse"
    # 是否启用整批回显检测与一次重试（复核轮全量输出，不适用）
    _echo_retry_enabled = True
    # 判定词表：命中后写入标记字段的兜底文本
    _default_marker = "1"
    # 视为「无具体原因」的占位 reason（落盘归一为哨兵）
    _placeholder_reasons: Tuple[str, ...] = ()
    # 首轮是否先清空旧标记（复核轮必须保留第一轮信号）
    _clear_marker_before_round = True
    # 复核轮把第一轮原因注入输入行 problem 键所用的句属性名；空串=不注入
    _extra_problem_attr = ""

    # ---- 异常输出防御（见 _parse_fix_response / _detect_batch_anomaly）----
    # 输出行中只要出现这些键，即为模型回显输入行（输入行才有），整行丢弃
    _echo_line_keys: Tuple[str, ...] = ("src", "dst")
    # 命中 reason 是否必须携带可在译文中核对的「…」片段证据。
    # 仅当提示词要求 reason 带片段时才开启（ForVerboseCheck）；未开启的引擎沿用
    # 「无 reason → 写哨兵 _default_marker」旧口径，避免误杀无 reason 的旧缓存流程
    _reason_evidence_required = False
    # 本批「无片段证据命中数 / 模型输出命中行数」超过该比例 → 本批判为格式异常
    _reason_anomaly_ratio = 0.5
    # 判定格式异常的最小规模：低于该值只逐条丢弃，不整批作废（避免小批被一次误判清空）
    _reason_anomaly_min_hits = 3
    # 「过度解释」类片段的最短长度：单个副词/连词/程度词属语气补足，不构成过度解释。
    # 提示词已明令「3 字以内一律不算」，此处是同一口径的机械兜底（模型常仍写「早已」）
    _over_explain_min_fragment = 4
    # 判为格式异常时重试追加的纠正提示；空串=不重试、直接作废本批
    _reason_retry_hint = (
        "注意：上一轮你输出的命中句多数没有给出可核对的具体原因。"
        "请重新逐句判断，仅输出确实有问题的句子，并为每句写明具体原因；"
        "若无问题，输出空代码块。"
    )

    # 日志措辞：逐字对齐各后端原有文案
    _noun = "检测"
    _done_noun = "检测"
    _input_label = "检测"
    _no_target_noun = "检测"
    _first_round_hint = ""
    _context_noun = "检测"

    def __init__(
        self,
        config: CProjectConfig,
        eng_type: str,
        proxy_pool: Optional[CProxyPool] = None,
        token_pool: Optional[COpenAITokenPool] = None,
    ) -> None:
        """初始化检测后端：主 profile 令牌池缺失/不可用时降级跳过，不中断后处理。

        Args:
            config: 项目配置对象。
            eng_type: 引擎标识（如 ForSemCheck）。
            proxy_pool: 代理池对象，为 None 时不使用代理。
            token_pool: 主翻译令牌池；为 None 时按主 profile 自动构建。
        """
        self._disabled_reason = ""
        if token_pool is None:
            # 直接实例化（测试/独立调用）未传池：按主 profile 自建，与其他调用路径一致
            try:
                token_pool = COpenAITokenPool(config, eng_type)
            except Exception as e:
                # 老项目缺 OpenAI-Compatible 段等极端场景：降级禁用，不中断后处理
                LOGGER.warning(
                    f"{self._log_tag} 主翻译令牌池构建失败：{type(e).__name__}: {e}"
                )
                self._disabled_reason = "主翻译令牌池构建失败"
                token_pool = EmptyTokenPool()
        if not getattr(token_pool, "get_available_token", lambda: [])():
            if not self._disabled_reason:
                self._disabled_reason = (
                    "主翻译令牌池无可用 token（请在「API 配置」页配置 OpenAI 兼容端点）"
                )
        super().__init__(config, eng_type, proxy_pool, token_pool)
        # 覆盖基类（翻译轮）的系统提示词为检测轮专用角色声明与任务模板
        self.system_prompt, self.trans_prompt = self._prompt_pair
        # 覆盖默认值后统一重放 change_prompt 与用户模板 override（基类 __init__ 已应用过一次）
        self._finalize_prompts()

        # 首轮检测默认按批次级元数据区间划分批次（internals.detect.batch_from_metadata）；
        # 复核轮恒按固定句数（命中句稀疏，按区间分会炸出大量 1-2 句批）
        self.batch_from_metadata = coerce_bool(
            self.pj_config.getKey("internals.detect.batch_from_metadata", True),
            default=True,
        )
        # 单批句数上限，0=不限制（用 optional 口径：_coerce_positive_int 会把 0 变成 1）
        self.batch_metadata_max_sentences = min(
            512,
            self._coerce_optional_int(
                self.pj_config.getKey(
                    "internals.detect.batch_from_metadata_max_sentences", 0
                ),
                0,
            ),
        )
        LOGGER.debug(
            f"{self._log_tag} 批次划分配置：按批次元数据={self.batch_from_metadata}，"
            f"单批上限={self.batch_metadata_max_sentences or '不限制'}"
        )

    async def batch_translate(
        self,
        filename: str,
        cache_file_path: str,
        trans_list: CTransList,
        num_pre_request: int,
        retry_failed: bool = False,
        gpt_dic=None,
        proofread: bool = False,
        retran_key: str = "",
        translist_hit: Optional[list] = None,
        translist_unhit: Optional[list] = None,
    ) -> CTransList:
        """检测轮入口：禁用/未配置时降级跳过；否则逐批单轮判定（不累积对话上下文）。

        首轮批次默认按批次级元数据区间划分（每批不跨区间），无元数据或行号不可用时
        回退固定句数；复核轮恒按固定句数。标记字段为持久化信号：首轮进入即清旧标记
        再写新结果（幂等），复核轮只按 keep 结论撤销。每批只发送 [system] + 本批
        user，不注入术语表、历史结果、翻译规范与全局分析（上下文由子类钩子按需注入）。
        """
        if self._disabled_reason:
            LOGGER.warning(
                f"{self._log_tag} {filename}：{self._disabled_reason}，跳过{self._noun}"
            )
            return trans_list
        targets = self._prepare_round(filename, trans_list)
        total = len(targets)
        if total == 0:
            return trans_list
        num_per_request = self._resolve_num_per_request(num_pre_request)
        # 首轮默认按批次级元数据区间切批（无元数据/行号不匹配时回退固定句数）
        batches, metadata_grouped = self._split_into_batches(
            filename, targets, num_per_request
        )
        total_batches = len(batches)
        stats: dict = {
            "hit": 0,
            "confirm": 0,
            "dismiss": 0,
            "dropped": 0,
            "total_batches": total_batches,
            "dispatched_batches": 0,
            "fallback_batches": 0,
            "skip_no_guide": 0,
        }
        ctx = self._prepare_file_context(filename)
        self._log_round_start(filename, total, total_batches)
        if self._round_kind == "review":
            self._log_review_reason_injection(filename, targets)
        for batch_no, batch in enumerate(batches, start=1):
            self._check_stop_requested()
            if metadata_grouped:
                lo, hi = self._trans_global_range(batch)
                LOGGER.debug(
                    f"{self._log_tag} {filename} 批次 {batch_no}/{total_batches}"
                    f" 区间[{lo}-{hi}] sentences={len(batch)}"
                )
            idx_tip = self._build_idx_tip(batch)
            _input_list, _sig_list, n_symbol, input_src = self._build_input_jsonlines(
                batch,
                proofread=True,
                filename=filename,
                include_src=True,
                extra_problem_attr=self._extra_problem_attr,
            )
            prepared = self._build_batch_user_content(batch, ctx, input_src, stats)
            if prepared is None:
                self._log_batch_skipped(filename, batch, batch_no, total_batches)
                continue
            user_content, batch_note = prepared
            stats["dispatched_batches"] += 1
            if self.pj_config.active_workers == 1:
                LOGGER.info(
                    f"-> {self._input_label}输入[{batch_no}/{total_batches}] | "
                    f"backend={self.eng_type} | sentences={len(batch)}"
                )
            batch_note_or_none = await self._judge_batch(
                filename, batch, idx_tip, n_symbol, user_content, stats
            )
            if batch_note_or_none is not None:
                LOGGER.debug(
                    f"{self._log_tag} {filename} 批次 {batch_no}/{total_batches}"
                    f"（序号 {idx_tip}）{batch_note_or_none}{batch_note}"
                )
        self._log_summary(filename, stats)
        return trans_list

    async def _judge_batch(
        self,
        filename: str,
        batch: list,
        idx_tip: str,
        n_symbol: str,
        user_content: str,
        stats: dict,
    ) -> Optional[str]:
        """对单批调用 LLM 并应用结果；返回本批结论摘要，None 表示本批无结论。

        本批输出被判为异常时（整批回显 / 无理由命中率过高）丢弃本批标记，并追加
        对应纠正提示重试一次；重试仍异常则整批丢弃并记一次运行态错误
        （fail-safe，不写脏标记）。每批最多重试一次。
        """
        batch_retried = False
        while True:
            call_messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_content},
            ]
            try:
                raw_resp, _token = await self._call_llm(
                    call_messages, filename, idx_tip, None
                )
            except JobCancelledError:
                raise
            except Exception as e:
                LOGGER.warning(
                    f"{self._log_tag}[{filename}:{idx_tip}]LLM调用失败："
                    f"{type(e).__name__}: {e}"
                )
                self._record_round_runtime_error(
                    filename, idx_tip, f"{type(e).__name__}: {e}", None
                )
                return None
            result_text = preprocess_jsonline_response(raw_resp or "")
            if self._parse_mode == "confirm":
                confirm, dismiss = self._parse_confirm_response(result_text, batch)
                stats["confirm"] += confirm
                stats["dismiss"] += dismiss
                return f"确认 {confirm} 句，撤销 {dismiss} 句"
            quality: Dict[str, int] = {}
            hit, _ = self._parse_fix_response(result_text, batch, n_symbol, quality)
            anomaly = self._detect_batch_anomaly(hit, len(batch), quality)
            if self._echo_retry_enabled and anomaly is not None:
                # 异常输出（整批回显 / 无理由命中率过高）：丢弃本批标记，必要时重试一次
                anomaly_kind, anomaly_desc = anomaly
                for t in batch:
                    setattr(t, self._marker_attr, "")
                # 面板上刚推的命中已被丢弃：强制覆盖，否则后续 0 命中说明让位、误导
                self._push_discarded_preview(f"本批判定为{anomaly_desc}，命中已丢弃")
                retry_hint = (
                    self._echo_retry_hint
                    if anomaly_kind == "echo"
                    else self._reason_retry_hint
                )
                if not batch_retried and retry_hint:
                    LOGGER.warning(
                        f"{self._log_tag}[{filename}:{idx_tip}] 本批输出异常："
                        f"{anomaly_desc}，追加纠正提示后重试一次"
                    )
                    user_content = user_content + "\n\n" + retry_hint
                    batch_retried = True
                    continue
                _anomaly_msg = (
                    f"{self._log_tag}[{filename}:{idx_tip}] 本批输出异常且无法恢复："
                    f"{anomaly_desc}，已丢弃本批全部{self._issue_label}标记"
                    f"（{'未配置纠正提示' if not retry_hint else '重试后仍异常'}）"
                )
                LOGGER.warning(_anomaly_msg)
                self._record_round_runtime_error(
                    filename, idx_tip, _anomaly_msg, None
                )
                return None
            stats["hit"] += hit
            stats["dropped"] += int(quality.get("no_reason", 0) or 0)
            return f"命中 {hit} 句"

    def _detect_batch_anomaly(
        self, hit: int, batch_size: int, quality: Dict[str, int]
    ) -> Optional[Tuple[str, str]]:
        """判定本批模型输出是否异常，返回 (异常类别, 描述)，正常时返回 None。

        三类异常：
        - `echo` 回显输入行：`quality["echo_lines"]`（带 src/dst 的输出行）达到
          `_is_echo_response` 门槛——模型把推理正文里的输入行整片回显成了结果；
        - `echo` 整批回显：命中率 ≥ `_echo_hit_ratio` 且命中数 ≥ `_echo_min_hits`；
        - `reason`：「无片段证据命中数 / 模型输出命中行数」> `_reason_anomaly_ratio`
          且规模 ≥ `_reason_anomaly_min_hits`——模型把推理过程或空 reason 当成了
          判定结果，这类命中在前端显示为「没有原因的节奏冗长」，必须整批作废。

        第一类的行已在解析时丢弃（不写标记），故必须在此单独判一次：否则整批
        回显输入行会退化成「0 命中、不重试、不留痕」，把检测失败显示成检测通过。
        """
        echo_lines = int(quality.get("echo_lines", 0) or 0)
        if self._is_echo_response(echo_lines, batch_size):
            return ("echo", f"整批回显输入行（{echo_lines}/{batch_size} 行带 src/dst）")
        if self._is_echo_response(hit, batch_size):
            return ("echo", f"整批回显（命中 {hit}/{batch_size} 句）")
        no_reason = int(quality.get("no_reason", 0) or 0)
        raw_hit = hit + no_reason
        if raw_hit < self._reason_anomaly_min_hits or raw_hit <= 0:
            return None
        if no_reason / raw_hit > self._reason_anomaly_ratio:
            return (
                "reason",
                f"无理由命中率过高（{no_reason}/{raw_hit} 句缺少可核对的原因片段）",
            )
        return None

    def _reason_has_evidence(self, reason_text: str, tran: CSentense) -> bool:
        """判定命中 reason 是否携带可在译文中核对的片段证据。

        要求：reason 非空、至少含一个「…」片段，且每个片段都能在当前译文中找到。
        无片段证据的命中不写标记——前端会显示成「无头的原因」，无法人工复核。

        另外按提示词口径卡一道幅度门槛：「过度解释」的片段必须 ≥
        `_over_explain_min_fragment` 字。单个副词/连词（「早已」「慢慢」）删掉确实
        不损信息，但那是中文自然表达，提示词已明令不算；模型仍常这么写，需机械兜底。
        """
        if not reason_text:
            return False
        fragments = _FRAGMENT_RE.findall(reason_text)
        if not fragments:
            return False
        if reason_text.startswith("过度解释") and any(
            len(fragment.strip()) < self._over_explain_min_fragment
            for fragment in fragments
        ):
            return False
        dst = tran.proofread_zh or tran.pre_dst or ""
        return all(self._fragment_in_dst(fragment, dst) for fragment in fragments)

    @staticmethod
    def _fragment_in_dst(fragment: str, dst: str) -> bool:
        """片段能否在译文中找到：原样命中即通过，否则两级容错后再比。

        两级容错对应提示词编码与标点漂移，都不放宽「片段必须来自译文」这条硬约束：
        - 换行/制表符在提示词里编成 <br>/[t]，比对前还原；
        - 半角/全角逗号不参与比对：模型常把 reason 的分隔符写进片段
          （「的话呢,也就是说」），而译文中对应位置没有逗号，否则整条会被误判为编造。
        """
        text = fragment.strip()
        if not text:
            return False
        if text in dst:
            return True
        restored = text.replace("<br>", "\n").replace("[t]", "\t")
        if restored in dst:
            return True
        stripped = restored.replace(",", "").replace("，", "")
        if not stripped:
            return False
        return stripped in dst.replace(",", "").replace("，", "")

    def _prepare_round(self, filename: str, trans_list: CTransList) -> list:
        """筛选本轮判定对象：首轮清旧标记后取有效译文；复核轮只取已标记的命中句。"""
        if self._round_kind == "review":
            targets = [
                t
                for t in trans_list
                if getattr(t, self._marker_attr, "") != "" and t.pre_dst != ""
            ]
            if not targets:
                self._log_no_review_target(filename, trans_list)
                return targets
            return targets
        if self._clear_marker_before_round:
            for t in trans_list:
                setattr(t, self._marker_attr, "")
        targets = self._filter_target_translations(trans_list)
        if not targets:
            LOGGER.info(
                f"{self._log_tag} {filename} 无{self._no_target_noun}的有效译文，跳过"
            )
        return targets

    def _log_no_review_target(self, filename: str, trans_list: CTransList) -> None:
        """复核轮无待复核句：区分「标记存在但译文失效」与「压根没跑过第一轮」。"""
        if any(getattr(t, self._marker_attr, "") != "" for t in trans_list):
            LOGGER.info(
                f"{self._log_tag} {filename} 存在{self._issue_label}标记但均无有效译文，"
                f"跳过（请先确认译文已生成）"
            )
            return
        LOGGER.info(
            f"{self._log_tag} {filename} 无待复核的命中句（{self._marker_attr} 为空），"
            f"跳过；若需复核请先执行{self._first_round_hint}产生标记"
        )

    def _log_review_reason_injection(self, filename: str, targets: list) -> None:
        """复核轮统计第一轮具体原因注入句数（哨兵视为无原因）。"""
        if not self._extra_problem_attr:
            return
        reason_count = sum(
            1 for t in targets if self._issue_reason_of(t, self._extra_problem_attr)
        )
        if reason_count:
            LOGGER.debug(
                f"{self._log_tag} {filename} {reason_count}/{len(targets)} 句将注入"
                f"第一轮原因（problem 字段）"
            )

    def _resolve_num_per_request(self, num_pre_request: int) -> int:
        """按配置链解析每批句数：自低到高逐层套用，与逐层嵌套回退等价。"""
        value = num_pre_request or 20
        for key in reversed(self._batch_size_keys):
            value = self._coerce_positive_int(self.pj_config.getKey(key), value)
        return value

    # 批次划分（首轮按批次级元数据区间；复核轮与退化场景按固定句数）
    @staticmethod
    def _has_batches(batch_metadata: Any) -> bool:
        """严格判定批次元数据可用：必须是带非空 list 的条目（防替身/半成品命中）。"""
        batches = getattr(batch_metadata, "batches", None)
        return isinstance(batches, list) and bool(batches)

    @staticmethod
    def _global_index_of(tran: Any) -> Optional[int]:
        """取句子全局行号（与批次元数据区间同源），缺失时回退文件内序号。"""
        gi = getattr(tran, "runtime_index", None)
        if not isinstance(gi, int):
            gi = getattr(tran, "index", None)
        return gi if isinstance(gi, int) else None

    def _resolve_detect_batch_metadata(self, filename: str) -> Tuple[Optional[Any], str]:
        """解析检测链路的批次元数据，返回 (元数据, 实际命中的文件名)。

        候选顺序：原文件名 → basename → 还原 -} 分隔符后的 basename，每个候选再
        试剥 _N 分批后缀。阶段 8 与独立引擎传入的是扁平化路径名，而 pass2 产物
        按 basename 存（子目录同名文件仍按写入侧口径取其一，DEBUG 留命中来源）。
        批次划分与首轮色彩语境共用本解析口径。
        """
        # 1) 基类口径（原文件名，含剥 _N 分批后缀）；mock 替身也走这条
        try:
            batch_metadata = self._resolve_batch_metadata(filename)
        except Exception as e:
            LOGGER.debug(f"{self._log_tag} {filename} 解析批次级元数据失败：{e}")
            batch_metadata = None
        if self._has_batches(batch_metadata):
            return batch_metadata, filename

        # 2) 子目录/扁平化名兜底：需要显式注入表才能登记命中结果
        if not isinstance(getattr(self, "batch_metadata_map", None), dict):
            return None, filename
        if getattr(self, "_batch_metadata_loaded", True) is False:
            try:
                self._ensure_batch_metadata_loaded()
            except Exception as e:
                LOGGER.debug(f"{self._log_tag} {filename} 载入批次级元数据失败：{e}")
        explicit = self.batch_metadata_map
        loaded = getattr(self, "_batch_metadata_by_file", None)

        candidates = [filename, os.path.basename(filename)]
        restored = os.path.basename(filename.replace("-}", os.sep))
        if restored:
            candidates.append(restored)
        names: List[str] = []
        for candidate in candidates:
            for name in (candidate, strip_chunk_suffix(candidate) if candidate else ""):
                if name and name not in names:
                    names.append(name)
        for name in names:
            batch_metadata = explicit.get(name)
            if batch_metadata is None and isinstance(loaded, dict):
                batch_metadata = loaded.get(name)
            if not self._has_batches(batch_metadata):
                continue
            if name != filename:
                # 兜底命中登记为显式注入：本轮分组与子类钩子统一按原文件名解析
                explicit.setdefault(filename, batch_metadata)
                LOGGER.debug(
                    f"{self._log_tag} {filename} 批次级元数据按「{name}」命中"
                    f"（文件名口径兜底）"
                )
            return batch_metadata, name
        return None, filename

    def _outside_segments(self, group: list, seg_ranges: list) -> bool:
        """组内是否存在不属于任何区间的句子（含无行号句）。"""
        for tran in group:
            gi = self._global_index_of(tran)
            if gi is None:
                return True
            if not any(lo <= gi <= hi for lo, hi in seg_ranges):
                return True
        return False

    @staticmethod
    def _chunk_by(group: list, step: int) -> List[list]:
        """按固定步长切片（step<=0 表示不限制，整组一批）。"""
        if step <= 0 or len(group) <= step:
            return [group]
        return [group[i : i + step] for i in range(0, len(group), step)]

    def _cap_sentences(self, group: list) -> List[list]:
        """按配置的单批句数上限切分（0=不限制，整组一批）。"""
        return self._chunk_by(group, getattr(self, "batch_metadata_max_sentences", 0))

    def _log_no_metadata_fallback(
        self, filename: str, num_per_request: int, reason: str = "no_metadata"
    ) -> None:
        """无可用批次元数据的退化日志：同项目同后端同原因只提示一次，其余 DEBUG。

        阶段 8 每文件每后端都新建实例，故节流标记不能挂在实例上。
        """
        cause = (
            "批次级元数据无有效区间"
            if reason == "no_segment"
            else "无可用批次级元数据"
        )
        message = (
            f"{self._log_tag} {filename} {cause}，"
            f"按固定 {num_per_request} 句/批"
        )
        try:
            config = getattr(self, "pj_config", None)
            project_dir = (config.getProjectDir() if config is not None else "") or ""
        except Exception as e:
            LOGGER.debug(f"{self._log_tag} 取项目目录失败，退化日志按实例节流：{e}")
            project_dir = ""
        # 取不到项目目录时按配置对象隔离，避免长驻进程内跨项目互相节流
        scope = (
            str(project_dir)
            if project_dir
            else f"obj:{id(getattr(self, 'pj_config', self))}"
        )
        key = (scope, self._log_tag, reason)
        with _FALLBACK_LOG_LOCK:
            first = key not in _FALLBACK_LOG_KEYS
            _FALLBACK_LOG_KEYS.add(key)
        if not first:
            LOGGER.debug(message)
            return
        LOGGER.info(message + "（同一项目后续文件同此退化，记 DEBUG）")

    def _split_into_batches(
        self, filename: str, targets: list, num_per_request: int
    ) -> Tuple[List[list], bool]:
        """把本轮目标句切成批次，返回 (批列表, 是否按元数据划分)。

        退化回固定句数切片的情形：开关关闭 / 复核轮 / 无可用元数据 /
        句子均无有效行号 / 行号与所有区间均不匹配（后者若照搬分组会退化成
        整文件一批，故必须显式兜回）。
        """
        fixed = [
            targets[i : i + num_per_request]
            for i in range(0, len(targets), num_per_request)
        ]
        if not getattr(self, "batch_from_metadata", True) or self._round_kind != "detect":
            return fixed, False
        batch_metadata, _matched_name = self._resolve_detect_batch_metadata(filename)
        if batch_metadata is None:
            self._log_no_metadata_fallback(filename, num_per_request)
            return fixed, False
        seg_ranges = []
        for item in batch_metadata.batches:
            if not isinstance(item, dict):
                continue
            rng = parse_interval(item.get("区间") or item.get("interval"))
            if rng is not None:
                seg_ranges.append(rng)
        if not seg_ranges:
            self._log_no_metadata_fallback(filename, num_per_request, "no_segment")
            return fixed, False
        indexes = [self._global_index_of(t) for t in targets]
        if all(gi is None for gi in indexes):
            LOGGER.warning(
                f"{self._log_tag} {filename} 存在批次级元数据但句子均无行号"
                f"（runtime_index/index），按固定 {num_per_request} 句/批"
            )
            return fixed, False
        in_range = sum(
            1
            for gi in indexes
            if gi is not None and any(lo <= gi <= hi for lo, hi in seg_ranges)
        )
        if in_range == 0:
            LOGGER.warning(
                f"{self._log_tag} {filename} 句子行号与批次级元数据区间均不匹配"
                f"（{len(targets)} 句均未落入任一区间），按固定 {num_per_request} 句/批"
            )
            return fixed, False
        # 复用翻译轮的区间分组（兜底命中已登记为显式注入，此处按原文件名即可解析到）
        try:
            groups = self._group_by_batch_metadata(targets, filename)
        except AttributeError as e:
            # 半构造实例（无 batch_metadata_map）：退化固定句数，不中断本轮检测
            LOGGER.debug(f"{self._log_tag} {filename} 按元数据分组不可用：{e}")
            return fixed, False
        batches: List[list] = []
        for group in groups:
            if not group:
                continue
            if self._outside_segments(group, seg_ranges):
                # 区间外句子没有语义边界：按固定句数切；设了单批上限时取两者较小值
                cap = getattr(self, "batch_metadata_max_sentences", 0)
                step = min(num_per_request, cap) if cap > 0 else num_per_request
                batches.extend(self._chunk_by(group, step))
                continue
            batches.extend(self._cap_sentences(group))
        if not batches:
            return fixed, False
        LOGGER.info(
            f"{self._log_tag} {filename} 按批次级元数据划分批次："
            f"{len(seg_ranges)} 个区间 → {len(batches)} 批（{len(targets)} 句）"
        )
        return batches, True

    def _prepare_file_context(self, filename: str) -> Any:
        """每文件上下文钩子：默认无（子类按需解析元数据）。"""
        return None

    def _build_batch_user_content(
        self, batch: list, ctx: Any, input_src: str, stats: dict
    ) -> Optional[Tuple[str, str]]:
        """构建本批 user 内容：返回 (内容, 附加日志片段)，None 表示跳过本批。"""
        raise NotImplementedError

    def _log_batch_skipped(
        self, filename: str, batch: list, batch_no: int, total_batches: int
    ) -> None:
        """防御性跳过本批时的日志钩子（默认无输出）。"""

    def _log_round_start(self, filename: str, total: int, total_batches: int) -> None:
        """轮次开始日志：首轮/复核措辞不同，逐字沿用原有文案。"""
        if self._round_kind == "review":
            LOGGER.info(
                f"{self._log_tag} {filename} 开始二次复核，共 {total} 句命中句，"
                f"{total_batches} 批"
            )
            return
        LOGGER.info(
            f"{self._log_tag} {filename} 开始单轮{self._noun}，共 {total} 句，"
            f"{total_batches} 批"
        )

    def _summary_suffix(self, stats: dict) -> str:
        """汇总日志附加片段钩子（基准口径统计等），默认空。"""
        return ""

    def _log_summary(self, filename: str, stats: dict) -> None:
        """轮次结束日志：复核轮给确认/撤销数，首轮给命中数。"""
        suffix = self._summary_suffix(stats)
        if self._round_kind == "review":
            LOGGER.info(
                f"{self._log_tag} {filename} 二次复核完成：确认 {stats['confirm']} 句，"
                f"撤销 {stats['dismiss']} 句{suffix}"
            )
            return
        # 逐条丢弃（reason 无片段证据）只在 DEBUG 记明细，此处汇总一条 INFO：
        # 否则「命中数莫名变少」无从排查，用户只会以为模型判定变严了
        dropped = int(stats.get("dropped", 0) or 0)
        dropped_note = f"（另有 {dropped} 句因原因缺少可核对片段被丢弃）" if dropped else ""
        if stats["hit"] > 0:
            LOGGER.info(
                f"{self._log_tag} {filename} {self._done_noun}完成，"
                f"共命中 {stats['hit']} 句{dropped_note}{suffix}"
            )
            return
        LOGGER.info(
            f"{self._log_tag} {filename} {self._done_noun}完成，"
            f"未发现{self._issue_label}{dropped_note}{suffix}"
        )

    def _parse_fix_response(
        self,
        result_text: str,
        trans_list: CTransList,
        n_symbol: str,
        quality: Optional[Dict[str, int]] = None,
    ) -> Tuple[int, int]:
        """按 id 稀疏解析判定结果（仅取 id 与可选 reason），命中句置标记。

        返回 (hit_count, hit_count) 使基类进度计数一致。

        两道 fail-safe，都是为了拦住「模型推理过程被当成判定结果」：
        - 输出行带 `_echo_line_keys`（src/dst，输入行才有的键）→ 整行丢弃；
        - `_reason_evidence_required` 时，reason 为空或「」片段在译文中找不到 →
          该条不写标记，只计入质量统计。

        quality 传入时回填本批质量计数（`echo_lines` / `no_reason`），供
        `_detect_batch_anomaly` 判定本批是否整体作废。
        """
        id_map = {t.index: t for t in trans_list}
        hit_count = 0
        hits = []
        for line in result_text.splitlines():
            line = line.strip()
            if not line:
                continue
            if "|" in line:
                json_part = line.split("|", 1)[1].strip()
            else:
                json_part = line
            obj = decode_json_line_part(json_part)
            if obj is None:
                continue
            line_id = obj.get("id")
            if not isinstance(line_id, int):
                continue
            if any(key in obj for key in self._echo_line_keys):
                # 模型回显输入行（推理正文中逐句抄输入时整片出现）：不是判定结果
                if quality is not None:
                    quality["echo_lines"] = quality.get("echo_lines", 0) + 1
                continue
            tran = id_map.get(line_id)
            if tran is None:
                continue
            reason = obj.get("reason")
            if isinstance(reason, str) and "\ufffd" in reason:
                # 乱码 reason 视为输出异常：降级为默认标记，避免污染标记字段
                reason = ""
            reason_text = str(reason).strip() if isinstance(reason, str) else ""
            if reason_text in self._placeholder_reasons:
                reason_text = ""
            if self._reason_evidence_required and not self._reason_has_evidence(
                reason_text, tran
            ):
                # 无片段证据：写哨兵只会产生「没有原因的节奏冗长」，丢弃本条
                if quality is not None:
                    quality["no_reason"] = quality.get("no_reason", 0) + 1
                LOGGER.debug(
                    f"{self._log_tag} 句子 {line_id} 的判定原因缺少可核对片段"
                    f"（reason={reason_text!r}），已丢弃"
                )
                continue
            setattr(
                tran,
                self._marker_attr,
                normalize_problem_detail(reason_text) if reason_text else self._default_marker,
            )
            hit_count += 1
            hits.append(tran)
            LOGGER.debug(
                f"{self._log_tag} 句子 {line_id} 判定{self._issue_label}"
                f"（reason={getattr(tran, self._marker_attr)}）"
            )
        if hits:
            # 实时推送结果预览（前端翻译控制台"结果预览"，仅判定不产译文，展示命中标记与原因）
            self.push_result_preview(
                self.pj_config,
                self._format_result_preview(hits, self._marker_attr, self._issue_label),
            )
        else:
            # 0 命中是有效结论，也推一条，避免面板停留在上一批的陈旧内容
            note = f"本批未判定出{self._issue_label}句"
            if quality and quality.get("no_reason"):
                note = (
                    f"本批 {quality['no_reason']} 句命中缺少可核对的原因片段，已丢弃"
                )
            self._push_no_hit_preview(note)
        return hit_count, hit_count

    def _parse_confirm_response(
        self, result_text: str, trans_list: CTransList
    ) -> Tuple[int, int]:
        """解析复核轮全量判定（keep: true/false），应用确认/撤销。

        返回 (confirm_count, dismiss_count)：
        - keep: true：确认标记成立，保留（提供干净新 reason 时覆盖）；
        - keep: false：撤销标记（清空）；
        - 判定行缺失 / keep 值异常 / 乱码 reason：fail-safe 保留既有标记并告警。
        """
        id_map = {t.index: t for t in trans_list}
        confirm_count = 0
        dismiss_count = 0
        confirmed = []
        dismissed = []
        missing = set(id_map)
        for line in result_text.splitlines():
            line = line.strip()
            if not line:
                continue
            if "|" in line:
                json_part = line.split("|", 1)[1].strip()
            else:
                json_part = line
            obj = decode_json_line_part(json_part)
            if obj is None:
                continue
            line_id = obj.get("id")
            if not isinstance(line_id, int):
                continue
            if "keep" not in obj and any(key in obj for key in self._echo_line_keys):
                # 模型回显输入行：不是复核结论（keep 缺失会落到下面的异常分支误报）。
                # 行内带合法 keep 时按结论处理，避免回显行里夹着的撤销被一起丢掉
                continue
            missing.discard(line_id)
            tran = id_map.get(line_id)
            if tran is None:
                continue
            keep = obj.get("keep")
            if keep is True:
                reason = obj.get("reason")
                reason_text = reason.strip() if isinstance(reason, str) else ""
                # 占位 reason（如模型照抄类型名）与乱码一律视为无新原因，保留原标记
                if (
                    self._reason_evidence_required
                    and reason_text
                    and not self._reason_has_evidence(reason_text, tran)
                ):
                    # 证据对不上：不覆盖第一轮原因，保留既有标记
                    LOGGER.debug(
                        f"{self._log_tag} 句子 {line_id} 的复核原因缺少可核对片段"
                        f"（reason={reason_text!r}），保留第一轮原因"
                    )
                    reason_text = ""
                if (
                    reason_text
                    and "\ufffd" not in reason_text
                    and reason_text not in self._placeholder_reasons
                ):
                    setattr(
                        tran, self._marker_attr, normalize_problem_detail(reason_text)
                    )
                confirm_count += 1
                confirmed.append(tran)
                LOGGER.debug(
                    f"{self._log_tag} 句子 {line_id} 复核确认"
                    f"（reason={getattr(tran, self._marker_attr)}）"
                )
            elif keep is False:
                setattr(tran, self._marker_attr, "")
                dismiss_count += 1
                dismissed.append(tran)
                LOGGER.debug(f"{self._log_tag} 句子 {line_id} 复核撤销（可接受译文）")
            else:
                LOGGER.warning(
                    f"{self._log_tag} 句子 {line_id} 的 keep 值异常（{keep!r}），按保留处理"
                )
        if confirmed or dismissed:
            # 实时推送结果预览（前端翻译控制台"结果预览"，展示确认保留与已撤销的命中句）
            self.push_result_preview(
                self.pj_config,
                self._format_result_preview(
                    confirmed, self._marker_attr, self._issue_label, dismissed
                ),
            )
        else:
            # 0 结论也要推送，避免面板停留在上一批的陈旧内容
            self._push_no_hit_preview("本批复核无确认或撤销的命中句")
        if missing:
            LOGGER.warning(
                f"{self._log_tag} 本批 {len(missing)} 句未获得复核判定"
                f"（模型输出缺失），按保留处理"
            )
        return confirm_count, dismiss_count

    def _apply_better_result(
        self, tran: CSentense, current_dst: str, normalized: str, line_id: int
    ) -> bool:
        """防御性死代码：检测轮重写 batch_translate 与解析链，基类调用链不会触达
        本方法；保留以声明「绝不写 alt_dst / 主译文」的契约。"""
        return False
