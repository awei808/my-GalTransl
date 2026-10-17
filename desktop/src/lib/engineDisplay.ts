/**
 * engineDisplay — 翻译后端（引擎）在选择器中的展示元数据与派生逻辑。
 *
 * 值的口径不变：选项 value 始终是引擎名（`/api/translators` 返回的 name），
 * 本模块只补「中文短名 / 简短说明 / 分组 / 组内顺序」这些纯展示信息，
 * 供翻译控制台的后端下拉渲染与过滤使用。
 *
 * 后端清单的唯一真源仍是 `GalTransl/__init__.py` 的 TRANSLATOR_SUPPORTED
 * （经 `/api/translators` 下发）。未收录的引擎不会消失也不会报错：
 * 短名回退为引擎名、说明回退为后端给的长描述、分组归入「其它」。
 * 新增引擎时同步本文件的 ENGINE_LABELS / ENGINE_HINTS / ENGINE_GROUPS / ENGINE_ORDER，
 * 否则该引擎会显示在后处理组之后的「其它」组里（可用但不好找）。
 */
import type { TranslatorOption } from "./api/types";

export type EngineGroupId = "translate" | "analysis" | "postprocess" | "other";

/** 分组显示名（组的先后顺序即 ENGINE_GROUP_ORDER） */
export const ENGINE_GROUP_LABELS: Record<EngineGroupId, string> = {
  translate: "翻译执行",
  analysis: "分析与元数据",
  postprocess: "后处理与校对",
  other: "其它",
};

export const ENGINE_GROUP_ORDER: EngineGroupId[] = [
  "translate",
  "analysis",
  "postprocess",
  "other",
];

/** 中文短名：与「取最好效果」的流程位置对应，面向零基础用户 */
export const ENGINE_LABELS: Record<string, string> = {
  "ForGal-full-pipeline": "完整流水线",
  "ForGal-json-translate": "翻译执行",
  ForGlobalPrompt: "全局分析",
  ForRouteAnalysis: "路线分析",
  ForGlobalAnalysis: "全局分析汇总",
  GenDic: "术语表生成",
  ForFileMetaData: "文件元数据",
  ForPlotRouteMap: "剧情路线图",
  ForBatchMetaData: "批次划分",
  ForImproveTranslation: "译文改进",
  ForFixRound: "统一修复",
  ForBRStation: "换行修复",
  ForJPResidue: "残留日文修复",
  ForBanWordFix: "禁用词修复",
  ForSemCheck: "语义检测",
  ForSemCheckAgain: "语义复核",
  ForToneCheck: "色彩检测",
  ForToneCheckAgain: "色彩复核",
};

/**
 * 单行短说明：宽度只够一行，控制在约 26 个汉字内，超出由 CSS 省略号收尾，
 * 完整描述与补充细节走 title 悬浮（见 engineTitle）。
 */
export const ENGINE_HINTS: Record<string, string> = {
  "ForGal-full-pipeline": "一键跑完全流程（不建议日常启用）",
  "ForGal-json-translate": "按批次翻译原文，建议最后执行本后端",
  ForGlobalPrompt: "AI 通读全文，生成世界观与角色档案",
  ForRouteAnalysis: "按剧情路线逐条做局部游戏分析",
  ForGlobalAnalysis: "把各路线分析汇总为全局游戏分析",
  GenDic: "AI 自动生成 gpt 字典",
  ForFileMetaData: "AI 分析每个文件的剧情与角色",
  ForPlotRouteMap: "由文件元数据生成剧情路线图",
  ForBatchMetaData: "按剧情划分批次，生成指导语言风格",
  ForImproveTranslation: "为可改进的句子给出备选译文",
  ForFixRound: "按所选问题类型组合修复译文",
  ForBRStation: "为换行位置异常的句子生成备选译文",
  ForJPResidue: "为残留日文的句子生成备选译文",
  ForBanWordFix: "为用词不当的句子生成备选译文",
  ForSemCheck: "标记语义差异过大的疑似错误句",
  ForSemCheckAgain: "复核疑似错误标记，剔除误报",
  ForToneCheck: "标记用词色彩偏离基准的句子",
  ForToneCheckAgain: "复核色彩不一致标记，剔除误报",
};

/** 引擎 → 分组（未登记归 other） */
const ENGINE_GROUPS: Record<string, EngineGroupId> = {
  "ForGal-full-pipeline": "translate",
  "ForGal-json-translate": "translate",
  ForGlobalPrompt: "analysis",
  ForRouteAnalysis: "analysis",
  ForGlobalAnalysis: "analysis",
  GenDic: "analysis",
  ForFileMetaData: "analysis",
  ForPlotRouteMap: "analysis",
  ForBatchMetaData: "analysis",
  ForImproveTranslation: "postprocess",
  ForFixRound: "postprocess",
  ForBRStation: "postprocess",
  ForJPResidue: "postprocess",
  ForBanWordFix: "postprocess",
  ForSemCheck: "postprocess",
  ForSemCheckAgain: "postprocess",
  ForToneCheck: "postprocess",
  ForToneCheckAgain: "postprocess",
};

