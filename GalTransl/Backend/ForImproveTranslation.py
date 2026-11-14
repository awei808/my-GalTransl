"""改进轮后端：整文件翻译完成后评估译文质量，生成可替换的备选译文。"""

import unicodedata
from typing import Any, Optional

from GalTransl.COpenAI import COpenAITokenPool
from GalTransl.ConfigHelper import CProxyPool, CProjectConfig
from GalTransl.Backend.BaseEngine import register_engine
from GalTransl.Backend.BaseFixRound import BaseImproveRound
from GalTransl.Backend.Prompts import (
    FORGAL_JSON_IMPROVE_PROMPT,
    FORIMPROVE_SYSTEM,
)
from GalTransl.Utils import contains_japanese

# better 只填了键名/占位符（未真正给译文）时的非法值清单
_BARE_PLACEHOLDERS = frozenset({"dst", "better", "src", "name", "pre_dst", "alt_dst"})


@register_engine("ForImproveTranslation")
class ForImproveTranslation(BaseImproveRound):
    """
    改进轮后端：向 AI 发送「文件级元数据 + 翻译规范 + 评估标准 + 原文 + 译文」，
    让模型评估哪些句子的译文还能翻译得更好，并把备选译文写入各句 alt_dst。

    作为完整流水线的「阶段 8」（修复和改进译文）使用；也可被独立选中对已翻译文件执行改进评估。
    引擎标识：ForImproveTranslation
    """

    def __init__(
        self,
        config: CProjectConfig,
        eng_type: str,
        proxy_pool: Optional[CProxyPool] = None,
        token_pool: Optional[COpenAITokenPool] = None,
    ) -> None:
        """
        初始化改进轮后端。

        Args:
            config: 项目配置对象。
            eng_type: 引擎标识（ForImproveTranslation）。
            proxy_pool: 代理池对象，为 None 时不使用代理。
            token_pool: API Token 池。
        """
        super().__init__(config, eng_type, proxy_pool, token_pool)
        # 覆盖基类（翻译轮）的系统提示词为改进轮专用角色声明
        self.system_prompt = FORIMPROVE_SYSTEM
        self.trans_prompt = FORGAL_JSON_IMPROVE_PROMPT
        # 覆盖默认值后统一重放 change_prompt 与用户模板 override（基类 __init__ 已应用过一次）
        self._finalize_prompts()

    def _better_reject_reason(self, tran: Any, better: str) -> str:
        """改进轮合法性校验：拦占位符/纯符号译文与模型新引入的日文假名。

        日文口径按「只拦新增」：原文与当前译文里已出现的假名（专名/术语/拟声词）
        保留合法，对齐「残留日文修复轮放行原文专名」的指导口径（注意
        Problem.find_problems 的检测本身不扣原文已有假名）。目标语言是日文时
        该规则不适用。纯符号台词（如「……」）一并拦下属刻意取舍：丢一条备选
        不会写坏主译文，放宽反而会放过 `...` 这类占位输出。
        """
        text = unicodedata.normalize("NFC", better.strip())
        if not text:
            return "空译文"
        if text.casefold() in _BARE_PLACEHOLDERS:
            return "占位符而非译文"
        if not any(ch.isalnum() for ch in text):
            return "纯标点或符号"
        if "Japanese" in str(getattr(self, "target_lang", "") or ""):
            return ""
        jp_chars = contains_japanese(text)
        if not jp_chars:
            return ""
        kept = str(getattr(tran, "post_src", "") or "")
        kept += str(tran.proofread_zh or tran.pre_dst or "")
        kept = unicodedata.normalize("NFC", kept)
        introduced = "".join(ch for ch in jp_chars if ch not in kept)
        if not introduced:
            return ""
        return f"含新增日文假名 {introduced}"
