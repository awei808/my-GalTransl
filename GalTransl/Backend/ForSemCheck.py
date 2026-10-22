"""语义差异检测后端：用本地/外部 AI 判断原文与译文的语义是否存在极大差异。

仅判定、不产译文：命中句由本后端置 suspected_error 标记（AI 判定原因或固定
标记 "1"），随后 find_problems 认领并输出「疑似错误」问题（problem 字段），
不触碰主译文与备选译文。作为完整流水线的后处理阶段
（gpt.afterTranslation=semcheck）使用，也可作为独立引擎对已翻译文件执行。
引擎标识：ForSemCheck
"""

from GalTransl import LOGGER
from GalTransl.Backend.BaseDetectRound import BaseDetectRound
from GalTransl.Backend.BaseEngine import register_engine
from GalTransl.Backend.Prompts import (
    FORGAL_JSON_SEMCHECK_PROMPT,
    FORSEMCHECK_SYSTEM,
)


@register_engine("ForSemCheck")
class ForSemCheck(BaseDetectRound):
    """语义差异检测后端。

    向 AI 发送「原文 + 当前译文」（h 场景照常检测），AI 仅输出语义存在极大
    差异的句子 id（可选 reason），本后端为命中句置 suspected_error 标记。
    问题类型统一由 find_problems 认领为「疑似错误」。

    与主翻译 profile 共用令牌池（与其他后处理后端一致）：外部 OpenAI 兼容大
    模型与本地 llama.cpp 均可直接使用，取决于「API 配置」页所选端点；未传
    token_pool 时按主 profile 自建，主池无可用 token 时降级跳过，不发任何请求。

    引擎标识：ForSemCheck
    """

    _log_tag = "[语义检测]"
    _marker_attr = "suspected_error"
    _issue_label = "疑似错误"
    _prompt_pair = (FORSEMCHECK_SYSTEM, FORGAL_JSON_SEMCHECK_PROMPT)
    # 语义检测独立批次：优先 gpt.numPerRequestSemCheck（默认20），未配置时回退
    # 改进轮批次，再兜底调用方实参。本地小模型批次不宜过大。
    _batch_size_keys = ("gpt.numPerRequestSemCheck", "gpt.numPerRequestBetter")
    # 旧版固定文案与数字占位均归一为哨兵 "1"
    _placeholder_reasons = ("疑似错误", "1")
    _noun = "语义差异检测"
    _done_noun = "语义检测"
    _input_label = "语义检测"
    _no_target_noun = "可检测"
    _first_round_hint = "语义差异检测（ForSemCheck）"

    # 整批回显（复制输入全部标错）重试时追加的纠正提示（覆盖基类文案）
    _echo_retry_hint = (
        "注意：上一轮你的输出疑似把整批输入全部判定为疑似错误（整批回显）。"
        "请重新逐句对比 src 与 dst，仅输出语义差异极大的句子；"
        "若均无极大差异，输出空代码块。"
    )
    # 回显判定阈值：语义检测正常命中率极低（实测 ≤12.5%），60% 已远超正常，
    # 且本引擎只做标记不产译文，误杀成本低，宁可保守丢弃（覆盖基类 0.9）
    _echo_hit_ratio = 0.6

    def _prepare_file_context(self, filename: str) -> str:
        """文件级元数据作为检测语境：每文件解析一次，供本文件各批复用。

        pass1_cache 无对应条目时返回空串 → 模板占位符替换为空，行为与旧版一致。
        """
        metadata = self._resolve_file_metadata(filename)
        block = (
            self._format_file_metadata_block(metadata) if metadata is not None else ""
        )
        if block:
            LOGGER.debug(
                f"{self._log_tag} {filename} 注入文件级元数据作为{self._context_noun}语境"
            )
        return block

    def _build_batch_user_content(
        self, batch: list, ctx: str, input_src: str, stats: dict
    ) -> tuple:
        """基类钩子：本批 user 内容 + 空附加日志片段（单轮检测无基准段）。"""
        return self._build_detect_user_content(input_src, ctx or ""), ""

    def _build_detect_user_content(
        self, input_src: str, metadata_block: str = ""
    ) -> str:
        """单轮拼接 user 提示词：替换 [plot_metadata]/[TargetLang]/[Input] 占位符。

        除任务说明、文件级元数据与批次 input 外，不注入术语表/批次元数据/历史
        结果/翻译规范。固定的任务说明置于最前作为缓存头（保证 API 前缀缓存可
        跨文件/批次命中），metadata_block 经模板 [plot_metadata] 占位符注入到
        任务说明之后（<plot_metadata> 作为全局语境）。
        """
        prompt_req = self.trans_prompt.replace("[plot_metadata]", metadata_block)
        prompt_req = prompt_req.replace("[TargetLang]", self.target_lang)
        prompt_req = prompt_req.replace("[Input]", input_src)
        return prompt_req