/**
 * 组内顺序：面向使用的聚类顺序（作品级分析 → 术语表 → 文件级 → 批次级），
 * 不是流水线执行顺序（后者见 GalTransl/Frontend/pipeline_stages.py，其阶段序为
 * 术语表 → 文件元数据 → 路线图 → 全局分析 → 批次 → 翻译）。
 *
 * 「完整流水线」刻意排在首位：它与后端 `/api/translators` 下发列表的首项一致，
 * 而控制台在无选中值时取列表首项作为默认（ensureBackendSelection），
 * 因此默认后端与改动前保持同一引擎。
 */
const ENGINE_ORDER: Record<EngineGroupId, string[]> = {
  translate: ["ForGal-full-pipeline", "ForGal-json-translate"],
  analysis: [
    "ForGlobalPrompt",
    "ForRouteAnalysis",
    "ForGlobalAnalysis",
    "GenDic",
    "ForFileMetaData",
    "ForPlotRouteMap",
    "ForBatchMetaData",
  ],
  postprocess: [
    "ForImproveTranslation",
    "ForFixRound",
    "ForBRStation",
    "ForJPResidue",
    "ForBanWordFix",
    "ForSemCheck",
    "ForSemCheckAgain",
    "ForToneCheck",
    "ForToneCheckAgain",
  ],
  other: [],
};

/** 中文短名；未登记返回空串（调用方据此回退到引擎名） */
export function engineLabel(name: string): string {
  return ENGINE_LABELS[name] ?? "";
}

/** 触发框与选项里显示的短名，未登记回退引擎名 */
export function engineShortName(name: string): string {
  return engineLabel(name) || name;
}

/** 单行短说明；未登记回退后端长描述（可见部分由 CSS 省略，完整内容在 title） */
export function engineHint(name: string, description = ""): string {
  return ENGINE_HINTS[name] ?? description;
}

/**
 * 悬浮提示：短说明 + 后端长描述（两者相同时不重复），并始终带上引擎名，
 * 便于用户与日志、首页任务列表里的引擎名对上号。
 */
export function engineTitle(name: string, description = ""): string {
  const hint = engineHint(name, description);
  return [name, hint === description ? "" : hint, description].filter(Boolean).join("\n");
}

/** 引擎所属分组，未登记归 other */
export function engineGroup(name: string): EngineGroupId {
  return ENGINE_GROUPS[name] ?? "other";
}

/**
 * 查询过滤：空白分词后为 AND 语义，每个词需命中 引擎名 / 中文短名 / 短说明 / 后端描述
 * 之一；空查询返回原列表。不改变原数组。
 */
export function filterEngines(list: TranslatorOption[], query: string): TranslatorOption[] {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (terms.length === 0) return list;
  return list.filter((t) => {
    const haystack = [
      t.name,
      engineLabel(t.name),
      engineHint(t.name, t.description),
      t.description,
    ]
      .join("\n")
      .toLowerCase();
    return terms.every((term) => haystack.includes(term));
  });
}

export type EngineGroup = {
  id: EngineGroupId;
  label: string;
  items: TranslatorOption[];
};

/**
 * 分组 + 组内排序，供下拉直接渲染。
 * 只输出非空分组；未登记在 ENGINE_ORDER 的引擎追加在所属组末尾，保持后端下发顺序。
 */
export function groupEngines(list: TranslatorOption[]): EngineGroup[] {
  const apiIndex = new Map(list.map((t, i) => [t.name, i]));
  const buckets = new Map<EngineGroupId, TranslatorOption[]>();
  for (const id of ENGINE_GROUP_ORDER) buckets.set(id, []);
  for (const t of list) buckets.get(engineGroup(t.name))!.push(t);

  const groups: EngineGroup[] = [];
  for (const id of ENGINE_GROUP_ORDER) {
    const items = buckets.get(id)!;
    if (items.length === 0) continue;
    const order = ENGINE_ORDER[id];
    const rank = (name: string) => {
      const i = order.indexOf(name);
      return i >= 0 ? i : order.length + (apiIndex.get(name) ?? 0);
    };
    items.sort((a, b) => rank(a.name) - rank(b.name));
    groups.push({ id, label: ENGINE_GROUP_LABELS[id], items });
  }
  return groups;
}

/** 分组结果摊平为渲染顺序的一维列表（键盘上下移动与过滤结果定位共用同一口径） */
export function flattenGroups(groups: EngineGroup[]): TranslatorOption[] {
  return groups.flatMap((g) => g.items);
}
