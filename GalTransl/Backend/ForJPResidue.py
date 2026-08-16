"""残留日文修复后端：针对译文「残留日文」问题，用 AI 修复残留的日文假名。

仅向 AI 发送带有「残留日文」问题标注的译文（同时携带对应原文 src，不带 problem
标注），AI 返回修复残留日文后的备选译文，后端解析、按 id 稀疏匹配、筛查后写入
各句 alt_dst，不破坏既有主译文。

作为完整流水线的独立修复阶段使用；也可被独立选中，对已翻译文件单独执行。
引擎标识：ForJPResidue
"""

from GalTransl.Backend.BaseEngine import register_engine
from GalTransl.Backend.BaseFixRound import BaseProblemFixRound
from GalTransl.Backend.Prompts import (
    FORGAL_JSON_JPREPAIR_PROMPT,
    FORJP_SYSTEM,
)
from GalTransl.ConfigHelper import CProblemType


@register_engine("ForJPResidue")
class ForJPResidue(BaseProblemFixRound):
    """残留日文修复后端：针对「残留日文」问题译文生成备选译文（写入 alt_dst）。

    本类作为「按问题类型修复译文」的模板基类：子类仅需覆盖下列类属性与提示词，
    即可复用完整的筛选 / 分桶 / 解析 / 错误恢复流程，避免平行重写导致口径漂移：
      - _problem_types：命中的 CProblemType 白名单（筛选依据）。
      - _log_tag：日志前缀（如 "[残留日文]"）。
      - _inject_problem：是否把问题字符串注入输入 JSONL 给模型（JP 不注入，
        让模型对照原文自行判断应译/应留；Ban 注入，让模型直接看到命中禁用词）。

    引擎标识：ForJPResidue
    """

    # 子类覆盖：命中的译文问题类型
    _problem_types = [CProblemType.残留日文]
    # 子类覆盖：日志前缀
    _log_tag = "[残留日文]"
    # 子类覆盖：是否把问题字符串注入输入 JSONL
    _inject_problem = False

    def __init__(
        self,
        config,
        eng_type: str,
        proxy_pool=None,
        token_pool=None,
    ) -> None:
        """
        初始化残留日文修复后端。

        Args:
            config: 项目配置对象。
            eng_type: 引擎标识（ForJPResidue）。
            proxy_pool: 代理池对象，为 None 时不使用代理。
            token_pool: API Token 池。
        """
        super().__init__(config, eng_type, proxy_pool, token_pool)
        # 覆盖基类（翻译轮）的系统提示词为残留日文修复轮专用角色声明
        self.system_prompt = FORJP_SYSTEM
        self.trans_prompt = FORGAL_JSON_JPREPAIR_PROMPT
        # 覆盖默认值后统一重放 change_prompt 与用户模板 override（基类 __init__ 已应用过一次）
        self._finalize_prompts()
