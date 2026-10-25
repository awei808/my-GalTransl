"""流水线阶段清单与自定义步骤解析（唯一真源）。

完整流水线的阶段顺序、配置开关、后端槽位与依赖关系集中定义在此，
供三处复用：
1. 后端编排（`Frontend/LLMTranslate.py:_run_full_pipeline`）——按 `order` 遍历执行；
2. 服务端接口（`GET /api/pipeline-stages`）——向任意客户端暴露同一份清单；
3. 前端设置界面（项目设置页 / 新建向导）——渲染阶段开关，不再各自硬编码。

自定义流水线（`internals.pipeline.steps` 低代码有序列表）的词表、解析与校验
（`resolve_pipeline_steps`）也收口在此：列表存在即权威（enable* 开关不再生效），
支持 AI 初步处理后端作为顶层步骤、同一后端多次出现。

约束：本模块**不得**导入重依赖（后端引擎、CProjectConfig 等），
只依赖标准库，以便服务端与测试轻量引用。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class PipelineStage:
    """单个流水线阶段的静态描述。

    Attributes:
        key: 阶段标识（英文 snake_case），前端与日志的稳定引用名。
        label: 阶段的中文显示名（不含"阶段 N"前缀，编号由 order 派生）。
        order: 执行顺序；同一数字表示并列，显示时按列表位置编号。
        enabled_key: config.yaml 中控制该阶段开关的完整参数路径。
        backend_slot: 使用的 stageBackends 槽位；空字符串表示不使用后端。
        depends_on: 依赖的阶段 key；被依赖阶段未执行时本阶段自动跳过。
        backend_names: 该阶段会实例化的后端引擎类名（供「注入内容可配」界面展示）。
        sample_key: 该阶段「生成示例文件」对应的缓存产物名；空表示不支持示例文件。
        needs_compressed_text: 是否要求阶段 1 的压缩文本非空（运行期硬依赖）。
    """

    key: str
    label: str
    order: int
    enabled_key: str
    backend_slot: str
    depends_on: Tuple[str, ...] = ()
    backend_names: Tuple[str, ...] = ()
    sample_key: str = ""
    needs_compressed_text: bool = False

    def to_dict(self) -> Dict[str, object]:
        """导出为可 JSON 序列化的字典（服务端接口用）。"""
        return {
            "key": self.key,
            "label": self.label,
            "order": self.order,
            "enabled_key": self.enabled_key,
            "backend_slot": self.backend_slot,
            "depends_on": list(self.depends_on),
            "backend_names": list(self.backend_names),
            "sample_key": self.sample_key,
            "needs_compressed_text": self.needs_compressed_text,
        }


# 参数路径前缀：阶段开关统一挂在 internals.pipeline 下
_PIPELINE_PREFIX = "internals.pipeline."


def _enabled_key(name: str) -> str:
    """由开关短名（如 enableGlobalPrompt）拼出完整配置路径。"""
    return _PIPELINE_PREFIX + name


# 阶段清单：顺序即执行顺序，勿随意调换
PIPELINE_STAGES: Tuple[PipelineStage, ...] = (
    PipelineStage(
        key="validate",
        label="输入数据校验",
        order=0,
        enabled_key=_enabled_key("enableValidate"),
        backend_slot="",
    ),
    PipelineStage(
        key="compress",
        label="文本无损压缩",
        order=1,
        enabled_key=_enabled_key("enableCompress"),
        backend_slot="",
    ),
    PipelineStage(
        key="gen_dic",
        label="术语表构建",
        order=2,
        enabled_key=_enabled_key("enableGenDic"),
        backend_slot="metadata",
        backend_names=("GenDic",),
    ),
    PipelineStage(
        key="file_meta",
        label="文件级元数据",
        order=3,
        enabled_key=_enabled_key("enableFileMeta"),
        backend_slot="metadata",
        backend_names=("ForFileMetaData",),
        sample_key="FileMetaData.json",
    ),
    PipelineStage(
        key="plot_route",
        label="剧情路线图",
        order=4,
        enabled_key=_enabled_key("enablePlotRoute"),
        backend_slot="metadata",
        depends_on=("file_meta",),
        backend_names=("ForPlotRouteMap",),
        sample_key="PlotRouteMap.json",
    ),
    PipelineStage(
        # 全局分析后置到路线图之后：优先按路线图划分逐路线分析再汇总；
        # 依赖只声明 compress——路线图缺失/不合格时本阶段回退全文分析而非跳过
        key="global_analysis",
        label="全局游戏分析",
        order=5,
        enabled_key=_enabled_key("enableGlobalPrompt"),
        backend_slot="metadata",
        depends_on=("compress",),
        backend_names=("ForRouteAnalysis", "ForGlobalAnalysis"),
        sample_key="GlobalPrompt.json",
        needs_compressed_text=True,
    ),
    PipelineStage(
        key="batch_meta",
        label="批次级元数据",
        order=6,
        enabled_key=_enabled_key("enableBatchMeta"),
        backend_slot="metadata",
        backend_names=("ForBatchMetaData",),
        sample_key="BatchMetadata.json",
    ),
    PipelineStage(
        key="translate",
        label="翻译执行",
        order=7,
        enabled_key=_enabled_key("enableTranslate"),
        backend_slot="translate",
        backend_names=("ForGalJsonTranslate",),
    ),
    PipelineStage(
        key="improve",
        label="修复和改进译文",
        order=8,
        enabled_key=_enabled_key("enableImprove"),
        backend_slot="afterTrans",
    ),
)

# key -> 阶段 的索引（顺序与 PIPELINE_STAGES 一致）
STAGES_BY_KEY: Dict[str, PipelineStage] = {s.key: s for s in PIPELINE_STAGES}

# 「生成示例文件」支持的阶段：缓存产物名 -> 阶段 key（与前端 sampleable 对应）
SAMPLE_PRODUCTS: Dict[str, str] = {
    s.sample_key: s.key for s in PIPELINE_STAGES if s.sample_key
}


def get_stage(key: str) -> Optional[PipelineStage]:
    """按 key 取阶段描述，不存在返回 None。"""
    return STAGES_BY_KEY.get(key)


def iter_stages() -> Tuple[PipelineStage, ...]:
    """按执行顺序返回全部阶段。"""
    return PIPELINE_STAGES


def stage_display_label(stage: PipelineStage) -> str:
    """生成带编号的显示名，如「阶段 2：全局游戏分析」。

    编号取 1-based 列表位置，故小数序号（如历史上的「阶段 4.5」）自然消失。
    """
    index = PIPELINE_STAGES.index(stage)
    return f"阶段 {index}：{stage.label}"


def _satisfied_dependencies(
    stage: PipelineStage, enabled_map: Dict[str, bool]
) -> List[str]:
    """返回该阶段未满足的依赖 key 列表（依赖被显式关闭即算未满足）。"""
    return [
        dep for dep in stage.depends_on
        if not enabled_map.get(dep, True)
    ]


def compute_skip_reasons(
    enabled_map: Dict[str, bool],
    compressed_texts_present: bool = True,
) -> Dict[str, str]:
    """计算每个阶段在给定开关下的跳过原因。

    Args:
        enabled_map: 阶段 key -> 是否启用（缺省视为启用）。
        compressed_texts_present: 阶段 1 产出的压缩文本是否非空。

    Returns:
        阶段 key -> 跳过原因（中文）；未跳过的阶段不在结果中。
        原因文案与运行期日志口径一致，保证前端预告与实跑行为不偏差。
    """
    reasons: Dict[str, str] = {}
    for stage in PIPELINE_STAGES:
        if not enabled_map.get(stage.key, True):
            reasons[stage.key] = f"已禁用（{stage.enabled_key.split('.')[-1]}=false）"
            continue
        missing = _satisfied_dependencies(stage, enabled_map)
        if missing:
            labels = "、".join(STAGES_BY_KEY[m].label for m in missing)
            reasons[stage.key] = f"依赖阶段未执行：{labels}"
            continue
        if stage.needs_compressed_text and not compressed_texts_present:
            reasons[stage.key] = "阶段 1 压缩文本为空，无输入可供分析"
    return reasons


def to_payload() -> Dict[str, object]:
    """导出完整清单（含编号显示名），供 `GET /api/pipeline-stages` 返回。"""
    return {
        "stages": [
            {
                **s.to_dict(),
                "display_label": stage_display_label(s),
                "index": PIPELINE_STAGES.index(s),
            }
            for s in PIPELINE_STAGES
        ],
        "sample_products": dict(SAMPLE_PRODUCTS),
        "default_steps": list(DEFAULT_PIPELINE_STEPS),
    }


# —— 自定义流水线步骤（低代码列表）——
# internals.pipeline.steps：有序步骤列表，存在即权威（各阶段 enable* 开关不再生效，
# 缺省时不读取本列表、维持 enable* 开关语义）。条目两类：
# 1. 阶段 key 字符串（上方 PIPELINE_STAGES 的 key，improve 为宏：展开 gpt.afterTranslation 链）；
# 2. AI 初步处理后端条目（与 gpt.afterTranslation 条目同构：字符串 key 或
#    {"fix": {"types": [...], "injectProblem": ...}} 对象，types 支持类型名或
#    序号——1 起，按 /api/problem-types 目录顺序，见 PROBLEM_TYPE_CATALOG_ORDER）。
# 同一后端允许多次出现（如两个不同类型组合的 fix 依次执行）。

# 可作为顶层步骤出现的 AI 初步处理后端 key（improve 除外——它在步骤列表中是阶段宏）
AFTER_TRANS_STEP_KEYS: Tuple[str, ...] = (
    "brfix", "jpfix", "banfix", "fix",
    "semcheck", "semcheckagain", "tonecheck", "tonecheckagain",
    "verbosecheck", "verbosecheckagain",
)

# 默认步骤顺序（未配置 steps 时的等价清单；实际执行仍走各阶段 enable* 开关）
DEFAULT_PIPELINE_STAGES: Tuple[str, ...] = tuple(s.key for s in PIPELINE_STAGES)
DEFAULT_PIPELINE_STEPS: Tuple[str, ...] = DEFAULT_PIPELINE_STAGES

# 重复出现大概率是配置失误的阶段（有「已存在即跳过」护栏，重复执行无意义且耗时；
# improve 宏重复等于整条 afterTranslation 链跑两遍，也计入）
_EXPENSIVE_STAGE_KEYS = frozenset(
    (
        "validate", "compress", "gen_dic", "file_meta", "plot_route",
        "global_analysis", "batch_meta", "translate", "improve",
    )
)

# 自定义步骤的配置键
PIPELINE_STEPS_KEY = "internals.pipeline.steps"


def _is_aftertrans_entry(entry: object) -> bool:
    """判定条目是否为合法的 AI 初步处理步骤（字符串 key 或 fix 对象条目）。

    注意裸字符串 "fix" 不算：fix 必须是 {fix: {types, ...}} 对象条目（无参数的
    fix 无意义），由 resolve_pipeline_steps 单独拒收并告警。
    """
    if isinstance(entry, str):
        p = entry.strip().lower()
        return p in AFTER_TRANS_STEP_KEYS and p != "fix"
    if isinstance(entry, dict):
        return isinstance(entry.get("fix"), dict)
    return False


def _aftertrans_canonical(entry: object) -> str:
    """译后条目的规范化键：完全相同的条目共享断点分组，重复出现只执行一遍。"""
    if isinstance(entry, str):
        return entry.strip().lower()
    fix_cfg = (entry or {}).get("fix") or {}
    return json.dumps(
        {
            "fix": {
                "types": list(fix_cfg.get("types") or []),
                "injectProblem": bool(fix_cfg.get("injectProblem", True)),
            }
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def resolve_pipeline_steps(projectConfig: Any) -> Tuple[List[object], List[str], bool]:
    """解析自定义流水线步骤配置（internals.pipeline.steps）。

    Args:
        projectConfig: 项目配置对象（仅用 getKey，避免本模块依赖 CProjectConfig）。

    Returns:
        (steps, warnings, custom)：steps 为有序步骤列表（阶段 key 字符串与 AI
        初步处理条目混排，字符串条目规范化为小写去空白，重复允许）；warnings 为
        需调用方落日志/用户提示的中文告警；custom 表示 steps 配置**有效生效**
        （权威模式，enable* 开关不再参与判定）——配置缺省或非法回退默认顺序时
        为 False，此时各阶段仍走 enable* 开关（与旧版行为一致）。
    """
    raw = projectConfig.getKey(PIPELINE_STEPS_KEY)
    if raw is None:
        return list(DEFAULT_PIPELINE_STEPS), [], False
    if not isinstance(raw, list):
        return list(DEFAULT_PIPELINE_STEPS), [
            f"{PIPELINE_STEPS_KEY} 不是列表（{type(raw).__name__}），已回退默认阶段顺序"
        ], False

    steps: List[object] = []
    warnings: List[str] = []
    for item in raw:
        if isinstance(item, str):
            p = item.strip().lower()
            if p in STAGES_BY_KEY or p in AFTER_TRANS_STEP_KEYS:
                if p == "fix":
                    warnings.append(
                        "流水线步骤忽略条目 'fix'：统一问题修复须写成 "
                        "{fix: {types: [...], injectProblem: ...}} 对象条目"
                    )
                    continue
                steps.append(p)
                continue
            warnings.append(f"流水线步骤忽略未知条目：{item!r}")
        elif isinstance(item, dict) and isinstance(item.get("fix"), dict):
            steps.append({"fix": item["fix"]})
        else:
            warnings.append(f"流水线步骤忽略未知条目：{item!r}")

    if not steps:
        return list(DEFAULT_PIPELINE_STEPS), warnings + [
            f"{PIPELINE_STEPS_KEY} 未包含任何有效步骤，已回退默认阶段顺序"
        ], False

    # 译后步骤在翻译前执行没有输入（problem 字段为空、无译文），提示但不阻断；
    # 无翻译阶段的流水线同样适用（译后步骤永远无输入）
    translate_seen = False
    aftertrans_seen_canonicals: set = set()
    for step in steps:
        if step == "translate":
            translate_seen = True
            continue
        if _is_aftertrans_entry(step):
            if not translate_seen:
                label = step if isinstance(step, str) else "fix"
                warnings.append(
                    f"流水线步骤「{label}」位于翻译执行之前（或本流水线无翻译阶段）："
                    f"此时无译文与问题标注，该步骤将检测不到任何待处理句"
                )
            canon = _aftertrans_canonical(step)
            if canon in aftertrans_seen_canonicals:
                warnings.append(
                    "流水线步骤存在完全相同的译后条目重复出现：断点标记按条目内容分组，"
                    "第二次出现将被直接跳过（如需再次执行请调整类型组合）"
                )
            aftertrans_seen_canonicals.add(canon)

    # 高成本阶段重复执行大概率是配置失误（多数有已存在即跳过护栏，少数会真重跑）
    counts: Dict[str, int] = {}
    for step in steps:
        if isinstance(step, str):
            counts[step] = counts.get(step, 0) + 1
    for key, n in counts.items():
        if n > 1 and key in _EXPENSIVE_STAGE_KEYS:
            warnings.append(
                f"流水线步骤「{key}」出现 {n} 次：该阶段重复执行通常无意义，请确认配置"
            )

    # steps 有效时即权威：显式关闭的 enable* 开关会被忽略，提示避免用户困惑
    for stage in PIPELINE_STAGES:
        if projectConfig.getKey(stage.enabled_key, True) is False:
            warnings.append(
                f"已配置 {PIPELINE_STEPS_KEY}，阶段开关 {stage.enabled_key} 不再生效"
                f"（以步骤列表为准）"
            )
            break
    return steps, warnings, True
