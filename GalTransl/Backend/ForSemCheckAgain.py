"""语义复核后端：对 ForSemCheck 标记的「疑似错误」命中句做二次复核。

第一轮 ForSemCheck 判定为疑似错误的句子（suspected_error 非空）作为本引擎的
输入；本引擎逐句要求 AI 给出「确认/撤销」结论，仅保留确认仍为实质错译的标记，
撤销属于可接受译文（合理意译、h 场景委婉化等）的误报标记。
不产译文、不触碰主译文与备选译文。可独立运行，也可在语义检测之后按文件调用。
引擎标识：ForSemCheckAgain
"""

from GalTransl.Backend.BaseEngine import register_engine
from GalTransl.Backend.ForSemCheck import ForSemCheck
from GalTransl.Backend.Prompts import (
    FORGAL_JSON_SEMCHECK_AGAIN_PROMPT,
    FORSEMCHECK_AGAIN_SYSTEM,
)


@register_engine("ForSemCheckAgain")
class ForSemCheckAgain(ForSemCheck):
    """语义复核后端（命中句二次复核）。

    输入为第一轮 ForSemCheck 已标记 suspected_error 的句子；本引擎把「src+dst」
    重新发给 AI，并要求对输入每一行显式给出 keep: true/false 结论，只保留确认
    的标记、撤销误报。与 ForSemCheck 的差异（复核轮必须保守，绝不误删真错）：

    - 不预清 suspected_error：仅显式 keep:false 才撤销，保留第一轮信号；
    - 输出为全量判定（每行 keep），而非稀疏命中，不适用整批回显判定；
    - LLM 调用失败 / 判定行缺失 / keep 值异常一律 fail-safe 保留既有标记。

    与主翻译 profile 共用令牌池（与其他后处理后端一致），主池无可用 token 时
    降级跳过，不发任何请求、不清理既有标记。

    引擎标识：ForSemCheckAgain
    """

    _log_tag = "[语义复核]"
    _prompt_pair = (FORSEMCHECK_AGAIN_SYSTEM, FORGAL_JSON_SEMCHECK_AGAIN_PROMPT)
    _round_kind = "review"
    _parse_mode = "confirm"
    # 复核轮每行都必须输出 keep 结论，整批回显判定不适用
    _echo_retry_enabled = False
    # 复核对象仍按语义检测批次配置，保持与第一轮同批大小
    _batch_size_keys = ("gpt.numPerRequestSemCheck", "gpt.numPerRequestBetter")
    _extra_problem_attr = "suspected_error"
    _noun = "语义复核"
    _input_label = "语义复核"
    _context_noun = "复核"
