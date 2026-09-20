/**
 * ReviewPage 对外导出符号契约快照测试（0.4.10 重构安全网）。
 *
 * 背景：0.4.10 把 ReviewPage.tsx（2659 行）拆分为主组件 + 若干子模块，
 * 主文件必须 re-export 全部原导出符号，否则 5 个既有测试文件会因导入失败而
 * 报错（或更糟：被删除的符号悄悄变成 undefined 后测试假通过）。
 *
 * 本测试把「ReviewPage.tsx 必须提供的导出面」固化，任何符号丢失都会立刻失败。
 */
import { describe, it, expect } from "vitest";
import * as ReviewPageModule from "../pages/review/ReviewPage";

/** 重构前 ReviewPage.tsx 的全部导出符号（值导出 + 类型导出分开列）。 */
const RUNTIME_EXPORTS = [
  "ReviewPage",
  "EntryCard",
  "applyProblemTypeFilter",
  "isMetaKvEditorElement",
  "shouldYieldToNative",
  "shouldBlurBeforeUndo",
  "decideCrossFileRestore",
  "resolveKeyAction",
  "computeHRangeBoundaries",
];

/** 类型导出（编译期存在，运行时不可枚举，用 tsc 校验；此处仅记录清单）。 */
const TYPE_EXPORTS = [
  "PendingRestore",
  "CrossFileDecision",
  "KeyAction",
  "KeyEventLike",
];

describe("ReviewPage 导出符号契约", () => {
  it("全部运行时导出符号存在且类型正确", () => {
    for (const name of RUNTIME_EXPORTS) {
      const value = (ReviewPageModule as Record<string, unknown>)[name];
      expect(value, `ReviewPage.tsx 丢失导出符号: ${name}`).toBeDefined();
    }
  });

  it("组件导出为函数，纯函数导出可调用", () => {
    for (const name of RUNTIME_EXPORTS) {
      const value = (ReviewPageModule as Record<string, unknown>)[name];
      expect(typeof value, `${name} 应为函数`).toBe("function");
    }
  });

  it("类型导出清单非空（编译期由 tsc 保证，此处防清单被清空）", () => {
    expect(TYPE_EXPORTS.length).toBeGreaterThan(0);
  });
});
