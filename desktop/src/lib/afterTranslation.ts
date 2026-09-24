/**
 * AI 初步处理（阶段 7 后处理）后端清单与有序数组配置的解析辅助。
 *
 * gpt.afterTranslation 配置形态：有序数组（[improve, brfix]），数组顺序即执行顺序；
 * 空数组 = 不执行。旧字符串格式（none / improve+brfix）仍兼容读取，统一解析为数组。
 * 统一问题修复后端（ForFixRound）使用对象条目：{"fix": {"types": [...], "injectProblem": true}}，
 * 输入模式由所选问题类型自动推导（见 ForProblemFixRound.set_fix_params），types 为空时
 * 后端运行直接跳过且不执行（见 LLMTranslate._run_after_trans_single_file）。
 * 前后端解析口径一致（后端见 GalTransl/Frontend/LLMTranslate.py 的
 * _resolve_after_translation_order），避免显示与执行不一致。
 *
 * 分组仅为编辑器展示（四个 AI 初步处理子分组），不影响执行顺序：执行顺序仍由
 * 数字框（数组顺序）决定；推荐顺序为 基本问题 → 风格修正 → 色彩检查 → 色彩复核
 * → 疑似错误标注 → 复核。
 */

/** AI 初步处理分组（仅编辑器分组展示用） */
export type AfterTranslationGroup = "basic" | "style" | "tone" | "semcheck";

export interface AfterTranslationBackend {
  /** 配置数组元素值，与后端白名单 key 一致 */
  key: string;
  /** 展示名称 */
  label: string;
  /** 面向零基础用户的说明 */
  hint: string;
  /** 所属分组（编辑器分组标题） */
  group: AfterTranslationGroup;
}

/** 分组标题与说明（顺序即编辑器展示顺序） */
export const AFTER_TRANSLATION_GROUPS: {
  key: AfterTranslationGroup;
  label: string;
  hint: string;
}[] = [
  { key: "basic", label: "基本问题处理", hint: "换行位置、残留日文、禁用词等基本问题修复。" },
  { key: "style", label: "修正翻译风格", hint: "整文件评估译文质量，对可改进句给出备选译文。" },
  { key: "tone", label: "词语色彩一致性检查", hint: "对照批次区间的用词色彩标注检查译文，并可二次复核撤销误报。" },
  { key: "semcheck", label: "标注疑似错误", hint: "AI 判定错译/漏译/串行并标记，供人工复核。" },
];

export const AFTER_TRANSLATION_BACKENDS: AfterTranslationBackend[] = [
  {
    key: "brfix",
    label: "换行修复",
    group: "basic",
    hint: "针对译文内换行位置异常（未紧跟中文标点）的句子生成备选译文。",
  },
  {
    key: "jpfix",
    label: "残留日文修复",
    group: "basic",
    hint: "对照原文清除译文残留的日文假名，生成备选译文。",
  },
  {
    key: "banfix",
    label: "禁用词修复",
    group: "basic",
    hint: "针对标注「用词不当」的译文重新翻译，生成备选译文。",
  },
  {
    key: "fix",
    label: "统一问题修复",
    group: "basic",
    hint: "按所选问题类型组合修复译文（可多选），每类问题按对应修复指令处理，生成备选译文。",
  },
  {
    key: "improve",
    label: "改进轮",
    group: "style",
    hint: "AI 评估整文件译文质量，对可改进的句子给出备选译文（可在校对页一键交换）。",
  },
  {
    key: "tonecheck",
    label: "词语色彩一致性检查",
    group: "tone",
    hint: "对照批次划分标注的「用词色彩」（视角/氛围），标记用词色彩明显不符的句子（不改译文）。需先在完整流水线中生成批次级元数据。",
  },
  {
    key: "tonecheckagain",
    label: "色彩命中句二次复核",
    group: "tone",
    hint: "对词语色彩检查标记的「词语色彩不一致」句子逐句二次复核，撤销可接受译文的误报标记（不改译文）。需先执行词语色彩一致性检查（tonecheck）产生标记，否则无待复核句。",
  },
  {
    key: "semcheck",
    label: "语义差异检测",
    group: "semcheck",
    hint: "用 AI 判定疑似错译、漏译、译文串行，标记「疑似错误」问题（不生成备选译文）。",
  },
  {
    key: "semcheckagain",
    label: "命中句二次复核",
    group: "semcheck",
    hint: "对语义差异检测标记的「疑似错误」句子逐句二次复核，撤销可接受译文的误报标记（不生成备选译文）。需先执行语义差异检测（semcheck）产生标记，否则无待复核句。",
  },
];

