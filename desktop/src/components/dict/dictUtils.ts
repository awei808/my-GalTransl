/**
 * Dictionary utility functions — row parsing, formatting, tab/file helpers.
 */
import type { DictFileContent, DictionaryCategory } from "../../lib/api";
import { apiRequest } from "../../lib/api/client";

export type DictRowType = "normal" | "conditional" | "situation" | "gpt" | "forbidden" | "comment" | "blank";

export type ConditionItem = {
  word: string;
  op: "and" | "or" | "";
  negate: boolean;
  startswith: boolean;
  endswith: boolean;
  placeholder: boolean;
};

export type DictRow = {
  type: DictRowType;
  values: string[];
  raw: string;
  // 结构化字段（与后端 parse_dict_line 对齐）
  target?: string | null;
  condItems?: ConditionItem[];
  splWord?: "and" | "or" | "";
  note?: string;
};

function snakeToCamelKey(k: string): string {
  return k.replace(/_([a-z0-9])/g, (_, c) => c.toUpperCase());
}

/**
 * 后端 asdict 默认输出 snake_case，前端 DictRow 用 camelCase；
 * 解析响应时统一做 key 归一化，避免字段名不匹配导致 UI 永远拿不到值。
 */
export function normalizeDictRow(row: Record<string, unknown>): DictRow {
  const out: Record<string, unknown> = {};
  for (const k of Object.keys(row)) {
    out[snakeToCamelKey(k)] = row[k];
  }
  // condItems 内每个子项也做一次 key 归一化（op/word/negate/startswith/endswith/placeholder 本就是单词）
  const condItems = out.condItems as Array<Record<string, unknown>> | undefined;
  if (Array.isArray(condItems)) {
    out.condItems = condItems.map((c) => {
      const obj: Record<string, unknown> = {};
      for (const k of Object.keys(c)) obj[snakeToCamelKey(k)] = c[k];
      return obj;
    });
  }
  return out as DictRow;
}

export type DictRowWithIndex = {
  row: DictRow;
  rowIndex: number;
};

export type DictRowGroup = {
  type: DictRowType;
  items: DictRowWithIndex[];
};

export type DictTab = DictionaryCategory;

export const PROJECT_DIR_MARKER = "(project_dir)";

export function stripProjectDirMarker(name: string): string {
  return name.replace(PROJECT_DIR_MARKER, "").trim();
}

export function getFilesByTab(
  data: {
    dict_contents: Record<string, DictFileContent>;
    pre_dict_files: string[];
    gpt_dict_files: string[];
    post_dict_files: string[];
    h_dict_files?: string[];
    forbidden_dict_files_h?: string[];
    forbidden_dict_files_nh?: string[];
  } | null,
  tab: DictTab,
): string[] {
  if (!data) return [];
  let files: string[];
  if (tab === "pre") files = data.pre_dict_files;
  else if (tab === "gpt") files = data.gpt_dict_files;
  else if (tab === "post") files = data.post_dict_files;
  else if (tab === "forbidden")
    // 合成「禁用词」tab：h 与非 h 字典文件统一展示，通过文件名区分
    files = [...(data.forbidden_dict_files_h ?? []), ...(data.forbidden_dict_files_nh ?? [])];
  else files = data.h_dict_files ?? [];
  return [...files].sort((a, b) => {
    const aMtime = data.dict_contents[a]?.mtime ?? -1;
    const bMtime = data.dict_contents[b]?.mtime ?? -1;
    if (aMtime !== bMtime) return bMtime - aMtime;
    return stripProjectDirMarker(a).localeCompare(stripProjectDirMarker(b));
  });
}

/**
 * 判断字典文件归属 h / 非 h 场景（按文件名后缀约定）。
 * 文件名含 `_h`（无 `_非h`）归 h；否则归非 h（未带后缀默认非 h）。
 */
export function dictFileScene(name: string): "h" | "nh" {
  const lower = name.toLowerCase();
  return lower.includes("_h") && !lower.includes("_非h") ? "h" : "nh";
}

/**
 * 解析字典文本为结构化行。本地解析逻辑已彻底删除，统一调用后端
 * POST /api/dictionaries/parse（实现见 GalTransl.Dictionary.parse_dict_line），
 * 保证前后端解析行为一致。
 */
export async function parseDictContent(
  content: string,
  category: DictTab,
): Promise<DictRow[]> {
  // 合成「禁用词」tab 无后端对应 category；h/非h 解析逻辑一致，统一映射 forbiddenh
  const wireCategory = category === "forbidden" ? "forbiddenh" : category;
  const data = await apiRequest<{ rows: Record<string, unknown>[] }>("/api/dictionaries/parse", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, category: wireCategory }),
  });
  return (data.rows ?? []).map(normalizeDictRow);
}

/**
 * 将行数组序列化为文本（卡片编辑后的保存路径，统一入口）。
 * 每行走 rowToText：conditional 结构化重建（规范化条件列空格、丢弃尾随空备注列）；
 * 其余类型 values.join 保留原样。即"编辑一次即整篇规范化"，属既定行为。
 */
export function rowsToText(rows: DictRow[]): string {
  return rows.map(rowToText).join("\n");
}

