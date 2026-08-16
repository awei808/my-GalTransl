"""换行位置异常修复后端：针对译文「换行位置异常」问题，用 AI 修复换行位置。

仅向 AI 发送带有「换行位置异常」问题标注的译文（不携带原文 src），AI 返回修复
换行后的备选译文，后端解析（含换行防御替换，与多轮对话后端同策略）、按 id 稀疏
匹配、筛查后写入各句 alt_dst，不破坏既有主译文。

作为完整流水线的独立修复阶段使用；也可被独立选中，对已翻译文件单独执行。
引擎标识：ForBRStation
"""

from GalTransl.Backend.BaseEngine import register_engine
from GalTransl.Backend.BaseFixRound import BaseProblemFixRound
from GalTransl.Backend.Prompts import (
    FORGAL_JSON_BRSTATION_PROMPT,
    FORBR_SYSTEM,
)
from GalTransl.ConfigHelper import CProblemType


@register_engine("ForBRStation")
class ForBRStation(BaseProblemFixRound):
    """
    换行位置异常修复后端：向 AI 发送「文件级元数据 + 翻译规范 + 字典 + 换行异常
    说明与解决方法 + 当前译文（仅含换行位置异常问题标注的句子）」，让模型按 3 级
    优先级修复换行位置，并把备选译文写入各句 alt_dst。

    引擎标识：ForBRStation
    """

    # 目标问题类型：换行位置异常
    _problem_types = [CProblemType.换行位置异常]
    # 日志前缀
    _log_tag = "[换行修复]"
    # 换行修复只注入译文 dst，不注入原文 src（纯中文任务，且避免模型回显原文）
    _include_src = False
    # 注入 problem，让模型看到换行异常描述
    _inject_problem = True

    def __init__(
        self,
        config,
        eng_type: str,
        proxy_pool=None,
        token_pool=None,
    ) -> None:
        """
        初始化换行位置异常修复后端。

        Args:
            config: 项目配置对象。
            eng_type: 引擎标识（ForBRStation）。
            proxy_pool: 代理池对象，为 None 时不使用代理。
            token_pool: API Token 池。
        """
        super().__init__(config, eng_type, proxy_pool, token_pool)
        # 覆盖基类（翻译轮）的系统提示词为换行修复轮专用角色声明
        self.system_prompt = FORBR_SYSTEM
        self.trans_prompt = FORGAL_JSON_BRSTATION_PROMPT
        # 覆盖默认值后统一重放 change_prompt 与用户模板 override（基类 __init__ 已应用过一次）
        self._finalize_prompts()

    @staticmethod
    def _build_br_issue_guide() -> str:
        """动态生成「换行位置异常」说明与解决方法（避免与检测侧定义不同步）。

        允许换行字符集直接复用 Problem._ALLOWED_BREAK_CHARS，确保与检测逻辑一致。
        """
        try:
            from GalTransl.Problem import _ALLOWED_BREAK_CHARS

            allowed = _ALLOWED_BREAK_CHARS
        except Exception:
            allowed = "。！？…—”’」』）】、，"
        allowed_str = "、".join(list(allowed))
        lines = [
            "1. 【优先，推荐度最高】调整换行符位置：",
            "   把被拆断在行首/行尾的中文词语或短语合并回上一行，或把换行移动到",
            f"   合理的中文断句点（仅允许落在以下字符之后：{allowed_str}）。",
            "   例：把「她突然站了起<br>来，跑了出去。」改为「她突然站起来，跑了出去。」"
            "（删除不当换行）。",
            "2. 【次选，推荐度中等】在 `<br>` 前面增加一个中文逗号「，」：",
            "   仅当该处确实需要一个停顿、且补逗号后语义与节奏更自然时使用。",
            "   例：把「好痛<br>啊」改为「好痛，<br>啊」。",
            "3. 【最后手段，推荐度最低】直接删除 `<br>`：",
            "   当该处无需任何换行、删除后整句单行阅读更顺畅时使用，且最多删除一个<br>。",
            "   注意：优先用方法1，方法3会丢失原有的视觉分段意图，仅在确无分段必要时采用。",
        ]
        return "\n".join(lines)

    def _apply_extra_first_round_replacements(self, prompt_req: str) -> str:
        """注入换行修复专用说明（[br_issue_guide] 占位符）。"""
        return prompt_req.replace(
            "[br_issue_guide]", self._build_br_issue_guide()
        )
