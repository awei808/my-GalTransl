"""统一问题修复后端：按参数组合修复任意问题类型。

继承 BaseProblemFixRound（复用筛选/分桶/稀疏解析/回显回滚/错误上报），把旧引擎的
类属性升级为实例参数（set_fix_params），实现问题类型任意组合修复；支持两种输入模式：
模式 A「译文+原文」（_include_src=True）/ 模式 B「仅译文」（_include_src=False）。
提示词由 _FIX_SPECS 按白名单动态装配。旧引擎 ForJPResidue / ForBRStation /
ForBanWordFix 改为本类的薄包装子类。引擎标识：ForFixRound
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import List, Optional

from GalTransl import LOGGER
from GalTransl.COpenAI import COpenAITokenPool
from GalTransl.ConfigHelper import CProblemType, CProxyPool, CProjectConfig
from GalTransl.Backend.BaseEngine import register_engine
from GalTransl.Backend.BaseFixRound import BaseImproveRound, BaseProblemFixRound
from GalTransl.Backend.Prompts import FORFIXROUND_SYSTEM, build_fix_round_prompt

MODE_SRC_DST = "src+dst"
MODE_DST_ONLY = "dst-only"


@dataclass(frozen=True)
class FixSpec:
    """单个问题类型的修复策略：推荐模式与修复指令片段。"""

    mode: str
    instruction: str


# 全问题类型修复策略表：指令口径与 Problem.find_problems 检测逻辑保持一致
_FIX_SPECS: dict = {
    CProblemType.残留日文: FixSpec(
        mode=MODE_SRC_DST,
        instruction="对照 src 判断 dst 中残留日文：原文也存在（专名/角色名/术语）则保留，"
        "仅明显误用或拼写错误时调整；原文对应汉字则改中文；翻译遗漏则补全。仅处理残留日文。",
    ),
    CProblemType.用词不当: FixSpec(
        mode=MODE_SRC_DST,
        instruction="对照 problem 标注的禁用词改为合规同义/近义表达，不改变原意与风格；"
        "专名中且 src 亦然时仅在有合规替代时调整。仅修改被标注词。",
    ),
    CProblemType.换行位置异常: FixSpec(
        mode=MODE_DST_ONLY,
        instruction="仅调整 dst 的换行位置，把 <br> 移到合规断句点（标点/逗号/顿号/空格/Tab/"
        "emoji/颜文字之后），优先移动而非删除，删除是最后手段且最多删一个。\n[br_issue_guide]",
    ),
    CProblemType.丢失换行: FixSpec(
        mode=MODE_SRC_DST,
        instruction="对照 src 换行数，在语义断点（标点、逗号后）补齐缺失的 <br>。",
    ),
    CProblemType.多加换行: FixSpec(
        mode=MODE_SRC_DST,
        instruction="对照 src 换行数，删除 dst 多余的 <br>，使换行数量与原文一致。",
    ),
    CProblemType.长句丢失换行: FixSpec(
        mode=MODE_DST_ONLY,
        instruction="对长句在语义断点（标点、逗号后）补 <br>，使每行长度合理、可读。",
    ),
    CProblemType.频繁换行: FixSpec(
        mode=MODE_DST_ONLY,
        instruction="合并 dst 碎片化短行，恢复合理行长度与断句。",
    ),
    CProblemType.词频过高: FixSpec(
        mode=MODE_SRC_DST,
        instruction="将 problem 标注的高频词部分替换为同义表达；若重复源自 src 刻意修辞可保留。",
    ),
    CProblemType.标点错漏: FixSpec(
        mode=MODE_SRC_DST,
        instruction="对照 src 与 problem 修正标点：本有译无的补回、本无译有的删除"
        "（括号/引号/冒号/；/*/[/<）。",
    ),
    CProblemType.比日文长: FixSpec(
        mode=MODE_SRC_DST,
        instruction="精简冗余措辞使译文长度接近 src（problem 已标注比例），不改变原意。",
    ),
    CProblemType.比日文长严格: FixSpec(
        mode=MODE_SRC_DST,
        instruction="精简冗余措辞使译文长度不超过 src，不改变原意。",
    ),
    CProblemType.字典使用: FixSpec(
        mode=MODE_SRC_DST,
        instruction="对照 problem 标注与字典，把未用字典术语的表达统一为推荐用词。",
    ),
    CProblemType.引入英文: FixSpec(
        mode=MODE_SRC_DST,
        instruction="将 problem 标注的引入英文替换为对应中文表达。",
    ),
    CProblemType.语言不通: FixSpec(
        mode=MODE_SRC_DST,
        instruction="将 problem 标注的非 GBK 异常字符替换为合规中文（♪♥ 等允许符号除外）。",
    ),
    CProblemType.缺控制符: FixSpec(
        mode=MODE_SRC_DST,
        instruction="对照 src 与 problem 标注，把 dst 丢失的控制符（如 %p-1;、%fuser;）补回原位置。",
    ),
    CProblemType.独白男他: FixSpec(
        mode=MODE_SRC_DST,
        instruction="将无 name 字段句子的不当「他」调整为适配独白视角的表述（换用名字/改称「我」"
        "等；其他/他们/他人/他国等除外）。",
    ),
    CProblemType.定语过长: FixSpec(
        mode=MODE_SRC_DST,
        instruction="拆分「是……的」结构的超长定语，使句式自然。",
    ),
    CProblemType.状语过长: FixSpec(
        mode=MODE_SRC_DST,
        instruction="拆分「在……中/里」「……地」结构的超长状语，使句式自然。",
    ),
    CProblemType.疑似错误: FixSpec(
        mode=MODE_SRC_DST,
        instruction="对照 src 与 dst，仅修复明显的语义错译、漏译、译文串行；"
        "拿不准是否属于误报时保留原译文不动。",
    ),
}


def build_fix_instructions(problem_types: list) -> str:
    """按问题类型白名单装配修复指令段（无匹配时给出兜底提示）。"""
    parts = []
    for pt in problem_types:
        spec = _FIX_SPECS.get(pt)
        if spec is not None:
            parts.append(f"- {pt.name}：{spec.instruction}")
    if not parts:
        return "（未配置具体修复指令，仅对照 problem 标注修复对应问题）"
    return "\n".join(parts)


@lru_cache(maxsize=1)
def build_br_issue_guide() -> str:
    """动态生成「换行位置异常」说明与解决方法（复用检测侧描述，避免口径漂移）。"""
    try:
        from GalTransl.Problem import describe_allowed_break_ends

        allowed_desc = describe_allowed_break_ends()
    except Exception:
        allowed_desc = "中文标点、逗号、顿号、空格/Tab、emoji、颜文字"
    return "\n".join(
        [
            "1. 【优先，推荐度最高】调整换行符位置：",
            "   把被拆断在行首/行尾的中文词语或短语合并回上一行，或把换行移动到",
            f"   合理的中文断句点（仅允许落在以下内容之后：{allowed_desc}）。",
            "   例：把「她突然站了起<br>来，跑了出去。」改为「她突然站起来，跑了出去。」"
            "（删除不当换行）。",
            "2. 【次选，推荐度中等】在 `<br>` 前面增加一个中文逗号「，」：",
            "   仅当该处确实需要一个停顿、且补逗号后语义与节奏更自然时使用。",
            "   例：把「好痛<br>啊」改为「好痛，<br>啊」。",
            "3. 【最后手段，推荐度最低】直接删除 `<br>`：",
            "   当该处无需任何换行、删除后整句单行阅读更顺畅时使用，且最多删除一个<br>。",
            "   注意：优先用方法1，方法3会丢失原有的视觉分段意图，仅在确无分段必要时采用。",
        ]
    )


@register_engine("ForFixRound")
class ForProblemFixRound(BaseProblemFixRound):
    """统一问题修复后端：按实例参数组合修复任意问题类型。

    类属性作为默认值（旧引擎薄包装子类覆盖），set_fix_params 供配置注入
    （afterTranslation 的 fix 对象条目）覆盖，实现组合修复与模式切换。
    引擎标识：ForFixRound
    """

    # 日志前缀
    _log_tag = "[问题修复]"
    # 组合修复白名单；空列表时由 batch_translate 惰性回退 problemAnalyze.problemList 全部类型
    _problem_types: List[CProblemType] = []
    # 输入模式：True=译文+原文（模式 A）；False=仅译文（模式 B），由问题类型推导
    _include_src = True
    # 是否把过滤后的 problem 注入输入 JSONL
    _inject_problem = True

    def __init__(
        self,
        config: CProjectConfig,
        eng_type: str,
        proxy_pool: Optional[CProxyPool] = None,
        token_pool: Optional[COpenAITokenPool] = None,
    ) -> None:
        """初始化统一问题修复后端。

        Args:
            config: 项目配置对象。
            eng_type: 引擎标识（ForFixRound）。
            proxy_pool: 代理池对象，为 None 时不使用代理。
            token_pool: API Token 池。
        """
        super().__init__(config, eng_type, proxy_pool, token_pool)
        # 不在 __init__ 回退 problemList：afterTranslation 调度路径总会先 set_fix_params，
        # 此时实例化白名单为空属正常过程，避免打印误导日志或误置 _disabled；
        # 手动执行路径的惰性回退见 batch_translate -> _ensure_problem_types_configured。
        self._setup_dynamic_prompts()

    def set_fix_params(
        self,
        types: List[CProblemType],
        inject_problem: bool = True,
    ) -> bool:
        """注入组合修复参数（afterTranslation 的 fix 对象条目）。

        Args:
            types: 组合修复白名单（CProblemType 列表）；空列表 = 禁用本后端。
            inject_problem: 是否把过滤后的 problem 注入输入 JSONL。

        Returns:
            推导出的输入模式：True=译文+原文（含需对照原文的类型），False=仅译文。
            调用方可用返回值打日志，无需访问内部 _include_src 私有成员。
        """
        coerced = list(types or [])
        if not coerced:
            LOGGER.warning(f"{self._log_tag} 修复问题类型列表为空，本后端不执行任何修复")
            self._disabled = True
        else:
            self._disabled = False
        # 输入模式由问题类型自动推导：任一类型需对照原文 → 译文+原文，否则仅译文
        include_src = any(
            _FIX_SPECS.get(pt) is not None
            and _FIX_SPECS[pt].mode == MODE_SRC_DST
            for pt in coerced
        )
        self._include_src = include_src
        self._problem_types = coerced
        self._inject_problem = inject_problem
        self._setup_dynamic_prompts()
        LOGGER.debug(
            f"{self._log_tag} 参数注入：types={[t.name for t in self._problem_types]}，"
            f"include_src={include_src}（由问题类型推导），"
            f"inject_problem={inject_problem}"
        )
        return include_src

    def _setup_dynamic_prompts(self) -> None:
        """装配统一修复提示词并重放 change_prompt 与用户模板 override。"""
        self.system_prompt = FORFIXROUND_SYSTEM
        self.trans_prompt = build_fix_round_prompt(
            build_fix_instructions(self._problem_types)
        )
        self._finalize_prompts()

    def _effective_problem_types(self) -> Optional[list]:
        """本轮实际注入的 problem 类型白名单；不注入时返回 None。"""
        if not self._inject_problem:
            return None
        return self._problem_types or None

    def _apply_extra_first_round_replacements(self, prompt_req: str) -> str:
        """白名单含换行位置异常时注入换行修复专用说明。"""
        has_br = CProblemType.换行位置异常 in (self._problem_types or [])
        return prompt_req.replace(
            "[br_issue_guide]", build_br_issue_guide() if has_br else ""
        )

    @staticmethod
    def _build_br_issue_guide() -> str:
        """兼容旧 ForBRStation 的换行修复说明生成入口。"""
        return build_br_issue_guide()

    @staticmethod
    def _coerce_problem_type_list(raw_types) -> list:
        """把配置的问题类型规范为 CProblemType 列表（与 BaseImproveRound 同口径）。"""
        return BaseImproveRound._coerce_problem_type_list(raw_types)

    def _ensure_problem_types_configured(self) -> bool:
        """确保修复类型已配置；未配置时惰性回退 problemAnalyze.problemList 全部类型。

        Returns:
            True 表示可执行修复；False 表示两处皆空（或本后端已被禁用），调用方应跳过。
        """
        if self._problem_types:
            return True
        all_types = self._coerce_problem_type_list(
            self.pj_config.getProblemAnalyzeConfig("problemList")
        )
        if not all_types:
            return False
        LOGGER.warning(
            f"{self._log_tag} 未指定修复类型，回退 problemAnalyze.problemList "
            f"全部 {len(all_types)} 类：{[t.name for t in all_types]}"
        )
        self.set_fix_params(all_types)
        return True

    async def batch_translate(
        self,
        filename: str,
        cache_file_path: str,
        trans_list,
        num_pre_request: int,
        retry_failed: bool = False,
        gpt_dic=None,
        proofread: bool = False,
        retran_key: str = "",
        translist_hit: Optional[list] = None,
        translist_unhit: Optional[list] = None,
    ):
        """修复轮入口：修复类型未配置（含手动执行未指定）时跳过，否则走基类稀疏修复流程。"""
        if getattr(self, "_disabled", False) or not self._ensure_problem_types_configured():
            LOGGER.warning(f"{self._log_tag} 修复类型未配置，跳过 {filename}")
            return trans_list
        return await super().batch_translate(
            filename,
            cache_file_path,
            trans_list,
            num_pre_request,
            retry_failed=retry_failed,
            gpt_dic=gpt_dic,
            proofread=proofread,
            retran_key=retran_key,
            translist_hit=translist_hit,
            translist_unhit=translist_unhit,
        )
