/**
 * partitionDirtyKeys 单元测试：切页/切视图守卫收尾时的脏键划分。
 *
 * 背景：页内切换文件守卫曾存在「保存失败仍跳转且不清脏」的缺陷，导致 dirtyFiles
 * 残留非当前文件的陈旧键，navigateTo 切视图守卫（dirtyFiles.length > 0）被反复拦截，
 * 形成「保存/不保存后重复弹窗」死循环。守卫收尾需把残留键与当前文件键区分开清理。
 */

import { describe, it, expect } from "vitest";
import { partitionDirtyKeys } from "../pages/review/reviewKeymap";

describe("partitionDirtyKeys", () => {
  it("dirtyFiles 为空时当前文件视为不脏且无残留键", () => {
    expect(partitionDirtyKeys([], "a.json")).toEqual({ currentDirty: false, staleKeys: [] });
  });

  it("当前文件在 dirtyFiles 中时判定为脏，其余键归入残留", () => {
    const r = partitionDirtyKeys(["a.json", "b.json", "c.json"], "b.json");
    expect(r.currentDirty).toBe(true);
    expect(r.staleKeys).toEqual(["a.json", "c.json"]);
  });

  it("当前文件不在 dirtyFiles 中时判定为不脏，全部键归入残留", () => {
    const r = partitionDirtyKeys(["a.json", "b.json"], "c.json");
    expect(r.currentDirty).toBe(false);
    expect(r.staleKeys).toEqual(["a.json", "b.json"]);
  });

  it("currentKey 为空串（无已加载文件）时视为不脏，全部键归入残留", () => {
    const r = partitionDirtyKeys(["a.json"], "");
    expect(r.currentDirty).toBe(false);
    expect(r.staleKeys).toEqual(["a.json"]);
  });

  it("dirtyFiles 仅含当前文件时无残留键", () => {
    const r = partitionDirtyKeys(["only.json"], "only.json");
    expect(r.currentDirty).toBe(true);
    expect(r.staleKeys).toEqual([]);
  });

  it("残留键保持原顺序，便于按序 markClean", () => {
    const r = partitionDirtyKeys(["d", "a", "c", "b"], "a");
    expect(r.staleKeys).toEqual(["d", "c", "b"]);
  });
});
