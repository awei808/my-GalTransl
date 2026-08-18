import type { CacheEntry, CacheReplaceResponse } from "./api/types";
import type { UndoEntry } from "../stores/undoStore";

/**
 * 从 dryRun 与真实替换响应构造「查找替换」的撤销条目。
 *
 * before 取 dry_run 响应中未修改的原值 entries（后端 dry_run 不落盘、不改值），
 * after 取真实替换后的 entries；仅入栈实际发生变化的条目，避免空操作污染撤销栈。
 */
export function buildReplaceUndoEntries(
  dryRes: CacheReplaceResponse,
  res: CacheReplaceResponse,
): UndoEntry[] {
  const afterByFile = new Map<string, CacheEntry[]>();
  for (const fd of res.file_details) {
    if (fd.entries) afterByFile.set(fd.filename, fd.entries);
  }

  const entries: UndoEntry[] = [];
  for (const fd of dryRes.file_details) {
    if (!fd.entries) continue;
    const afterEntries = afterByFile.get(fd.filename) ?? [];
    for (const e of fd.entries) {
      const after = afterEntries.find((a) => a.index === e.index);
      if (!after || JSON.stringify(after) === JSON.stringify(e)) continue;
      entries.push({
        id: `${fd.filename}:${e.index}`,
        file: fd.filename,
        index: e.index,
        before: e,
        after,
        description: "查找替换",
      });
    }
  }
  return entries;
}
