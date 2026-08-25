"""禁用词修复后端：针对「用词不当」问题用 AI 替换译文中被标注的禁用词。

统一问题修复后端 ForProblemFixRound 的薄包装子类：仅覆盖类属性（目标问题类型 /
日志前缀 / 是否注入 problem），筛选、分桶、解析、错误恢复与提示词装配全部复用父类。
引擎标识：ForBanWordFix
"""

from GalTransl.ConfigHelper import CProblemType
from GalTransl.Backend.BaseEngine import register_engine
from GalTransl.Backend.ForFixRound import ForProblemFixRound


@register_engine("ForBanWordFix")
class ForBanWordFix(ForProblemFixRound):
    """禁用词修复后端：统一修复后端的薄包装子类（单一类型）。引擎标识：ForBanWordFix"""

    # 命中「用词不当」问题
    _problem_types = [CProblemType.用词不当]
    # 日志前缀
    _log_tag = "[禁用词修复]"
    # 注入 problem：让模型直接看到命中的禁用词
    _inject_problem = True
