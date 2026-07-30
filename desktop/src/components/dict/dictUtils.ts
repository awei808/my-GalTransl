/**
 * Dictionary utility functions — row parsing, formatting, tab/file helpers.
 */
import type { DictFileContent, DictionaryCategory } from "../../lib/api";
import { apiRequest } from "../../lib/api/client";

export type DictRowType = "normal" | "conditional" | "situation" | "gpt" | "comment" | "blank";

export type DictRow = {
  type: DictRowType;
  values: string[];
  raw: string;
};

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
  } | null,
  tab: DictTab,
): string[] {
  if (!data) return [];
  const files =
    tab === "pre"
      ? data.pre_dict_files
      : tab === "gpt"
        ? data.gpt_dict_files
        : data.post_dict_files;
  return [...files].sort((a, b) => {
    const aMtime = data.dict_contents[a]?.mtime ?? -1;
    const bMtime = data.dict_contents[b]?.mtime ?? -1;
    if (aMtime !== bMtime) return bMtime - aMtime;
    return stripProjectDirMarker(a).localeCompare(stripProjectDirMarker(b));
  });
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
  const data = await apiRequest<{ rows: DictRow[] }>("/api/dictionaries/parse", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, category }),
  });
  return data.rows ?? [];
}

export function rowsToText(rows: DictRow[]): string {
  return rows
    .map((row) => {
      if (row.type === "blank") return "";
      if (row.type === "comment") return row.values[0] ?? row.raw;
      return row.values.join("|");
    })
    .join("\n");
}

export function getTypeLabel(type: DictRowType, _tab: DictTab): string {
  if (type === "comment") return "注释";
  if (type === "blank") return "空行";
  if (type === "gpt") return "GPT";
  if (type === "normal") return "普通";
  if (type === "conditional") return "条件";
  if (type === "situation") return "场景";
  return type;
}

export function getFieldLabels(type: DictRowType, _tab: DictTab): string[] {
  if (type === "gpt") return ["原文", "译文", "解释(可空)"];
  if (type === "normal") return ["搜索", "替换", "备注"];
  if (type === "conditional") return ["目标", "条件", "搜索", "替换", "备注"];
  if (type === "situation") return ["场景", "搜索", "替换"];
  if (type === "comment") return ["内容"];
  return [];
}
