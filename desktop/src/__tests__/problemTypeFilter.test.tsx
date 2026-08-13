/**
 * 问题类型多选过滤（AND 语义）回归测试。
 *
 * 背景：校对审核工具栏原「全部类型」为单选 <select>，改为多选下拉后，
 * 勾选多个类型时需同时命中所有勾选类型才保留（AND 语义）。本测试直接验证
 * 纯函数 applyProblemTypeFilter 的过滤逻辑（空数组=全部、单选、多选 AND、
 * 类型名带「：细节」后缀解析）。
 */
import { describe, it, expect } from "vitest";
import type { CacheEntry } from "../lib/api/types";
import { applyProblemTypeFilter } from "../pages/review/ReviewPage";

function entry(index: number, problem: string | null): CacheEntry {
  return { index, problem } as CacheEntry;
}

describe("applyProblemTypeFilter 多选 AND 过滤", () => {
  const list: CacheEntry[] = [
    entry(1, "残留日文：ゴックシ"),
    entry(2, "缺控制符：[ ]"),
    entry(3, "残留日文"),
    entry(4, "比日文长"),
    entry(5, null),
    entry(6, "残留日文, 缺控制符：xx"),
  ];

  it("空数组返回原列表（全部类型）", () => {
    expect(applyProblemTypeFilter(list, [])).toEqual(list);
  });

  it("单选只保留命中该类型的条目", () => {
    const out = applyProblemTypeFilter(list, ["残留日文"]);
    expect(out.map((e) => e.index)).toEqual([1, 3, 6]);
  });

  it("多选为 AND 语义：须同时包含所有勾选类型才保留", () => {
    const out = applyProblemTypeFilter(list, ["残留日文", "缺控制符"]);
    // 仅 index 6 同时含「残留日文」与「缺控制符」；只含其一的不保留
    expect(out.map((e) => e.index)).toEqual([6]);
  });

  it("多选过滤后类型名带「：细节」后缀也能命中", () => {
    const out = applyProblemTypeFilter(list, ["缺控制符"]);
    // index 2 的 problem 为 "缺控制符：[ ]"，problemTypesOf 拆分后为 "缺控制符" → 命中
    expect(out.map((e) => e.index)).toEqual([2, 6]);
  });

  it("多选时缺少任一类型的条目被过滤（AND 不匹配）", () => {
    const out = applyProblemTypeFilter(list, ["残留日文", "比日文长"]);
    // 无条目同时含这两类 → 空列表
    expect(out).toEqual([]);
  });

  it("无匹配类型时返回空列表", () => {
    const out = applyProblemTypeFilter(list, ["词频过高"]);
    expect(out).toEqual([]);
  });

  it("problem 为 null/undefined 的条目不参与匹配", () => {
    const out = applyProblemTypeFilter(list, ["残留日文"]);
    expect(out.some((e) => e.index === 5)).toBe(false);
  });

  it("多选 AND 且含「：细节」后缀：仅同时命中所有类型者保留", () => {
    // index 6 为 "残留日文, 缺控制符：xx"（缺控制符带细节后缀）；
    // 同时勾选「残留日文」「缺控制符」仍应命中 index 6（细节后缀被剥离匹配）。
    const out = applyProblemTypeFilter(list, ["残留日文", "缺控制符"]);
    expect(out.map((e) => e.index)).toEqual([6]);
  });
});
