"""改进轮后端：整文件翻译完成后评估译文质量，生成可替换的备选译文。"""

from GalTransl.Backend.BaseEngine import register_engine
from GalTransl.Backend.BaseFixRound import BaseImproveRound
from GalTransl.Backend.Prompts import (
    FORGAL_JSON_IMPROVE_PROMPT,
    FORIMPROVE_SYSTEM,
)


@register_engine("ForImproveTranslation")
class ForImproveTranslation(BaseImproveRound):
    """
    改进轮后端：向 AI 发送「文件级元数据 + 翻译规范 + 评估标准 + 原文 + 译文」，
    让模型评估哪些句子的译文还能翻译得更好，并把备选译文写入各句 alt_dst。

    作为完整流水线的第 8 阶段使用；也可被独立选中对已翻译文件执行改进评估。
    引擎标识：ForImproveTranslation
    """

    def __init__(
        self,
        config,
        eng_type: str,
        proxy_pool=None,
        token_pool=None,
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
