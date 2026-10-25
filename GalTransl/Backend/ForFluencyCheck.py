"""中文通顺检测后端：判断译文的中文表达是否符合现代汉语的自然表达。

仅判定、不产译文：命中句由本后端置 fluency_issue 标记（格式
`类别：「原片段」→「建议片段」(依据:…)`，替换建议仅供校对者参考，不写
alt_dst、不碰主译文），随后 find_problems 认领并输出「中文不顺」问题。
作为完整流水线的后处理阶段（gpt.afterTranslation=fluencycheck）使用，
也可作为独立引擎对已翻译文件执行。

两级流水线的第二级：fluency_rules 规则初筛（正则/词表）压缩送检量，只有
初筛命中的句子才发给 LLM 复核——未复核的规则命中不产生任何标记。
与「节奏冗长」（ForVerboseCheck）判据正交：本项不判信息量增减，只判
"读起来不像人写的"。
引擎标识：ForFluencyCheck
"""

from GalTransl import LOGGER
from GalTransl.Backend.BaseDetectRound import _FRAGMENT_RE
from GalTransl.Backend.BaseEngine import register_engine
from GalTransl.Backend.ForSemCheck import ForSemCheck
from GalTransl.Backend.Prompts import (
    FORFLUENCY_SYSTEM,
    FORGAL_JSON_FLUENCYCHECK_PROMPT,
)
from GalTransl.CSentense import CSentense, CTransList
from GalTransl.ConfigHelper import CProjectConfig, CProxyPool
from GalTransl.COpenAI import COpenAITokenPool
from GalTransl.fluency_rules import find_fluency_candidates
from typing import Optional


def _lang_supports_fluency(target_lang: str) -> bool:
    """目标语言是否支持中文通顺检测：仅简体/繁体中文（口径同 BaseTranslate 的
    `"Chinese" in target_lang`——target_lang 取值为 LANG_SUPPORTED 的值，如
    Simplified_Chinese/Traditional_Chinese/English）。"""
    return "Chinese" in str(target_lang or "")


