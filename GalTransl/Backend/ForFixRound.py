"""统一问题修复后端：按参数组合修复任意问题类型。

继承 BaseProblemFixRound（复用筛选/分桶/稀疏解析/错误上报），把旧引擎的
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
from GalTransl.ConfigHelper import (
    DEFAULT_AVG_SENTENCE_LENGTH_THRESHOLD,
    DEFAULT_H_SENTENCE_LENGTH_THRESHOLD,
    CProblemType,
    CProxyPool,
    CProjectConfig,
)
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
        instruction="修复 dst 的换行位置异常：\n[br_issue_guide]\n"
        "修复后保留的 <br> 仍须落在合规断句点（[allowed_break_ends]之后），"
        "否则重检仍会报「换行位置异常」。",
    ),
    CProblemType.丢失换行: FixSpec(
        mode=MODE_SRC_DST,
        instruction="对照 src 换行数，在语义断点（标点、逗号后）补齐缺失的 <br>。"
        "修复后该句最多保留 3 个 <br>（即不超过 3 个断句片段），禁止出现 4 个及以上片段；"
        "即使 src 换行数更多，也以此为上限。",
    ),
    CProblemType.多加换行: FixSpec(
        mode=MODE_SRC_DST,
        instruction="对照 src 换行数，删除 dst 多余的 <br>，使换行数量与原文一致。",
    ),
    CProblemType.单句过长: FixSpec(
        mode=MODE_DST_ONLY,
        instruction="该句被判定为单句过长：在语义断点（标点、逗号后）补 <br>，使每行长度合理、可读。"
        "补入的 <br> 使修复后最多保留 3 个 <br>（不超过 3 个断句片段），禁止出现 4 个及以上片段。",
    ),
    CProblemType.频繁换行: FixSpec(
        mode=MODE_DST_ONLY,
        instruction="合并 dst 碎片化短行，恢复合理行长度与断句。"
        "合并后该句最多保留 3 个 <br>（不超过 3 个断句片段），禁止出现 4 个及以上片段。",
    ),
    CProblemType.节奏单一: FixSpec(
        mode=MODE_DST_ONLY,
        instruction="该句换行前后相邻两行字数过于接近，疑似为对齐行宽而添加了冗余措辞。"
        "在不改变原意、不新增或删除 <br> 的前提下压缩/改写冗余表达，使各行长度自然、"
        "节奏有变化；若译文本身明显刻意对仗/排比（整齐短语、叠句等），则保留原样。",
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
    CProblemType.词语色彩不一致: FixSpec(
        mode=MODE_SRC_DST,
        instruction="对照 problem 标注的期望色彩方向（来自词语色彩检查的判定说明，"
        "其依据可能是批次区间的用词色彩标注或原文语气），"
        "调整译文措辞风格使其与该色彩一致；仅修改色彩明显不符的表达，不改变句意与信息量。",
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
def build_allowed_break_ends_desc() -> str:
    """检测侧「换行前允许内容」描述（复用 Problem 描述，避免口径漂移）。"""
    try:
        from GalTransl.Problem import describe_allowed_break_ends

        return describe_allowed_break_ends()
    except Exception:
        return "中文标点、逗号、顿号、破折号（——）、空格/Tab、emoji、颜文字"


def build_sentence_len_threshold_desc(config: CProjectConfig) -> str:
    """平均分句长度阈值描述：取 problemAnalyze 非 H / H 两档中的较大值，供提示词注入。

    修复轮不携带 H 区间信息、无法按句区分场景，取较大值以免对 H 句按非 H 阈值过度拆分。
    口径与 Problem.find_problems 的「单句过长」判定一致（去换行后总字符数 ÷（换行数+1））。
    配置对象缺这两个 getter（测试桩 / 非常规调用）或取值抛错时，回退检测侧默认值。
    """
    try:
        avg_len = config.getAvgSentenceLengthThreshold()
        h_len = config.getHSentenceLengthThreshold()
    except Exception as e:
        avg_len = DEFAULT_AVG_SENTENCE_LENGTH_THRESHOLD
        h_len = DEFAULT_H_SENTENCE_LENGTH_THRESHOLD
        LOGGER.debug(
            f"平均分句长度阈值读取失败（回退 {avg_len}/{h_len}）：{type(e).__name__}: {e}"
        )
    limit = max(avg_len, h_len)
    return (
        f"本项目 {limit} 字，"
        "口径为去换行后译文总字符数（含标点）÷（换行数 + 1）"
    )


@lru_cache(maxsize=1)
def build_br_issue_guide() -> str:
    """「换行位置异常」修复指引（ForBRStation 与 ForFixRound 共用同一份文本）。

    阈值处留 [avg_sentence_len_threshold] 占位符，由首轮装配注入 problemAnalyze 配置实际值。
    """
    return (
        "在不改变句子原意、遵守翻译风格规范和保证单句长度不超过平均分句长度阈值"
        "（[avg_sentence_len_threshold]）的前提下，"
        "自由调整<br>位置或删除<br>、更改句子表述并添加标点符号；"
        "禁止添加<br>。"
    )


class _EmptyTokenPool:
    """空令牌池占位：主 profile 不可用时保证基类 init_chatbot 不崩溃（不发任何请求）。"""

    def get_available_token(self) -> list:
        return []


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
            token_pool: API Token 池；为 None 时复用主翻译 profile 的 OpenAI 兼容端点。
        """
        self._disabled_reason = ""
        if token_pool is None:
            # 直接实例化（测试/独立调用）未传池：按主 profile 自建，与其他调用路径一致
            try:
                token_pool = COpenAITokenPool(config, eng_type)
            except Exception as e:
                # 老项目缺 OpenAI-Compatible 段或 tokens 畸形等极端场景：降级禁用，不中断后处理
                LOGGER.warning(
                    f"{self._log_tag} 主翻译令牌池构建失败：{type(e).__name__}: {e}"
                )
                self._disabled_reason = "主翻译令牌池构建失败"
                token_pool = _EmptyTokenPool()
        if not getattr(token_pool, "get_available_token", lambda: [])():
            # 空令牌池（无配置端点）与构建失败同口径：禁用并跳过请求，避免回退到本地端口默认值
            if not self._disabled_reason:
                self._disabled_reason = "未配置 OpenAI 兼容后端（请在「API 配置」页配置）"
            LOGGER.warning(f"[{eng_type}] {self._disabled_reason}，将跳过问题修复请求")
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
        """白名单含换行位置异常时注入换行修复专用说明，并填充检测侧动态口径。"""
        has_br = CProblemType.换行位置异常 in (self._problem_types or [])
        prompt_req = prompt_req.replace(
            "[br_issue_guide]", build_br_issue_guide() if has_br else ""
        )
        # 检测侧「允许断行位置」描述动态注入，避免与 Problem 白名单口径漂移
        prompt_req = prompt_req.replace(
            "[allowed_break_ends]", build_allowed_break_ends_desc()
        )
        # 平均分句长度阈值取 problemAnalyze 配置实际值；占位符仅在白名单含换行位置异常
        # 或用户模板显式引用时出现，顺带作为该条调试日志的开关
        if "[avg_sentence_len_threshold]" in prompt_req:
            len_desc = build_sentence_len_threshold_desc(self.pj_config)
            LOGGER.debug(f"{self._log_tag} 注入平均分句长度阈值：{len_desc}")
            prompt_req = prompt_req.replace("[avg_sentence_len_threshold]", len_desc)
        return prompt_req

    @staticmethod
    def _build_br_issue_guide() -> str:
        """兼容旧 ForBRStation 的换行修复说明生成入口（返回未填阈值占位符的模板文本）。"""
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
        """修复轮入口：修复类型未配置（含手动执行未指定）或后端未配置时跳过，否则走基类稀疏修复流程。"""
        disabled_reason = getattr(self, "_disabled_reason", "")
        if disabled_reason or getattr(self, "_disabled", False) or not self._ensure_problem_types_configured():
            reason = disabled_reason or "修复类型未配置"
            LOGGER.warning(f"{self._log_tag} 跳过 {filename}：{reason}")
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
