"""禁用词修复后端：仅对带有「用词不当」问题标注的译文重新翻译。

与 ForJPResidue 完全同构：直接继承模板基类 BaseProblemFixRound，
仅覆盖类属性（目标问题类型 / 日志标签 / 是否注入 problem）与提示词，
筛选、分桶、解析、错误恢复全部复用父类，不出现任何平行实现路径。
引擎标识：ForBanWordFix
"""

from typing import Optional

from GalTransl.COpenAI import COpenAITokenPool
from GalTransl.ConfigHelper import CProblemType, CProxyPool, CProjectConfig
from GalTransl.Backend.BaseEngine import register_engine
from GalTransl.Backend.BaseFixRound import BaseProblemFixRound
from GalTransl.Backend.Prompts import FORBAN_SYSTEM, FORGAL_JSON_BANFIX_PROMPT


@register_engine("ForBanWordFix")
class ForBanWordFix(BaseProblemFixRound):
    """禁用词修复后端：针对「用词不当」问题译文生成备选译文。

    继承 BaseProblemFixRound 的完整 batch_translate / 解析 / 错误恢复机制，
    仅覆盖类属性与提示词，不重写任何流程方法。引擎标识：ForBanWordFix
    """

    # 覆盖：命中「用词不当」问题（父类筛选口径一致）
    _problem_types = [CProblemType.用词不当]
    # 覆盖：日志前缀
    _log_tag = "[禁用词修复]"
    # 覆盖：把命中禁用词随原文/译文一并给模型
    _inject_problem = True

    def __init__(
        self,
        config: CProjectConfig,
        eng_type: str,
        proxy_pool: Optional[CProxyPool] = None,
        token_pool: Optional[COpenAITokenPool] = None,
    ) -> None:
        """初始化禁用词修复后端。

        Args:
            config: 项目配置 CProjectConfig。
            eng_type: 引擎标识，固定为 "ForBanWordFix"。
            proxy_pool: 代理池，可选。
            token_pool: 密钥池，可选。
        """
        super().__init__(config, eng_type, proxy_pool, token_pool)
        # 覆盖提示词：禁用词修复专用（模板占位符风格与 JP 版一致，走 replace 注入）
        self.system_prompt = FORBAN_SYSTEM
        self.trans_prompt = FORGAL_JSON_BANFIX_PROMPT
        # 与 ForJPResidue 对齐：覆盖默认值后统一重放 change_prompt 与用户模板 override
        self._finalize_prompts()
