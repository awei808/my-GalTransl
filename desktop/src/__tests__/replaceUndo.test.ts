/**
 * 查找替换撤销条目构造（buildReplaceUndoEntries）单元测试
 * 覆盖 H5：before 取 dry_run 原值、after 取替换后值、仅入栈实际变化的条目、
 * dry_run 无 entries 时跳过、多文件/多条目顺序稳定。
 */
import { describe, it, expect } from "vitest";

import { buildReplaceUndoEntries } from "../lib/replaceUndo";
import type { CacheReplaceResponse } from "../lib/api/types";

function makeResponse(
  dryRun: boolean,
  details: Array<{ filename: string; matches: number; entries?: Array<Record<string, unknown>> }>,
): CacheReplaceResponse {
  const total = details.reduce((acc, d) => acc + d.matches, 0);
  return {
    success: true,
    total_matches: total,
    total_files: details.length,
    dry_run: dryRun,
    file_details: details.map((d) => ({
      filename: d.filename,
      matches: d.matches,
      entries: d.entries as CacheReplaceResponse["file_details"][number]["entries"],
    })),
  };
}

describe("buildReplaceUndoEntries", () => {
  it("before 取 dryRun 原值，after 取替换后值", () => {
    const dryRes = makeResponse(true, [
      {
        filename: "a.txt.json",
        matches: 1,
        entries: [{ index: 1, pre_dst: "旧译文", proofread_dst: "旧校对" }],
      },
    ]);
    const res = makeResponse(false, [
      {
        filename: "a.txt.json",
        matches: 1,
        entries: [{ index: 1, pre_dst: "新译文", proofread_dst: "新校对" }],
      },
    ]);
    const entries = buildReplaceUndoEntries(dryRes, res);
    expect(entries).toHaveLength(1);
    expect(entries[0].id).toBe("a.txt.json:1");
    expect(entries[0].index).toBe(1);
    expect(entries[0].before).toEqual({ index: 1, pre_dst: "旧译文", proofread_dst: "旧校对" });
    expect(entries[0].after).toEqual({ index: 1, pre_dst: "新译文", proofread_dst: "新校对" });
  });

  it("未变化的条目不入栈", () => {
    const dryRes = makeResponse(true, [
      {
        filename: "a.txt.json",
        matches: 1,
        entries: [
          { index: 1, pre_dst: "旧译文" },
          { index: 2, pre_dst: "未命中" },
        ],
      },
    ]);
    const res = makeResponse(false, [
      {
        filename: "a.txt.json",
        matches: 1,
        entries: [
          { index: 1, pre_dst: "新译文" },
          { index: 2, pre_dst: "未命中" },
        ],
      },
    ]);
    const entries = buildReplaceUndoEntries(dryRes, res);
    expect(entries).toHaveLength(1);
    expect(entries[0].index).toBe(1);
  });

  it("dryRun 无 entries 时跳过该文件", () => {
    const dryRes = makeResponse(true, [{ filename: "a.txt.json", matches: 1 }]);
    const res = makeResponse(false, [
      { filename: "a.txt.json", matches: 1, entries: [{ index: 1, pre_dst: "新译文" }] },
    ]);
    expect(buildReplaceUndoEntries(dryRes, res)).toHaveLength(0);
  });

  it("多文件条目全部入栈且顺序稳定", () => {
    const dryRes = makeResponse(true, [
      { filename: "a.txt.json", matches: 1, entries: [{ index: 1, pre_dst: "旧A" }] },
      { filename: "b.txt.json", matches: 1, entries: [{ index: 2, pre_dst: "旧B" }] },
    ]);
    const res = makeResponse(false, [
      { filename: "a.txt.json", matches: 1, entries: [{ index: 1, pre_dst: "新A" }] },
      { filename: "b.txt.json", matches: 1, entries: [{ index: 2, pre_dst: "新B" }] },
    ]);
    const entries = buildReplaceUndoEntries(dryRes, res);
    expect(entries.map((e) => e.id)).toEqual(["a.txt.json:1", "b.txt.json:2"]);
  });
});