/** 统一修复后端参数（对应后端 ForFixRound）；type 而非 interface：需赋给 ConfigValue 的索引签名分支 */
export type FixConfig = {
  /** 组合修复的问题类型白名单（problemAnalyze.problemList 同款类型名）；空数组 = 运行跳过 */
  types: string[];
  /** 是否把 problem 注入输入 JSONL（默认 true） */
  injectProblem: boolean;
};

/** fix 对象条目：统一问题修复后端参数化配置（type 而非 interface：type 具备隐式索引签名，可赋给 ConfigValue） */
export type FixEntry = {
  fix: FixConfig;
};

export type AfterTranslationEntry = string | FixEntry;

/** 是否为受支持的后处理后端 key */
export function isAfterTranslationBackend(key: string): boolean {
  return AFTER_TRANSLATION_BACKENDS.some((b) => b.key === key);
}

/** 是否为 fix 对象条目（统一问题修复后端） */
export function isFixEntry(entry: unknown): entry is FixEntry {
  return (
    typeof entry === "object" &&
    entry !== null &&
    typeof (entry as Record<string, unknown>).fix === "object" &&
    (entry as Record<string, unknown>).fix !== null
  );
}

/** 新建默认 fix 条目 */
export function createFixEntry(): FixEntry {
  return { fix: { types: [], injectProblem: true } };
}

/** 归一化 fix 条目（防御外部配置字段缺省/类型异常；残留 mode 字段直接忽略） */
export function normalizeFixEntry(fix: unknown): FixConfig {
  const cfg = (typeof fix === "object" && fix !== null ? fix : {}) as Record<string, unknown>;
  return {
    types: Array.isArray(cfg.types)
      ? cfg.types.filter((t): t is string => typeof t === "string")
      : [],
    injectProblem: cfg.injectProblem !== false,
  };
}

/**
 * 解析配置值（有序数组或旧字符串 none/improve+brfix）为有序后端条目数组。
 * 数组元素仅保留字符串（白名单内）与 fix 对象条目；同 key 去重保序；
 * "none" 哨兵值忽略。
 */
export function parseAfterTranslation(value: unknown): AfterTranslationEntry[] {
  if (Array.isArray(value)) {
    return filterOrder(value);
  }
  if (typeof value === "string") {
    const s = value.trim().toLowerCase();
    if (!s || s === "none") return [];
    return filterOrder(s.split("+"));
  }
  return [];
}

function filterOrder(parts: unknown[]): AfterTranslationEntry[] {
  const seen = new Set<string>();
  const result: AfterTranslationEntry[] = [];
  for (const part of parts) {
    if (typeof part === "string") {
      const p = part.trim().toLowerCase();
      if (p === "none") continue;
      if (isAfterTranslationBackend(p) && !seen.has(p)) {
        seen.add(p);
        result.push(p);
      }
      continue;
    }
    if (isFixEntry(part) && isAfterTranslationBackend("fix") && !seen.has("fix")) {
      seen.add("fix");
      result.push({ fix: normalizeFixEntry(part.fix) });
    }
  }
  return result;
}

/**
 * 校验有序后端条目：返回 fix 条目 types 为空的警告文案列表（运行时会跳过不执行）。
 */
export function validateAfterTranslation(order: AfterTranslationEntry[]): string[] {
  return order
    .map((entry, i) => ({ entry, step: i + 1 }))
    .filter(
      ({ entry }) => isFixEntry(entry) && entry.fix.types.length === 0,
    )
    .map(
      ({ step }) =>
        `第 ${step} 步「统一问题修复」未选择任何问题类型，运行时将跳过且不执行，请勾选至少一种问题类型`,
    );
}
