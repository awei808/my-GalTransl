"""中文通顺复核后端：对 ForFluencyCheck 标记的「中文不顺」命中句做二次复核。

第一轮 ForFluencyCheck 判定为中文不通顺的句子（fluency_issue 非空）作为本
引擎的输入；本引擎逐句要求 AI 给出「确认/撤销」结论，仅保留替换建议经核实
成立的标记，撤销属可接受译文（强调句、固定搭配、角色口癖、h 场景处理本身、
纯风格差异）的误报标记。不产译文、不触碰主译文与备选译文。可独立运行，也可
在通顺检测之后按文件调用。

与第一轮 ForFluencyCheck 的关键差异（复核轮必须保守，绝不误删真错）：
- 不预清 fluency_issue：仅显式 keep:false 才撤销，保留第一轮信号；
- 输出为全量判定（每行 keep），而非稀疏命中，不适用整批回显判定；
- LLM 调用失败 / 判定行缺失 / keep 值异常一律 fail-safe 保留既有标记。

引擎标识：ForFluencyCheckAgain
"""

from GalTransl.Backend.BaseEngine import register_engine
from GalTransl.Backend.ForFluencyCheck import ForFluencyCheck
from GalTransl.Backend.Prompts import (
    FORFLUENCY_AGAIN_SYSTEM,
    FORGAL_JSON_FLUENCYCHECK_AGAIN_PROMPT,
)


@register_engine("ForFluencyCheckAgain")
class ForFluencyCheckAgain(ForFluencyCheck):
    """中文通顺复核后端（命中句二次复核）。

    输入为第一轮 ForFluencyCheck 已标记 fluency_issue 的句子；本引擎把
    「src+dst+第一轮建议」重新发给 AI，并要求对输入每一行显式给出
    keep: true/false 结论，只保留确认的标记、撤销误报。继承 ForFluencyCheck
    以复用文件级元数据语境、令牌池降级与证据校验覆写；_prepare_round 在
    复核轮分支不做规则初筛。

    引擎标识：ForFluencyCheckAgain
    """

    _log_tag = "[通顺复核]"
    _prompt_pair = (
        FORFLUENCY_AGAIN_SYSTEM,
        FORGAL_JSON_FLUENCYCHECK_AGAIN_PROMPT,
    )
    _round_kind = "review"
    _parse_mode = "confirm"
    # 复核轮每行都必须输出 keep 结论，整批回显判定不适用
    _echo_retry_enabled = False
    _extra_problem_attr = "fluency_issue"
    _noun = "中文通顺复核"
    _input_label = "通顺复核"
    _context_noun = "复核"
