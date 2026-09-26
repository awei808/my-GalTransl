/**
 * 路线图工作台执行计划的纯逻辑：指令/注入开关定义与提交载荷构造。
 * 与 UI 解耦，便于单测（buildJobExtras 的映射口径是任务提交正确性的核心）。
 */

export type FilesMode = "selected" | "all" | "custom";
export type ToggleValue = "default" | "on" | "off";

export interface InstructionDef {
  id: string;
  label: string;
  description: string;
}

/** 工作台可执行的翻译流程后端（指令 -> 引擎 ID） */
export const INSTRUCTIONS: InstructionDef[] = [
  { id: "ForGal-json-translate", label: "翻译", description: "主翻译后端（多轮/单轮对话）" },
  { id: "ForGal-full-pipeline", label: "完整流水线", description: "按需自动串联 0-7 各阶段" },
  { id: "ForFileMetaData", label: "文件元数据", description: "生成 pass1 文件级元数据" },
  { id: "ForBatchMetaData", label: "批次划分", description: "生成 pass2 批次级元数据" },
  { id: "ForGlobalPrompt", label: "全局分析", description: "生成全局游戏分析" },
  { id: "ForPlotRouteMap", label: "重生成路线图", description: "基于文件元数据重新生成剧情路线图（始终全项目，忽略文件范围）" },
  { id: "ForImproveTranslation", label: "译文改进", description: "为可改进句生成备选译文" },
  { id: "ForBRStation", label: "换行修复", description: "为换行异常句生成备选译文" },
  { id: "ForJPResidue", label: "残留修复", description: "为残留日文句生成备选译文" },
  { id: "ForBanWordFix", label: "用词修复", description: "为用词不当句生成备选译文" },
  { id: "ForSemCheck", label: "语义复核", description: "标记疑似语义错误" },
  { id: "ForSemCheckAgain", label: "二次复核", description: "复核疑似错误并剔除误报" },
  { id: "ForFixRound", label: "统一修复", description: "按 gpt.afterTranslation 的 fix 条目组合修复" },
];

/** 注入开关 -> 配置键映射（「跟随项目配置」时不写入 config_overrides） */
export const INJECTION_TOGGLES: { key: string; label: string }[] = [
  { key: "internals.promptBlocks.globalPrompt", label: "全局分析+路线图剧情" },
  { key: "internals.promptBlocks.plotMetadata", label: "文件元数据" },
  { key: "internals.promptBlocks.batchMetadata", label: "批次元数据" },
  { key: "internals.promptBlocks.glossary", label: "GPT字典术语表" },
  { key: "internals.promptBlocks.translationGuideline", label: "翻译规范" },
  { key: "internals.forglobalprompt.inject_name_table", label: "人名表（全局分析）" },
];

export interface InstructionConfig {
  filesMode: FilesMode;
  customFilesText: string;
  injections: Record<string, ToggleValue>;
  advancedText: string;
}

export function defaultConfig(): InstructionConfig {
  return {
    filesMode: "selected",
    customFilesText: "",
    injections: {},
    advancedText: "",
  };
}

/** 忽略文件范围的指令：输入来自元数据缓存而非输入文件枚举，下发 file_filter 会被静默忽略 */
const FILE_FILTER_IGNORED_IDS = new Set<string>(["ForPlotRouteMap"]);

/** 按文件范围口径解析 file_filter；全部文件 / 被忽略的指令返回 undefined（缺省=全项目） */
export function buildFileFilter(
  translatorId: string,
  cfg: InstructionConfig,
  selectedFiles: string[],
): string[] | undefined {
  if (FILE_FILTER_IGNORED_IDS.has(translatorId)) return undefined;
  if (cfg.filesMode === "all") return undefined;
  if (cfg.filesMode === "custom") {
    return cfg.customFilesText
      .split(/[\n,，]/)
      .map((s) => s.trim())
      .filter(Boolean);
  }
  return selectedFiles.slice();
}

/** 与后端 apply_job_config_overrides 同口径的值类型校验：标量或标量/字典列表，dict 值拒绝 */
function assertOverrideValue(key: string, value: unknown): void {
  if (value === null || ["string", "number", "boolean"].includes(typeof value)) return;
  if (Array.isArray(value)) {
    for (const item of value) {
      if (item === null || ["string", "number", "boolean"].includes(typeof item)) continue;
      if (typeof item === "object" && !Array.isArray(item)) continue;
      throw new Error(`config_overrides[${key}] 列表中含非法元素类型（仅支持标量或字典）`);
    }
    return;
  }
  throw new Error(`config_overrides[${key}] 值类型非法（仅支持标量或列表）`);
}

/** 汇总注入覆盖与高级 JSON 覆盖；JSON 非法或值类型越界时抛错由调用方提示 */
export function buildOverrides(cfg: InstructionConfig): Record<string, unknown> {
  const overrides: Record<string, unknown> = {};
  for (const { key } of INJECTION_TOGGLES) {
    const value = cfg.injections[key] ?? "default";
    if (value === "on") overrides[key] = true;
    else if (value === "off") overrides[key] = false;
  }
  const text = cfg.advancedText.trim();
  if (text) {
    const parsed = JSON.parse(text) as Record<string, unknown>;
    for (const [key, value] of Object.entries(parsed)) {
      assertOverrideValue(key, value);
      overrides[key] = value;
    }
  }
  return overrides;
}

/** 构造提交 /api/jobs 时的范围扩展字段；空文件范围返回 null 由调用方拦截提示 */
export function buildJobExtras(
  translatorId: string,
  cfg: InstructionConfig,
  selectedFiles: string[],
): { file_filter?: string[]; config_overrides?: Record<string, unknown> } | null {
  const fileFilter = buildFileFilter(translatorId, cfg, selectedFiles);
  if (fileFilter && fileFilter.length === 0) return null;
  const overrides = buildOverrides(cfg);
  return {
    ...(fileFilter ? { file_filter: fileFilter } : {}),
    ...(Object.keys(overrides).length > 0 ? { config_overrides: overrides } : {}),
  };
}
