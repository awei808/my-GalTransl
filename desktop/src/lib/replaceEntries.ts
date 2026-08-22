/**
 * 查找替换纯函数：仅修改内存中的缓存条目，不落盘。
 * 供查找替换侧边栏「文件内全部替换 / 替换单个」走纯前端路径使用
 * （只改校对页当前打开文件的内存 entries，标脏保存后才写盘）。
 */
import type { CacheEntry, CacheReplaceField } from "./api/types";

/** 一条被替换的条目快照（完整条目，供撤销栈 before/after 使用） */
export interface ReplaceChange {
  index: number;
  before: CacheEntry;
  after: CacheEntry;
}

export interface ReplaceInEntriesResult {
  entries: CacheEntry[];
  changed: ReplaceChange[];
}

/** 按主键优先、历史兼容别名兜底解析参与替换的文本键；无文本值返回 null */
function pickTextKey(
  e: CacheEntry,
  primary: keyof CacheEntry,
  legacy: keyof CacheEntry,
): keyof CacheEntry | null {
  if (typeof e[primary] === "string") return primary;
  if (typeof e[legacy] === "string") return legacy;
  return null;
}

/** 按字段口径解析一条条目上参与替换的文本键（与后端 /cache/replace 一致） */
function resolveReplaceKeys(e: CacheEntry, field: CacheReplaceField): Array<keyof CacheEntry> {
  const keys: Array<keyof CacheEntry> = [];
  if (field === "src" || field === "all") {
    const k = pickTextKey(e, "post_src", "post_jp");
    if (k) keys.push(k);
  }
  if (field === "dst" || field === "all") {
    const k1 = pickTextKey(e, "pre_dst", "pre_zh");
    if (k1) keys.push(k1);
    const k2 = pickTextKey(e, "proofread_dst", "proofread_zh");
    if (k2 && !keys.includes(k2)) keys.push(k2);
  }
  return keys;
}

/**
 * 在内存条目列表中执行查找替换。
 * - 命中判断 `text.includes(query)`，替换用 `String.replace`（只替换第一处，与后端一致）；
 * - opts.onlyIndex 指定时仅处理该 index 的条目（「替换单个」）；
 * - 返回替换后的 entries 与实际变化条目的 before/after 完整快照。
 */
export function replaceInEntries(
  entries: CacheEntry[],
  query: string,
  replacement: string,
  field: CacheReplaceField,
  opts?: { onlyIndex?: number },
): ReplaceInEntriesResult {
  if (!query) return { entries, changed: [] };
  const next: CacheEntry[] = [];
  const changed: ReplaceChange[] = [];
  for (const e of entries) {
    if (opts?.onlyIndex !== undefined && e.index !== opts.onlyIndex) {
      next.push(e);
      continue;
    }
    const keys = resolveReplaceKeys(e, field);
    if (keys.length === 0) {
      next.push(e);
      continue;
    }
    let entry: CacheEntry = e;
    let modified = false;
    for (const k of keys) {
      const text = String(entry[k] ?? "");
      if (!text.includes(query)) continue;
      if (!modified) {
        entry = { ...entry };
        modified = true;
      }
      // k 经 pickTextKey 限定为字符串字段；keyof CacheEntry 联合类型无法直接索引赋值，作 Record 写入
      const record = entry as unknown as Record<string, unknown>;
      record[k] = text.replace(query, replacement);
    }
    if (modified) changed.push({ index: e.index, before: e, after: entry });
    next.push(entry);
  }
  return { entries: next, changed };
}
