"""残留日文修复后端：针对「残留日文」问题用 AI 修复残留的日文假名。

统一问题修复后端 ForProblemFixRound 的薄包装子类：仅覆盖类属性（目标问题类型 /
日志前缀 / 是否注入 problem），筛选、分桶、解析、错误恢复与提示词装配全部复用父类。
引擎标识：ForJPResidue
"""

from GalTransl.ConfigHelper import CProblemType
from GalTransl.Backend.BaseEngine import register_engine
from GalTransl.Backend.ForFixRound import ForProblemFixRound


@register_engine("ForJPResidue")
class ForJPResidue(ForProblemFixRound):
    """残留日文修复后端：统一修复后端的薄包装子类（单一类型）。引擎标识：ForJPResidue"""

    # 命中「残留日文」问题
    _problem_types = [CProblemType.残留日文]
    # 日志前缀
    _log_tag = "[残留日文]"
    # 不注入 problem：让模型对照原文自行判断日文应译应留
    _inject_problem = False
