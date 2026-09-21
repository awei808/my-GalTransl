/**
 * stageBackendFields — 「大阶段独立 API」卡片字段清单的派生逻辑。
 *
 * 与后端 pipeline_stages.to_payload() 的 stages[] 对齐：每个阶段的 backend_slot
 * 即 common.stageBackends 的一个键。本模块从后端清单派生卡片清单，
 * 使新增阶段只需改后端一处，前端无需同步硬编码（0.5.0 批次 2）。
 *
 * 依赖仅类型，便于单测直接引用。
 */
import type { PipelineStageInfo } from "./api/types";

export type StageBackendField = {
  key: string;
  label: string;
  desc: string;
  reserved?: boolean;
};

/** 后端不可达时的兜底清单：与 0.4.x 的旧 4 键保持一致，保证界面始终可用。 */
export const LEGACY_STAGE_BACKEND_FIELDS: StageBackendField[] = [
  { key: "translate", label: "翻译执行", desc: "翻译后端：多轮/单轮对话可选（阶段 8）" },
  {
    key: "afterTrans",
    label: "修复和改进译文",
    desc: "阶段 9 全部后处理引擎（改进轮/换行修复/色彩检查/语义检测等）",
  },
  { key: "proofread", label: "人工校对时 AI 精修", desc: "功能预留，当前版本未实现", reserved: true },
  {
    key: "metadata",
    label: "元数据阶段（旧键·回退）",
    desc: "0.4.x 遗留的整体槽位：单独阶段未指定时回退到这里；此处置空则跟随任务主配置",
  },
];

/** 按阶段 key 补充人话说明（后端只给 label 与槽位，说明文案在前端维护）。 */
const PIPELINE_STAGE_BACKEND_DESC: Record<string, string> = {
  global_prompt: "全局游戏分析：整部作品的设定/人物/世界观提炼",
  gen_dic: "术语表生成：从全局分析结果抽取专有名词词典",
  file_meta: "文件级元数据：逐文件生成剧情摘要与人物表（pass1）",
  plot_route: "剧情路线图：梳理分支线路（pass1 剧情路线图）",
  batch_meta: "批次划分：按剧情语义段切分翻译区间（pass2）",
  translate: "翻译执行：多轮/单轮对话可选（阶段 8）",
  afterTrans: "阶段 9 全部后处理引擎（改进轮/换行修复/色彩检查/语义检测等）",
};

/** 旧键槽位：由元数据域各阶段作为回退目标，始终附加在清单末尾。 */
const LEGACY_SLOT_KEYS = new Set(["metadata"]);

/**
 * 由后端阶段清单构造卡片字段清单，顺序即阶段顺序。
 *
 * - 跳过无独立槽位的阶段（validate/compress：一般无需独立后端）
 * - 跳过槽位为旧键（metadata）的阶段：元数据域统一由末尾的旧键条目代表
 * - 末尾追加旧键与预留项，并用 key 去重（防后端清单异常导致重复渲染）
 */
export function buildStageBackendFields(
  stages: PipelineStageInfo[] | undefined | null,
): StageBackendField[] {
  const list = Array.isArray(stages) ? stages : [];
  if (list.length === 0) return [...LEGACY_STAGE_BACKEND_FIELDS];

  const fromPipeline: StageBackendField[] = [];
  for (const s of list) {
    const slot = String(s?.backend_slot ?? "").trim();
    if (!slot || LEGACY_SLOT_KEYS.has(slot)) continue;
    const extra = PIPELINE_STAGE_BACKEND_DESC[s.key] ?? "";
    fromPipeline.push({
      key: slot,
      label: `${s.label}（阶段 ${s.order}）`,
      desc: extra || `后端槽位：${slot}`,
    });
  }

  const seen = new Set<string>();
  const merged = [...fromPipeline, ...LEGACY_STAGE_BACKEND_FIELDS];
  return merged.filter((f) => (seen.has(f.key) ? false : (seen.add(f.key), true)));
}