@register_engine("ForFluencyCheck")
class ForFluencyCheck(ForSemCheck):
    """中文通顺检测后端。

    向 AI 发送「原文 + 当前译文」（初筛候选句，h 场景照常检测），AI 仅输出
    中文表达不自然的句子 id 与替换建议（reason），本后端为命中句置
    fluency_issue 标记。问题类型统一由 find_problems 认领为「中文不顺」。

    与 ForSemCheck 同构（单轮、稀疏命中、文件级元数据作为语境、整批回显
    重试），差异：_prepare_round 先做规则初筛；证据校验口径不同——reason 的
    建议片段按定义不在译文中，不能沿用「所有片段都在 dst 中找到」的基类口径。

    引擎标识：ForFluencyCheck
    """

    _log_tag = "[通顺检测]"
    _marker_attr = "fluency_issue"
    _issue_label = "中文不顺"
    _prompt_pair = (FORFLUENCY_SYSTEM, FORGAL_JSON_FLUENCYCHECK_PROMPT)
    # 通顺检测独立批次：优先 gpt.numPerRequestFluencyCheck（默认 20），未配置时
    # 回退冗长检测批次、语义检测批次、改进轮批次，再兜底调用方实参。
    _batch_size_keys = (
        "gpt.numPerRequestFluencyCheck",
        "gpt.numPerRequestVerboseCheck",
        "gpt.numPerRequestSemCheck",
        "gpt.numPerRequestBetter",
    )
    # 旧版固定文案与数字占位视为无原因：证据校验开启时按无片段证据丢弃
    _placeholder_reasons = ("中文不顺", "1")
    _noun = "中文通顺检测"
    _done_noun = "通顺检测"
    _input_label = "通顺检测"
    _no_target_noun = "可检测"
    _first_round_hint = "中文通顺检测（ForFluencyCheck）"

    # 整批回显（复制输入全部标错）重试时追加的纠正提示（覆盖基类文案）
    _echo_retry_hint = (
        "注意：上一轮你的输出疑似把整批输入全部判定为中文不顺（整批回显）。"
        "请重新逐句判断，仅输出确实存在轻动词冗余、判断句冗赘、搭配冲突、"
        "虚词堆叠或名词化过度的句子；若均无问题，输出空代码块。"
    )
    # 回显判定阈值：与 ForSemCheck 同口径（本引擎只做标记不产译文，误杀成本低）
    _echo_hit_ratio = 0.6

    # reason 必须带「原片段→建议片段」证据（提示词已要求类目格式），开启片段
    # 证据校验丢弃无证据命中——口径见下方 _reason_has_evidence 覆写
    _reason_evidence_required = True
    _reason_anomaly_ratio = 0.5
    _reason_retry_hint = (
        "注意：上一轮你输出的命中句多数没有按格式给出可核对的替换建议"
        "（reason 为空，或不是「类别：「原片段」→「建议片段」(依据:…)」格式）。"
        "请重新逐句判断：原片段必须原样摘自本句 dst（2–12 字），建议片段字数"
        "不多于原片段；摘不出片段或给不出更短写法的句子说明它不成立，不要输出。"
        "若整批均无问题，输出空代码块。"
    )

    def __init__(
        self,
        config: CProjectConfig,
        eng_type: str,
        proxy_pool: Optional[CProxyPool] = None,
        token_pool: Optional[COpenAITokenPool] = None,
    ) -> None:
        super().__init__(config, eng_type, proxy_pool, token_pool)
        # 初筛后候选句稀疏：按批次元数据区间分会炸出大量 1-2 句批，恒按固定句数
        # （与复核轮同口径；见 BaseDetectRound._split_into_batches）
        self.batch_from_metadata = False
        # 目标语言非中文时无从谈"中文通顺"，直接禁用（复核轮继承同判）
        if not _lang_supports_fluency(getattr(self, "target_lang", "")):
            if not self._disabled_reason:
                self._disabled_reason = "目标语言非中文，中文通顺检测不适用"

    def _prepare_round(self, filename: str, trans_list: CTransList) -> list:
        """首轮：先走基类口径（清旧标记＋取有效译文），再做规则初筛。

        复核轮（ForFluencyCheckAgain 继承本类）不做初筛：已标记句可能在校对后
        不再命中规则，按规则过滤会漏复核、留死标记。
        """
        targets = super()._prepare_round(filename, trans_list)
        if self._round_kind == "review" or not targets:
            return targets
        candidates, counts = find_fluency_candidates(targets)
        if not candidates:
            LOGGER.info(
                f"{self._log_tag} {filename} 规则初筛无命中"
                f"（{len(targets)} 句过筛），跳过"
            )
            return []
        detail = "、".join(f"{cat}类 {n} 句" for cat, n in counts.items() if n)
        LOGGER.info(
            f"{self._log_tag} {filename} 规则初筛：{len(targets)} 句 → "
            f"{len(candidates)} 句候选（{detail}）"
        )
        return candidates

    def _reason_has_evidence(self, reason_text: str, tran: CSentense) -> bool:
        """覆写证据校验：reason 必须是「类别：「原片段」→「建议片段」(依据:…)」。

        与基类口径的差异：建议片段按定义不在 dst 中，不能要求所有片段都在译文
        中找到。本口径为：恰好两个「…」片段；原片段必须在 dst 中（沿用基类两级
        容错）；建议片段非空、与原片段不同且字数不多于原片段——最后一条同时是
        提示词硬约束的机械兜底。
        """
        if not reason_text:
            return False
        fragments = _FRAGMENT_RE.findall(reason_text)
        if len(fragments) != 2:
            return False
        original, suggestion = (fragment.strip() for fragment in fragments)
        if not original or not suggestion:
            return False
        if suggestion == original or len(suggestion) > len(original):
            return False
        dst = tran.proofread_zh or tran.pre_dst or ""
        return self._fragment_in_dst(original, dst)
