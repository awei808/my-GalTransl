"""换行位置异常修复后端：针对「换行位置异常」问题用 AI 修复换行位置。

统一问题修复后端 ForProblemFixRound 的薄包装子类：仅覆盖类属性（目标问题类型 /
日志前缀 / 模式），筛选、分桶、解析、错误恢复与提示词装配全部复用父类。
换行修复专用说明（[br_issue_guide]）由父类在白名单含「换行位置异常」时自动注入。
引擎标识：ForBRStation
"""

from GalTransl.ConfigHelper import CProblemType
from GalTransl.Backend.BaseEngine import register_engine
from GalTransl.Backend.ForFixRound import ForProblemFixRound


@register_engine("ForBRStation")
class ForBRStation(ForProblemFixRound):
    """换行位置异常修复后端：统一修复后端的薄包装子类（单一类型）。引擎标识：ForBRStation"""

    # 命中「换行位置异常」问题
    _problem_types = [CProblemType.换行位置异常]
    # 日志前缀
    _log_tag = "[换行修复]"
    # 仅注入译文 dst，不注入原文 src（纯中文任务，且避免模型回显原文）
    _include_src = False
