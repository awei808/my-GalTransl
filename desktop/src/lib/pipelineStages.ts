/**
 * 流水线阶段的前端常量（唯一真源在后端 GalTransl/Frontend/pipeline_stages.py）。
 *
 * 这里只保留两样东西：
 * 1. 后端不可用时的兜底清单（FALLBACK_STAGES）——离线/后端未起时向导仍可渲染；
 * 2. 由清单派生的「阶段开关短名」列表，供写回 config.yaml 时补全全量键。
 *
 * 运行时优先使用 `fetchPipelineStages()` 的返回结果；
 * 本文件的常量仅在请求失败时作为降级，不应作为业务判断依据。
 */
import type { PipelineStageInfo } from "./api/types";

/** 后端不可用时的兜底阶段清单（与后端 pipeline_stages.py 同序同名） */
export const FALLBACK_STAGES: PipelineStageInfo[] = [
  {
    key: "validate",
    label: "输入数据校验",
    order: 0,
    display_label: "阶段 0：输入数据校验",
    index: 0,
    enabled_key: "internals.pipeline.enableValidate",
    backend_slot: "",
    depends_on: [],
    backend_names: [],
    sample_key: "",
    needs_compressed_text: false,
  },
  {
    key: "compress",
    label: "文本无损压缩",
    order: 1,
    display_label: "阶段 1：文本无损压缩",
    index: 1,
    enabled_key: "internals.pipeline.enableCompress",
    backend_slot: "",
    depends_on: [],
    backend_names: [],
    sample_key: "",
    needs_compressed_text: false,
  },
  {
    key: "global_prompt",
    label: "全局游戏分析",
    order: 2,
    display_label: "阶段 2：全局游戏分析",
    index: 2,
    enabled_key: "internals.pipeline.enableGlobalPrompt",
    backend_slot: "metadata",
    depends_on: ["compress"],
    backend_names: ["ForGlobalPrompt"],
    sample_key: "GlobalPrompt.json",
    needs_compressed_text: true,
  },
  {
    key: "gen_dic",
    label: "术语表构建",
    order: 3,
    display_label: "阶段 3：术语表构建",
    index: 3,
    enabled_key: "internals.pipeline.enableGenDic",
    backend_slot: "metadata",
    depends_on: [],
    backend_names: ["GenDic"],
    sample_key: "",
    needs_compressed_text: false,
  },
  {
    key: "file_meta",
    label: "文件级元数据",
    order: 4,
    display_label: "阶段 4：文件级元数据",
    index: 4,
    enabled_key: "internals.pipeline.enableFileMeta",
    backend_slot: "metadata",
    depends_on: [],
    backend_names: ["ForFileMetaData"],
    sample_key: "FileMetaData.json",
    needs_compressed_text: false,
  },
  {
    key: "plot_route",
    label: "剧情路线图",
    order: 5,
    display_label: "阶段 5：剧情路线图",
    index: 5,
    enabled_key: "internals.pipeline.enablePlotRoute",
    backend_slot: "metadata",
    depends_on: ["file_meta"],
    backend_names: ["ForPlotRouteMap"],
    sample_key: "PlotRouteMap.json",
    needs_compressed_text: false,
  },
  {
    key: "batch_meta",
    label: "批次级元数据",
    order: 6,
    display_label: "阶段 6：批次级元数据",
    index: 6,
    enabled_key: "internals.pipeline.enableBatchMeta",
    backend_slot: "metadata",
    depends_on: [],
    backend_names: ["ForBatchMetaData"],
    sample_key: "BatchMetadata.json",
    needs_compressed_text: false,
  },
  {
    key: "translate",
    label: "翻译执行",
    order: 7,
    display_label: "阶段 7：翻译执行",
    index: 7,
    enabled_key: "internals.pipeline.enableTranslate",
    backend_slot: "translate",
    depends_on: [],
    backend_names: ["ForGalJsonTranslate"],
    sample_key: "",
    needs_compressed_text: false,
  },
  {
    key: "improve",
    label: "修复和改进译文",
    order: 8,
    display_label: "阶段 8：修复和改进译文",
    index: 8,
    enabled_key: "internals.pipeline.enableImprove",
    backend_slot: "afterTrans",
    depends_on: [],
    backend_names: [],
    sample_key: "",
    needs_compressed_text: false,
  },
];

/** 由 `internals.pipeline.enableXxx` 取末段短名（enableXxx） */
export function stageToggleName(stage: PipelineStageInfo): string {
  const parts = stage.enabled_key.split(".");
  return parts[parts.length - 1];
}

/** 阶段开关短名列表（如 ["enableValidate", ...]），顺序与清单一致 */
export const DEFAULT_STAGE_ENABLED_KEYS: string[] = FALLBACK_STAGES.map(stageToggleName);

/** 阶段开关的默认值（全部开启） */
export const DEFAULT_STAGE_ENABLED: Record<string, boolean> = Object.fromEntries(
  DEFAULT_STAGE_ENABLED_KEYS.map((k) => [k, true]),
);
