/**
 * computeHRangeBoundaries（H 区间分割线边界计算）测试。
 *
 * 覆盖：页内完整命中 / 区间整体在上一页 / 区间整体在下一页 /
 * 区间跨页首尾各半 / 多区间分离 / 空区间列表 / 过滤后无交集。
 */
import { describe, it, expect } from "vitest";
import { computeHRangeBoundaries } from "../pages/review/ReviewPage";
import type { CacheEntry, CacheHRange } from "../lib/api/types";

function makeEntries(indices: number[]): CacheEntry[] {
  return indices.map((index) => ({ index } as CacheEntry));
}

const RANGE_800_900: CacheHRange = { lo: 800, hi: 900 };

describe("computeHRangeBoundaries", () => {
  it("页内含完整区间时，首条画开始线、末条画结束线", () => {
    const { starts, ends } = computeHRangeBoundaries(
      makeEntries([801, 802, 803]),
      [RANGE_800_900],
    );
    expect(starts.has(801)).toBe(true);
    expect(ends.has(803)).toBe(true);
    expect(starts.size).toBe(1);
    expect(ends.size).toBe(1);
  });

  it("区间整体在上一页（页内无交集）时不画任何线", () => {
    const { starts, ends } = computeHRangeBoundaries(
      makeEntries([700, 701, 702]),
      [RANGE_800_900],
    );
    expect(starts.size).toBe(0);
    expect(ends.size).toBe(0);
  });

  it("区间整体在下一页时不画任何线", () => {
    const { starts, ends } = computeHRangeBoundaries(
      makeEntries([901, 902, 903]),
      [RANGE_800_900],
    );
    expect(starts.size).toBe(0);
    expect(ends.size).toBe(0);
  });

  it("区间尾部落在本页：画最近边界的开始线与结束线", () => {
    const { starts, ends } = computeHRangeBoundaries(
      makeEntries([898, 899, 900]),
      [RANGE_800_900],
    );
    expect(starts.has(898)).toBe(true);
    expect(ends.has(900)).toBe(true);
  });

  it("区间头部落在本页：画最近边界的开始线与结束线", () => {
    const { starts, ends } = computeHRangeBoundaries(
      makeEntries([800, 801, 802]),
      [RANGE_800_900],
    );
    expect(starts.has(800)).toBe(true);
    expect(ends.has(802)).toBe(true);
  });

  it("多区间分离时各自成对边界", () => {
    const { starts, ends } = computeHRangeBoundaries(
      makeEntries([11, 12, 13, 31, 32, 33]),
      [
        { lo: 11, hi: 13 },
        { lo: 31, hi: 33 },
      ],
    );
    expect(starts.has(11)).toBe(true);
    expect(starts.has(31)).toBe(true);
    expect(ends.has(13)).toBe(true);
    expect(ends.has(33)).toBe(true);
  });

  it("空区间列表不产生任何边界", () => {
    const { starts, ends } = computeHRangeBoundaries(makeEntries([1, 2, 3]), []);
    expect(starts.size).toBe(0);
    expect(ends.size).toBe(0);
  });

  it("过滤后页内条目不落在区间内时不画线", () => {
    const { starts, ends } = computeHRangeBoundaries(
      makeEntries([795, 796, 906, 907]),
      [RANGE_800_900],
    );
    expect(starts.size).toBe(0);
    expect(ends.size).toBe(0);
  });

  it("字符串 index 条目统一转为 number key，Map 查询可命中", () => {
    const entries = ["11", "12", "13"].map((index) => ({ index } as CacheEntry));
    const { starts, ends } = computeHRangeBoundaries(entries, [
      { lo: 12, hi: 13 },
    ]);
    // Map key 是 number，与渲染处 Number(entrySignal().index) 一致
    expect(starts.get(12)).toEqual({ lo: 12, hi: 13 });
    expect(ends.get(13)).toEqual({ lo: 12, hi: 13 });
    // 字符串 key 不应命中（口径统一后不会再出现）
    expect(starts.has("12")).toBe(false);
    expect(starts.size).toBe(1);
    expect(ends.size).toBe(1);
  });
});
