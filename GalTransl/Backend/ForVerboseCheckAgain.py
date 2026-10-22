"""节奏冗长复核后端：对 ForVerboseCheck 标记的「节奏冗长」命中句做二次复核。

第一轮 ForVerboseCheck 判定为冗长的句子（verbose_issue 非空）作为本引擎的输入；
本引擎逐句要求 AI 给出「确认/撤销」结论，仅保留确认仍明显冗长的标记，撤销属于
可接受译文（日语省略成分的必要补足、为通顺所需的虚词、h 场景委婉化、有意的语气
强化、原文本身铺陈）的误报标记。不产译文、不触碰主译文与备选译文。可独立运行，
也可在冗长检测之后按文件调用。

与第一轮 ForVerboseCheck 的关键差异（复核轮必须保守，绝不误删真错）：
- 不预清 verbose_issue：仅显式 keep:false 才撤销，保留第一轮信号；
- 输出为全量判定（每行 keep），而非稀疏命中，不适用整批回显判定；
- LLM 调用失败 / 判定行缺失 / keep 值异常一律 fail-safe 保留既有标记。

引擎标识：ForVerboseCheckAgain
"""

from GalTransl.Backend.BaseEngine import register_engine
from GalTransl.Backend.ForVerboseCheck import ForVerboseCheck
from GalTransl.Backend.Prompts import (
    FORGAL_JSON_VERBOSECHECK_AGAIN_PROMPT,
    FORVERBOSE_AGAIN_SYSTEM,
)


@register_engine("ForVerboseCheckAgain")
class ForVerboseCheckAgain(ForVerboseCheck):
    """节奏冗长复核后端（命中句二次复核）。

    输入为第一轮 ForVerboseCheck 已标记 verbose_issue 的句子；本引擎把「src+dst」
    重新发给 AI，并要求对输入每一行显式给出 keep: true/false 结论，只保留确认
    的标记、撤销误报。继承 ForVerboseCheck 以复用文件级元数据语境与主 profile
    令牌池降级逻辑，仅重写复核语义相关部分。

    引擎标识：ForVerboseCheckAgain
    """

    _log_tag = "[冗长复核]"
    _prompt_pair = (
        FORVERBOSE_AGAIN_SYSTEM,
        FORGAL_JSON_VERBOSECHECK_AGAIN_PROMPT,
    )
    _round_kind = "review"
    _parse_mode = "confirm"
    # 复核轮每行都必须输出 keep 结论，整批回显判定不适用
    _echo_retry_enabled = False
    _extra_problem_attr = "verbose_issue"
    _noun = "节奏冗长复核"
    _input_label = "冗长复核"
    _context_noun = "复核"
