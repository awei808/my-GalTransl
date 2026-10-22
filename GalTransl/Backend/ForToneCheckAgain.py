"""词语色彩复核后端：对 ForToneCheck 标记的「词语色彩不一致」命中句做二次复核。

第一轮 ForToneCheck 判定为色彩不一致的句子（tone_issue 非空）作为本引擎的输入；
本引擎重新注入该批句子的色彩基准，逐句要求 AI 给出「确认/撤销」结论，仅保留
确认仍明显偏离基准的标记，撤销属于可接受译文（表达普通、轻微语气差异、折中判定）
的误报标记。不产译文、不触碰主译文与备选译文。可独立运行，也可在色彩检查之后按
文件调用。

与第一轮 ForToneCheck 的关键差异（复核轮必须保守，绝不误删真错）：
- 不预清 tone_issue：仅显式 keep:false 才撤销，保留第一轮信号；
- 输出为全量判定（每行 keep），而非稀疏命中，不适用整批回显判定；
- 无批次级元数据时**不再跳过**：与第一轮同源回退到 src 原文语气基准继续复核；
- 仅在基准段意外为空（防御分支）时保留标记并跳过该校批次，避免「无依据撤销」；
- LLM 调用失败 / 判定行缺失 / keep 值异常一律 fail-safe 保留既有标记。

色彩基准与 pass2_cache 批次级元数据为软依赖（区间色彩标注优先，缺失时回退原文
语气），与第一轮同源。
引擎标识：ForToneCheckAgain
"""

from typing import Optional, Tuple

from GalTransl import LOGGER
from GalTransl.Backend.BaseEngine import register_engine
from GalTransl.Backend.ForToneCheck import ForToneCheck
from GalTransl.Backend.Prompts import (
    FORGAL_JSON_FORWORDTONE_AGAIN_PROMPT,
    FORWORDTONE_AGAIN_SYSTEM,
)


@register_engine("ForToneCheckAgain")
class ForToneCheckAgain(ForToneCheck):
    """词语色彩复核后端（命中句二次复核）。

    输入为第一轮 ForToneCheck 已标记 tone_issue 的句子；本引擎把「色彩基准 +
    原文 + 译文」重新发给 AI，并要求对输入每一行显式给出 keep: true/false 结论，
    只保留确认的标记、撤销误报。继承 ForToneCheck 以复用色彩基准渲染
    （_format_tone_guide，含无标注时的原文口径回退）与主 profile 令牌池降级逻辑，
    仅重写复核语义相关部分。

    与主翻译 profile 共用令牌池（与其他后处理后端一致），主池无可用 token 时
    降级跳过，不发任何请求、不清理既有标记。

    引擎标识：ForToneCheckAgain
    """

    _log_tag = "[色彩复核]"
    _prompt_pair = (
        FORWORDTONE_AGAIN_SYSTEM,
        FORGAL_JSON_FORWORDTONE_AGAIN_PROMPT,
    )
    _round_kind = "review"
    _parse_mode = "confirm"
    # 复核轮每行都必须输出 keep 结论，整批回显判定不适用
    _echo_retry_enabled = False
    _extra_problem_attr = "tone_issue"
    _noun = "词语色彩复核"
    _input_label = "色彩复核"
    _context_noun = "复核"
    _tone_mode_verb = "复核"
    _tone_mode_verb_cn = "复核"

    def _prepare_file_context(self, filename: str) -> object:
        """解析色彩基准上下文：返回 (批次元数据, 文件级元数据块)。

        复核轮的 prompt 与第一轮不同，同时注入文件级元数据（[plot_metadata]）与
        区间色彩基准；无批次元数据时与第一轮同源回退到原文语气，不跳过文件。
        """
        # 与批次划分共用同一解析口径（含扁平化名的 basename 兜底）
        batch_metadata, _matched_name = self._resolve_detect_batch_metadata(filename)
        if batch_metadata is None or not getattr(batch_metadata, "batches", None):
            LOGGER.info(
                f"{self._log_tag} {filename} 无批次级元数据（区间色彩标注），"
                f"按原文口径{self._tone_mode_verb}"
            )
        metadata = self._resolve_file_metadata(filename)
        metadata_block = (
            self._format_file_metadata_block(metadata) if metadata is not None else ""
        )
        if metadata_block:
            LOGGER.debug(
                f"{self._log_tag} {filename} 注入文件级元数据作为{self._context_noun}语境"
            )
        return batch_metadata, metadata_block

    def _build_batch_user_content(
        self, batch: list, ctx: object, input_src: str, stats: dict
    ) -> Optional[Tuple[str, str]]:
        """拼接复核轮 user 提示词；基准段意外为空时返回 None（保留标记、跳过本批）。"""
        batch_metadata, metadata_block = ctx
        tone_guide, annotated = self._format_tone_guide(batch, batch_metadata)
        if not tone_guide:
            # 防御分支（正常流程不会为空：无标注时用原文口径回退段）：
            # 无判定依据时保留标记、不发请求，绝不「无依据撤销」。该批不计入
            # 「按原文口径复核」统计（未下发请求）。
            stats["skip_no_guide"] += len(batch)
            return None
        if not annotated:
            stats["fallback_batches"] += 1
        note = f"（基准：{'区间标注' if annotated else '原文口径'}）"
        return (
            self._build_tonecheck_again_user_content(
                input_src, tone_guide, metadata_block
            ),
            note,
        )

    def _build_tonecheck_again_user_content(
        self, input_src: str, tone_guide: str, metadata_block: str = ""
    ) -> str:
        """拼接复核轮 user 提示词：替换 [ToneGuide]/[TargetLang]/[Input]/[plot_metadata]。

        除色彩基准段、文件级元数据、任务说明与批次 input 外，不注入术语表/
        批次元数据/历史结果/翻译规范。固定的任务说明置于最前作为缓存头，动态的
        基准段与剧情元数据依次注入其后。正常流程下 tone_guide 恒非空（无标注时
        用原文口径回退段），空值时的移除分支仅为防御。
        """
        prompt_req = self.trans_prompt
        if not tone_guide:
            prompt_req = prompt_req.replace(
                "<tone_guide>\n[ToneGuide]\n</tone_guide>\n\n", ""
            )
        prompt_req = prompt_req.replace("[plot_metadata]", metadata_block)
        prompt_req = prompt_req.replace("[ToneGuide]", tone_guide)
        prompt_req = prompt_req.replace("[TargetLang]", self.target_lang)
        prompt_req = prompt_req.replace("[Input]", input_src)
        return prompt_req

    def _log_batch_skipped(
        self, filename: str, batch: list, batch_no: int, total_batches: int
    ) -> None:
        """基准段为空时保留标记并跳过本批的 DEBUG 留痕。"""
        LOGGER.debug(
            f"{self._log_tag} {filename} 批次 {batch_no}/{total_batches} "
            f"色彩基准段为空，保留本批 {len(batch)} 句既有标记并跳过"
        )

    def _summary_suffix(self, stats: dict) -> str:
        """汇总附加「基准为空保留」句数与按原文口径批次统计。"""
        skip_note = (
            f"，{stats['skip_no_guide']} 句因色彩基准段为空保留"
            if stats["skip_no_guide"]
            else ""
        )
        return skip_note + self._tone_mode_note(stats, stats["dispatched_batches"])
