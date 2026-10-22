"""节奏冗长检测后端：判断译文是否比原文明显冗长拖沓。

仅判定、不产译文：命中句由本后端置 verbose_issue 标记（AI 判定原因或固定
标记 "1"），随后 find_problems 认领并输出「节奏冗长」问题（problem 字段），
不触碰主译文与备选译文。作为完整流水线的后处理阶段
（gpt.afterTranslation=verbosecheck）使用，也可作为独立引擎对已翻译文件执行。

与「节奏单一」（相邻行等长的启发式检测）互补：本引擎只看单句是否信息量未增
却被垫词、同义重复或过度解释撑大，不看行宽。
引擎标识：ForVerboseCheck
"""

from GalTransl.Backend.BaseEngine import register_engine
from GalTransl.Backend.ForSemCheck import ForSemCheck
from GalTransl.Backend.Prompts import (
    FORGAL_JSON_VERBOSECHECK_PROMPT,
    FORVERBOSE_SYSTEM,
)


@register_engine("ForVerboseCheck")
class ForVerboseCheck(ForSemCheck):
    """节奏冗长检测后端。

    向 AI 发送「原文 + 当前译文」（h 场景照常检测），AI 仅输出明显冗长的
    句子 id（可选 reason），本后端为命中句置 verbose_issue 标记。问题类型
    统一由 find_problems 认领为「节奏冗长」。

    与 ForSemCheck 同构（单轮、稀疏命中、文件级元数据作为语境、整批回显重试），
    仅提示词、标记字段与批次配置键不同；因此直接继承其语境/内容构建钩子。

    引擎标识：ForVerboseCheck
    """

    _log_tag = "[冗长检测]"
    _marker_attr = "verbose_issue"
    _issue_label = "节奏冗长"
    _prompt_pair = (FORVERBOSE_SYSTEM, FORGAL_JSON_VERBOSECHECK_PROMPT)
    # 冗长检测独立批次：优先 gpt.numPerRequestVerboseCheck（默认20），未配置时
    # 回退语义检测批次、改进轮批次，再兜底调用方实参。
    _batch_size_keys = (
        "gpt.numPerRequestVerboseCheck",
        "gpt.numPerRequestSemCheck",
        "gpt.numPerRequestBetter",
    )
    # 旧版固定文案与数字占位均归一为哨兵 "1"
    _placeholder_reasons = ("节奏冗长", "1")
    _noun = "节奏冗长检测"
    _done_noun = "冗长检测"
    _input_label = "冗长检测"
    _no_target_noun = "可检测"
    _first_round_hint = "节奏冗长检测（ForVerboseCheck）"

    # 整批回显（复制输入全部标错）重试时追加的纠正提示（覆盖基类文案）
    _echo_retry_hint = (
        "注意：上一轮你的输出疑似把整批输入全部判定为节奏冗长（整批回显）。"
        "请重新逐句对比 src 与 dst，仅输出确实存在同义重复、垫词堆砌或重复陈述的句子；"
        "若均无冗长问题，输出空代码块。"
    )
    # 回显判定阈值：与 ForSemCheck 同口径（本引擎只做标记不产译文，误杀成本低）
    _echo_hit_ratio = 0.6

    # reason 必须带可在 dst 中核对的「…」片段（提示词已要求「形态：「A」/「B」」）：
    # 实测模型失控时会把推理正文里的输入行回显成结果，或输出空 reason，这类命中在
    # 前端就是一条「没有原因的节奏冗长」，无法人工复核，故开启片段证据校验丢弃它们
    _reason_evidence_required = True
    # 本批「无片段证据命中数 / 输出命中行数」> 0.5 即整批作废（默认阈值，显式写出
    # 以便与 _echo_hit_ratio 一并review）
    _reason_anomaly_ratio = 0.5
    # 重试纠正提示：针对「没给片段」而非「整批回显」
    _reason_retry_hint = (
        "注意：上一轮你输出的命中句多数没有按格式给出可在 dst 中核对的片段证据"
        "（reason 为空，或片段在译文中找不到原样文字）。请重新逐句判断："
        "只输出确实存在同义堆叠或过度解释的句子，reason 必须写成"
        "「形态：「片段1」/「片段2」」，片段原样摘自 dst 且不超过 8 字；"
        "摘不出片段的句子说明它不成立，不要输出。若整批均无冗长，输出空代码块。"
    )
