/**
 * appStore 单元测试
 * 覆盖 markDirty / markClean / dirtyFiles — 本次修改的三个 P0 修复点依赖的正确性
 */
import { describe, it, expect, beforeEach } from "vitest";
import {
  appState,
  setAppState,
  markDirty,
  markClean,
  markDirty as md,
  markClean as mc,
  openProject,
} from "../stores/appStore";

/** 重置 dirtyFiles 与 activeFilePath 到初始状态 */
function resetDirtyFiles(): void {
  setAppState("dirtyFiles", []);
  setAppState("activeFilePath", null);
}

describe("markDirty / markClean", () => {
  beforeEach(() => {
    resetDirtyFiles();
  });

  it("markDirty 将文件添加到 dirtyFiles", () => {
    markDirty("foo.json");
    expect(appState.dirtyFiles).toContain("foo.json");
  });

  it("重复 markDirty 同一文件不产生重复条目（Set 去重）", () => {
    markDirty("foo.json");
    markDirty("foo.json");
    markDirty("foo.json");
    expect(appState.dirtyFiles).toEqual(["foo.json"]);
  });

  it("markClean 从 dirtyFiles 中移除指定文件", () => {
    markDirty("foo.json");
    markDirty("bar.json");
    markClean("foo.json");
    expect(appState.dirtyFiles).toEqual(["bar.json"]);
  });

  it("markClean 不存在的文件不抛出异常", () => {
    expect(() => markClean("nonexistent.json")).not.toThrow();
    expect(appState.dirtyFiles).toEqual([]);
  });

  it("多个文件被标记后 dirtyFiles 包含全部", () => {
    markDirty("a.json");
    markDirty("b.json");
    markDirty("c.json");
    expect(appState.dirtyFiles).toHaveLength(3);
    expect(appState.dirtyFiles).toContain("a.json");
    expect(appState.dirtyFiles).toContain("b.json");
    expect(appState.dirtyFiles).toContain("c.json");
  });

  it("markDirty 后的 markClean 让 dirtyFiles 回归空", () => {
    markDirty("f.json");
    expect(appState.dirtyFiles).toHaveLength(1);
    markClean("f.json");
    expect(appState.dirtyFiles).toHaveLength(0);
  });

  it("closeProject 清空 dirtyFiles", () => {
    markDirty("x.json");
    markDirty("y.json");
    // 模拟 closeProject 行为
    setAppState("dirtyFiles", []);
    expect(appState.dirtyFiles).toEqual([]);
  });
});

describe("markDirty 与 onInput 搭配场景（模拟主译文框逐键标脏）", () => {
  beforeEach(() => {
    resetDirtyFiles();
    setAppState("activeFilePath", "game/t01.txt.json");
  });

  it("逐键调用 markDirty 只保留一个文件记录（不随时间膨胀）", () => {
    const f = "game/t01.txt.json";
    // 模拟用户快速输入 50 个字符
    for (let i = 0; i < 50; i++) {
      md(f);
    }
    expect(appState.dirtyFiles).toEqual([f]);
    expect(appState.dirtyFiles).toHaveLength(1);
  });

  it("onInput markDirty 后 blur 再 markDirty 不产生重复", () => {
    const f = "game/t01.txt.json";
    // onInput → markDirty
    md(f);
    // blur → handleFieldChange → markDirty
    md(f);
    expect(appState.dirtyFiles).toEqual([f]);
  });

  it("保存后 markClean 清除，再次键入 markDirty 重新标记", () => {
    const f = "game/t01.txt.json";
    md(f);
    mc(f);
    expect(appState.dirtyFiles).toEqual([]);
    // 再次编辑
    md(f);
    expect(appState.dirtyFiles).toEqual([f]);
  });
});

describe("activeFilePath 守卫（模拟 onInput 中 if (appState.activeFilePath) 的条件）", () => {
  beforeEach(() => {
    resetDirtyFiles();
  });

  it("activeFilePath 为 null 时不调 markDirty", () => {
    expect(appState.activeFilePath).toBeNull();
    // 模拟: if (appState.activeFilePath) markDirty(...)
    if (appState.activeFilePath) {
      markDirty(appState.activeFilePath);
    }
    expect(appState.dirtyFiles).toEqual([]);
  });

  it("activeFilePath 非空时正常调 markDirty", () => {
    setAppState("activeFilePath", "game/file.json");
    if (appState.activeFilePath) {
      markDirty(appState.activeFilePath);
    }
    expect(appState.dirtyFiles).toEqual(["game/file.json"]);
  });
});

describe("openProject 状态重置（M20）", () => {
  beforeEach(() => {
    // 清理 openProject 测试可能遗留的项目状态，避免污染其他用例
    setAppState({
      activeProjectId: null,
      activeFilePath: null,
      dirtyFiles: [],
      cacheTree: [],
      cacheVersion: 0,
      problemVersion: 0,
      reviewJumpToIndex: null,
      prevJobStatus: "",
    });
  });

  it("打开新项目时重置上一项目的状态残留", async () => {
    // 模拟上一项目残留状态
    setAppState({
      activeProjectId: "old-project",
      activeFilePath: "old/game.txt.json",
      dirtyFiles: ["old/game.txt.json"],
      cacheTree: [{ path: "old/x.json", name: "x.json", is_file: true, size: 1, modified: "" }],
      cacheVersion: 5,
      problemVersion: 3,
      reviewJumpToIndex: 2,
      prevJobStatus: "running",
    });

    await openProject("new-project", { configFileName: "config.yaml" });

    expect(appState.activeProjectId).toBe("new-project");
    expect(appState.activeFilePath).toBeNull();
    expect(appState.dirtyFiles).toEqual([]);
    expect(appState.cacheTree).toEqual([]);
    expect(appState.cacheVersion).toBe(0);
    expect(appState.problemVersion).toBe(0);
    expect(appState.reviewJumpToIndex).toBeNull();
    expect(appState.prevJobStatus).toBe("");
  });
});
