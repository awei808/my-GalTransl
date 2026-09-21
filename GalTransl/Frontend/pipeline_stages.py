"""流水线阶段清单（唯一真源）。

完整流水线的阶段顺序、配置开关、后端槽位与依赖关系集中定义在此，
供三处复用：
1. 后端编排（`Frontend/LLMTranslate.py:_run_full_pipeline`）——按 `order` 遍历执行；
2. 服务端接口（`GET /api/pipeline-stages`）——向任意客户端暴露同一份清单；
3. 前端设置界面（项目设置页 / 新建向导）——渲染阶段开关，不再各自硬编码。

约束：本模块**不得**导入重依赖（后端引擎、CProjectConfig 等），
只依赖标准库，以便服务端与测试轻量引用。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


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
        key="global_prompt",
        label="全局游戏分析",
        order=2,
        enabled_key=_enabled_key("enableGlobalPrompt"),
        backend_slot="metadata",
        depends_on=("compress",),
        backend_names=("ForGlobalPrompt",),
        sample_key="GlobalPrompt.json",
        needs_compressed_text=True,
    ),
    PipelineStage(
        key="gen_dic",
        label="术语表构建",
        order=3,
        enabled_key=_enabled_key("enableGenDic"),
        backend_slot="metadata",
        backend_names=("GenDic",),
    ),
    PipelineStage(
        key="file_meta",
        label="文件级元数据",
        order=4,
        enabled_key=_enabled_key("enableFileMeta"),
        backend_slot="metadata",
        backend_names=("ForFileMetaData",),
        sample_key="FileMetaData.json",
    ),
    PipelineStage(
        key="plot_route",
        label="剧情路线图",
        order=5,
        enabled_key=_enabled_key("enablePlotRoute"),
        backend_slot="metadata",
        depends_on=("file_meta",),
        backend_names=("ForPlotRouteMap",),
        sample_key="PlotRouteMap.json",
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
    }