export function getTypeLabel(type: DictRowType, _tab: DictTab): string {
  if (type === "comment") return "注释";
  if (type === "blank") return "空行";
  if (type === "gpt") return "GPT";
  if (type === "normal") return "普通";
  if (type === "conditional") return "条件";
  if (type === "situation") return "场景";
  if (type === "forbidden") return "禁用词";
  return type;
}

/**
 * 条件列子项序列化为引擎可识别的字符串（与 GalTransl.Dictionary._serialize_cond_item 对齐）。
 */
export function serializeCondItem(item: ConditionItem): string {
  if (item.placeholder) return "(同上)";
  let w = item.word;
  if (item.startswith) w = `>${w}`;
  if (item.endswith) w = `${w}<`;
  if (item.negate) w = `!${w}`;
  return w;
}

/**
 * 卡片结构化数据 → 文本行。仅当行类型涉及结构化字段时使用，normal/gpt/situation 走 values 兼容路径。
 * 若结构化字段缺失则回退到 values 拼接，保证老数据仍能正常序列化。
 * 注意：条件列由 condItems 重建，会规范化空格（`「 [and]`→`「[and]`）、丢弃尾随空备注列——"保存即规范化"。
 */
export function rowToText(row: DictRow): string {
  if (row.type === "blank") return "";
  if (row.type === "comment") return row.values[0] ?? row.raw;
  if (
    row.type === "gpt" ||
    row.type === "forbidden" ||
    row.type === "normal" ||
    row.type === "situation"
  ) {
    return row.values.join("|");
  }
  // conditional: 用结构化字段重建
  const target = row.target ?? row.values[0] ?? "";
  const condText =
    row.condItems && row.condItems.length > 0
      ? row.condItems
          .map((c, i) => (i === 0 ? serializeCondItem({ ...c, op: "" }) : `[${row.splWord || "or"}]${serializeCondItem(c)}`))
          .join("")
      : row.values[1] ?? "";
  const search = row.values[2] ?? "";
  const replace = row.values[3] ?? "";
  // note 为空但原 rest（values[4]）非空时回退原值，避免非注释的尾随字段被丢弃
  const noteSuffix = row.note
    ? `|//${row.note}`
    : row.values[4] && row.values[4].length > 0
      ? `|${row.values[4]}`
      : "";
  return `${target}|${condText}|${search}|${replace}${noteSuffix}`;
}

export function getFieldLabels(type: DictRowType, tab: DictTab): string[] {
  if (type === "gpt") return ["原文", "译文", "解释(可空)"];
  if (type === "forbidden") return ["词", "备注"];
  if (type === "normal") {
    // h 词库与禁用词字典均为「词|备注」格式
    if (tab === "h" || tab === "forbidden") return ["词", "备注"];
    return ["搜索", "替换", "备注"];
  }
  if (type === "conditional") return ["目标", "条件", "搜索", "替换", "备注"];
  if (type === "situation") return ["场景", "搜索", "替换"];
  if (type === "comment") return ["内容"];
  return [];
}

// ---------- 单句填空卡片：搜索词前缀 & 条件语义 ----------

export type SearchMode = "all" | "first" | "startswith";

export const SEARCH_MODE_OPTIONS: Array<{ value: SearchMode; label: string }> = [
  { value: "all", label: "所有" },
  { value: "first", label: "第一个" },
  { value: "startswith", label: "以…开头" },
];

/**
 * 解析搜索词的引擎前缀。
 * `1^词` → first；`^^词` → startswith；`词` → all。
 */
export function parseSearchPrefix(raw: string): { mode: SearchMode; word: string } {
  if (raw.startsWith("1^")) return { mode: "first", word: raw.slice(2) };
  if (raw.startsWith("^^")) return { mode: "startswith", word: raw.slice(2) };
  return { mode: "all", word: raw };
}

/**
 * 按模式重建搜索词引擎串（与 parseSearchPrefix 互为逆运算）。
 */
export function serializeSearchPrefix(mode: SearchMode, word: string): string {
  if (mode === "first") return `1^${word}`;
  if (mode === "startswith") return `^^${word}`;
  return word;
}

export type CondSemantic = "has" | "not" | "startswith" | "same";

export const COND_SEMANTIC_OPTIONS: Array<{ value: CondSemantic; label: string }> = [
  { value: "has", label: "有" },
  { value: "not", label: "无" },
  { value: "startswith", label: "以…开头" },
  { value: "same", label: "同上" },
];

/**
 * 把条件项标志映射为语义枚举（展示用）。
 */
export function condSemanticOf(item: ConditionItem): CondSemantic {
  if (item.placeholder) return "same";
  if (item.startswith) return "startswith";
  if (item.negate) return "not";
  return "has";
}

/**
 * 把语义枚举应用到条件项（幂等覆盖全部标志，避免残留非法组合）。
 */
export function applyCondSemantic(item: ConditionItem, semantic: CondSemantic): ConditionItem {
  const base: ConditionItem = {
    word: item.word,
    op: item.op,
    negate: false,
    startswith: false,
    endswith: false,
    placeholder: false,
  };
  if (semantic === "has") return base;
  if (semantic === "not") return { ...base, negate: true };
  if (semantic === "startswith") return { ...base, startswith: true };
  return { ...base, placeholder: true, word: "" };
}
