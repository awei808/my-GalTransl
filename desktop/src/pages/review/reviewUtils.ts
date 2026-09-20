/**
 * 校对审核页的过滤与显示工具（0.4.10 从 ReviewPage.tsx 抽出）。
 *
 * 提供问题类型过滤、换行控制符可见化、以及 CacheEntry 字段元数据
 * （可编辑字段集合与 18 字段中文标签），供 EntryCard 与主组件共用。
 */
import type { CacheEntry } from "../../lib/api/types";
import { problemTypesOf } from "../../lib/problems";

/**
 * 按勾选的问题类型列表过滤条目（多选 AND 语义：需同时包含所有勾选类型）。
 * types 为空数组时不做过滤（等价「全部类型」）。
 */
export function applyProblemTypeFilter(
  list: CacheEntry[],
  types: string[],
): CacheEntry[] {
  if (types.length === 0) return list;
  return list.filter((e) => types.every((t) => problemTypesOf(e.problem).includes(t)));
}

/** 元数据键值编辑器的输入元素（键 input / 值 textarea）：走纯原生撤销（方案 A） */
export function toVisibleNewlines(s: unknown): string {
  if (s == null) return "";
  return String(s)
    .replace(/\r\n/g, "\\r\\n")
    .replace(/\n/g, "\\n")
    .replace(/\r/g, "\\r");
}

// 键盘快捷键分派（可导出纯函数，便于单元测试）
/* 展开字段中可编辑的译文字段（其余字段只读展示） */
export const EDITABLE_FIELD_KEYS: ReadonlySet<keyof CacheEntry> = new Set(["pre_dst", "proofread_dst", "alt_dst"]);

/* CacheEntry 18 字段的中文标签 */
export const ALL_FIELDS: Array<{ key: keyof CacheEntry; label: string }> = [
  { key: "index", label: "索引" },
  { key: "name", label: "说话人" },
  { key: "pre_src", label: "译前原文" },
  { key: "post_src", label: "译后原文" },
  { key: "pre_dst", label: "译前译文" },
  { key: "proofread_dst", label: "校对译文" },
  { key: "alt_dst", label: "备选译文" },
  { key: "trans_by", label: "翻译引擎" },
  { key: "proofread_by", label: "校对者" },
  { key: "problem", label: "问题" },
  { key: "trans_conf", label: "翻译置信度" },
  { key: "doub_content", label: "存疑内容" },
  { key: "unknown_proper_noun", label: "未知专名" },
  { key: "pre_jp", label: "预处理日语" },
  { key: "post_jp", label: "后处理日语" },
  { key: "pre_zh", label: "预处理中文" },
  { key: "proofread_zh", label: "校对中文" },
  { key: "post_zh_preview", label: "后处理中文预览" },
  { key: "post_dst_preview", label: "后处理译文预览" },
];
