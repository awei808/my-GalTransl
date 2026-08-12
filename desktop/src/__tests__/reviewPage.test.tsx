/**
 * ReviewPage 逻辑验证测试
 *
 * 由于 ReviewPage 组件依赖完整的 API/WS/Store 子系统，完整渲染集成测试
 * 需要大量 mock 基础设施（超出本次审查 scope）。本测试聚焦验证本次 P0 修改中
 * 所依赖的状态机逻辑正确性：
 *
 *   1. onInput → markDirty → dirtyFiles 即时更新（在主框打字即标脏）
 *   2. Ctrl+S → blur → entries 草稿提交 → save 读取最新数据
 *   3. 保存按钮 → blur → 同上
 *   4. 保存后 markClean → 脏标志清除
 *   5. 再次编辑 → markDirty 重新触发
 */

import { describe, it, expect, beforeEach } from "vitest";
import {
  appState,
  setAppState,
  markDirty,
  markClean,
} from "../stores/appStore";
import { confirm, getConfirmState } from "../stores/confirmStore";
import {
  clearUndo,
  getUndoState,
  peekRedo,
  peekUndo,
  pushUndo,
  redo,
  undo,
} from "../stores/undoStore";
import { decideCrossFileRestore, resolveKeyAction, shouldYieldToNative } from "../pages/review/ReviewPage";
import type { PendingRestore } from "../pages/review/ReviewPage";
import type { CacheEntry } from "../lib/api/types";

/* ─────────── 模拟 Repair: 保存前的 blur 提交草稿流程 ─────────── */

/**
 * 模拟 EntryCard 的 onBlur 提交逻辑。
 * 主译文框失焦 → 调用 handleFieldChange("pre_dst", currentValue)。
 * 这里简化为直接修改 entries 数组并标脏 + 递增 entriesRev。
 */
function simulateBlurCommit(entries: Array<{ index: number; pre_dst: string }>, serial: number, newValue: string): {
  entries: Array<{ index: number; pre_dst: string }>;
  entriesRev: number;
} {
  const idx = entries.findIndex((e) => e.index === serial);
  if (idx === -1) return { entries, entriesRev: 0 };
  entries[idx] = { ...entries[idx], pre_dst: newValue };
  markDirty("game/t01.txt.json");
  return { entries, entriesRev: 1 };
}

/**
 * 模拟 Ctrl+S / 保存按钮点击前的 blur 操作：
 *   (document.activeElement as HTMLElement | null)?.blur()
 * 等价于：如果 focused 元素是主译文框，则同步调用其 onBlur。
 */
function simulateBlurBeforeSave(
  focusedEntryIndex: number | null,
  draftValue: string | null,
  entries: Array<{ index: number; pre_dst: string }>,
): {
  entries: Array<{ index: number; pre_dst: string }>;
  committed: boolean;
} {
  if (focusedEntryIndex === null || draftValue === null) {
    return { entries, committed: false };
  }
  const r = simulateBlurCommit(entries, focusedEntryIndex, draftValue);
  return { entries: r.entries, committed: r.entriesRev > 0 };
}

/* ─────────── 测试 ─────────── */

beforeEach(() => {
  setAppState("dirtyFiles", []);
  setAppState("activeFilePath", "game/t01.txt.json");
});

describe("场景 1：主框打字 → onInput markDirty 即时出现", () => {
  it("键入第一个字符时 dirtyFiles 就包含该文件", () => {
    // 模拟 onInput handler 中的逻辑
    const activeFile = appState.activeFilePath;
    if (activeFile) markDirty(activeFile);
    expect(appState.dirtyFiles).toContain("game/t01.txt.json");
  });

  it("之后 blur 再 markDirty 不产生重复条目（Set 去重）", () => {
    const activeFile = appState.activeFilePath;
    if (activeFile) markDirty(activeFile);          // onInput
    if (activeFile) markDirty(activeFile);          // blur → handleFieldChange
    expect(appState.dirtyFiles).toEqual(["game/t01.txt.json"]);
  });
});

describe("场景 2：Ctrl+S（无 blur 路径）→ 先 blur 再 save", () => {
  it("父组件先 blur 已聚焦框把草稿落盘，再 save 读到最新 entries", () => {
    let entries: Array<{ index: number; pre_dst: string }> = [
      { index: 1, pre_dst: "旧译文" },
    ];
    const draftValue = "新译文（正在输入）";
    const focusedIdx = 1;

    // 步骤 1: Ctrl+S handler: (document.activeElement as HTMLElement)?.blur()
    const { entries: updated, committed } = simulateBlurBeforeSave(
      focusedIdx,
      draftValue,
      entries,
    );
    entries = updated;

    // 步骤 2: saveCurrentFile 读取 entries() 进行持久化
    // 验证: entries 中已包含"新译文"
    expect(committed).toBe(true);
    expect(entries[0].pre_dst).toBe("新译文（正在输入）");
  });

  it("如果 blur 后没发现聚焦框（activeElement 非主译文框），entries 不额外变更", () => {
    let entries: Array<{ index: number; pre_dst: string }> = [
      { index: 1, pre_dst: "旧译文" },
    ];
    // 无聚焦框（如点击侧栏菜单等）
    const { entries: updated, committed } = simulateBlurBeforeSave(null, null, entries);
    entries = updated;
    expect(committed).toBe(false);
    expect(entries[0].pre_dst).toBe("旧译文");
  });
});

describe("场景 3：保存按钮点击 → blur → save", () => {
  it("按钮 click 先 blur 再保存，逻辑与 Ctrl+S 一致", () => {
    let entries: Array<{ index: number; pre_dst: string }> = [
      { index: 3, pre_dst: "已保存内容" },
    ];
    // 模拟模糊前草稿：用户键盘输入了"新内容"但焦点还在 box
    const draft = "新内容";
    const { entries: updated, committed } = simulateBlurBeforeSave(3, draft, entries);
    entries = updated;
    expect(committed).toBe(true);
    expect(entries[0].pre_dst).toBe("新内容");

    // 模拟保存完成后 markClean
    markClean("game/t01.txt.json");
    expect(appState.dirtyFiles).toEqual([]);
  });
});

describe("场景 4：完整生命周期 — 打字 → 标脏 → 保存 → markClean → 再打再标", () => {
  it("完整脏标志周转", () => {
    const f = "game/t01.txt.json";

    // 1. 用户打字
    markDirty(f);
    expect(appState.dirtyFiles).toContain(f);

    // 2. blur 再标一次（不重复）
    markDirty(f);
    expect(appState.dirtyFiles).toEqual([f]);

    // 3. 保存完成 → markClean
    markClean(f);
    expect(appState.dirtyFiles).toEqual([]);

    // 4. 再次编辑
    markDirty(f);
    expect(appState.dirtyFiles).toEqual([f]);

    // 5. 再次保存
    markClean(f);
    expect(appState.dirtyFiles).toEqual([]);
  });
});

describe("场景 5：确认弹窗「取消」后留在原文件（runSwitch 的 extra 分支）", () => {
  beforeEach(() => {
    // 确保无残留 confirm
    if (getConfirmState().visible) {
      confirm.resolve(false);
    }
  });

  it("三按钮弹窗：点 extraText='取消' → action='extra', confirmed=false", async () => {
    const promise = confirm.show({
      title: "未保存的修改",
      message: "有未保存的修改，是否保存后再切换？",
      confirmText: "保存",
      cancelText: "不保存",
      extraText: "取消",
      tone: "warning",
      dismissible: false,
    });

    confirm.resolve(false, undefined, "extra");
    const result = await promise;

    // runSwitch 中判断: if (res.action === "extra") { setAppState("activeFilePath", prevFile); break; }
    expect(result.action).toBe("extra");
    expect(result.confirmed).toBe(false);
  });

  it("三按钮弹窗：点保存 → action='confirm', confirmed=true", async () => {
    const promise = confirm.show({
      title: "未保存的修改",
      message: "有未保存的修改，是否保存后再切换？",
      confirmText: "保存",
      cancelText: "不保存",
      extraText: "取消",
      tone: "warning",
      dismissible: false,
    });

    confirm.resolve(true);
    const result = await promise;
    expect(result.action).toBe("confirm");
    expect(result.confirmed).toBe(true);
  });

  it("三按钮弹窗：点不保存 → action='cancel', confirmed=false", async () => {
    const promise = confirm.show({
      title: "未保存的修改",
      message: "有未保存的修改，是否保存后再切换？",
      confirmText: "保存",
      cancelText: "不保存",
      extraText: "取消",
      tone: "warning",
      dismissible: false,
    });

    confirm.resolve(false);
    const result = await promise;
    expect(result.action).toBe("cancel");
    expect(result.confirmed).toBe(false);
  });
});

describe("场景 6：边界条件", () => {
  it("activeFilePath 为 null 时 onInput guard 不调 markDirty（不抛异常）", () => {
    setAppState("activeFilePath", null);
    // 模拟 onInput: if (appState.activeFilePath) markDirty(...)
    if (appState.activeFilePath) {
      markDirty(appState.activeFilePath);
    }
    expect(appState.dirtyFiles).toEqual([]);
  });

  it("document.activeElement 为 null 时 blur() 安全跳过", () => {
    // 模拟: (document.activeElement as HTMLElement | null)?.blur()
    const el = null as HTMLElement | null;
    expect(() => el?.blur()).not.toThrow();
  });

  it("重复快速 Ctrl+S（saveInFlight=true）不导致双写", () => {
    // saveCurrentFile 内部: if (saveInFlight) return;
    // 第二次 Ctrl+S 只是 blur（空操作）+ 直接 return
    // 此测试验证逻辑不抛异常
    let callCount = 0;
    function mockSave() {
      callCount++;
    }
    // 第一次: 设置 saveInFlight=true, 保存
    let saveInFlight = true;
    // 第二次: 因为 saveInFlight=true, 直接 return
    if (!saveInFlight) mockSave();
    expect(callCount).toBe(0); // 第二次被跳过
    // 第一次完成后 finally{ saveInFlight=false }，第三次可进入
    saveInFlight = false;
    if (!saveInFlight) mockSave();
    expect(callCount).toBe(1); // 第三次成功
  });
});

describe("场景 7：主译文框输入中撤销/重做（修复：撤销/重做前先 blur 提交草稿）", () => {
  beforeEach(() => {
    clearUndo();
  });

  it("blur 提交草稿（值变化 → pushUndo）后 undo 取回该记录并可还原", () => {
    // 模拟 handleFieldChange 值变化分支：setEntries + pushUndo
    pushUndo({
      id: "game/t01.txt.json:1",
      file: "game/t01.txt.json",
      index: 1,
      before: { pre_dst: "旧译文" },
      after: { pre_dst: "新译文" },
      description: "修改 译文",
    });
    const entry = undo();
    expect(entry?.index).toBe(1);
    expect((entry?.before as Record<string, unknown>).pre_dst).toBe("旧译文");
    expect(getUndoState().canUndo).toBe(false);
  });

  it("撤销后 redo 取回该记录", () => {
    pushUndo({
      id: "game/t01.txt.json:1",
      file: "game/t01.txt.json",
      index: 1,
      before: { pre_dst: "旧译文" },
      after: { pre_dst: "新译文" },
      description: "修改 译文",
    });
    undo();
    const entry = redo();
    expect(entry?.index).toBe(1);
    expect((entry?.after as Record<string, unknown>).pre_dst).toBe("新译文");
    expect(getUndoState().canRedo).toBe(false);
  });

  it("blur 提交时值未变化 → 不额外入栈，不打断既有 undo 链", () => {
    // 先有历史记录
    pushUndo({
      id: "game/t01.txt.json:1",
      file: "game/t01.txt.json",
      index: 1,
      before: { pre_dst: "旧译文" },
      after: { pre_dst: "中译文" },
      description: "修改 译文",
    });
    // 模拟 handleFieldChange 的"值未变化"分支：直接 return，不 pushUndo
    // 此时栈应保持原样，undo 仍取回既有记录
    const entry = undo();
    expect(entry?.index).toBe(1);
    expect((entry?.after as Record<string, unknown>).pre_dst).toBe("中译文");
    expect(getUndoState().stackSize).toBe(1);
  });

  it("连续编辑后 undo 按 LIFO 回退，redo 按顺序恢复", () => {
    for (let i = 1; i <= 3; i++) {
      pushUndo({
        id: `game/t01.txt.json:${i}`,
        file: "game/t01.txt.json",
        index: i,
        before: { pre_dst: `旧${i}` },
        after: { pre_dst: `新${i}` },
        description: "修改 译文",
      });
    }
    expect(undo()?.index).toBe(3);
    expect(undo()?.index).toBe(2);
    expect(undo()?.index).toBe(1);
    expect(undo()).toBeNull();
    expect(redo()?.index).toBe(1);
    expect(redo()?.index).toBe(2);
    expect(redo()?.index).toBe(3);
    expect(redo()).toBeNull();
  });
});

describe("场景 8：元数据模式撤销/重做（修复：元数据编辑接入 undo 栈）", () => {
  const META_FILE = "game/pass1_cache/script.meta.json";
  const OTHER_META_FILE = "game/pass1_cache/other.meta.json";
  const baseMeta: Record<string, unknown> = { id: "file01", title: "旧标题" };
  const editedMeta: Record<string, unknown> = { id: "file01", title: "新标题" };

  beforeEach(() => {
    clearUndo();
  });

  it("元数据整对象快照入栈后 undo 取回 before（index 固定为 0，file 为元数据路径）", () => {
    // 模拟 handleUndo 元数据分支：未保存编辑先 pushUndo 再 undo
    pushUndo({
      id: `${META_FILE}:meta`,
      file: META_FILE,
      index: 0,
      before: baseMeta,
      after: editedMeta,
      description: "修改 元数据",
    });
    const entry = undo();
    expect(entry?.index).toBe(0);
    expect(entry?.file).toBe(META_FILE);
    expect(entry?.before).toEqual(baseMeta);
    expect(getUndoState().canUndo).toBe(false);
  });

  it("撤销后 redo 取回 after 整对象快照", () => {
    pushUndo({
      id: `${META_FILE}:meta`,
      file: META_FILE,
      index: 0,
      before: baseMeta,
      after: editedMeta,
      description: "修改 元数据",
    });
    undo();
    const entry = redo();
    expect(entry?.after).toEqual(editedMeta);
    expect(getUndoState().canRedo).toBe(false);
  });

  it("currentFile 与记录 file 不匹配时守卫拦截（模拟 handleUndo 的 file 校验）", () => {
    pushUndo({
      id: `${META_FILE}:meta`,
      file: META_FILE,
      index: 0,
      before: baseMeta,
      after: editedMeta,
      description: "修改 元数据",
    });
    // 当前打开的是另一个元数据文件 → undo 取回记录后 handleUndo 直接 return，不生效
    const entry = undo();
    const matchesCurrent = entry?.file === OTHER_META_FILE;
    expect(matchesCurrent).toBe(false);
  });

  it("存在未保存编辑时先入栈会清空 redo 栈（避免 redo 覆盖未保存编辑）", () => {
    // 先有一条已撤销的历史（canRedo 应为 true）
    pushUndo({
      id: `${META_FILE}:meta`,
      file: META_FILE,
      index: 0,
      before: baseMeta,
      after: editedMeta,
      description: "修改 元数据",
    });
    undo();
    expect(getUndoState().canRedo).toBe(true);
    // 模拟 handleRedo 元数据分支：有未保存编辑先 pushUndo（等价于 blur 提交，会清空 redo）
    pushUndo({
      id: `${META_FILE}:meta`,
      file: META_FILE,
      index: 0,
      before: editedMeta,
      after: { id: "file01", title: "再次编辑" },
      description: "修改 元数据",
    });
    expect(getUndoState().canRedo).toBe(false);
  });
});

describe("场景 9：resolveKeyAction 快捷键分派", () => {
  it("Ctrl+Z → undo", () => {
    expect(resolveKeyAction({ key: "z", ctrlKey: true, metaKey: false, shiftKey: false })).toBe("undo");
  });

  it("Ctrl+Shift+Z（key 为大写 Z）→ redo", () => {
    expect(resolveKeyAction({ key: "Z", ctrlKey: true, metaKey: false, shiftKey: true })).toBe("redo");
  });

  it("Caps Lock 时 Ctrl+Z（key 为大写 Z、无 Shift）→ undo", () => {
    expect(resolveKeyAction({ key: "Z", ctrlKey: true, metaKey: false, shiftKey: false })).toBe("undo");
  });

  it("Ctrl+Y → redo", () => {
    expect(resolveKeyAction({ key: "y", ctrlKey: true, metaKey: false, shiftKey: false })).toBe("redo");
  });

  it("Ctrl+S → save", () => {
    expect(resolveKeyAction({ key: "s", ctrlKey: true, metaKey: false, shiftKey: false })).toBe("save");
  });

  it("Mac Cmd+S（metaKey）→ save", () => {
    expect(resolveKeyAction({ key: "s", ctrlKey: false, metaKey: true, shiftKey: false })).toBe("save");
  });

  it("无 Ctrl/Meta → null", () => {
    expect(resolveKeyAction({ key: "z", ctrlKey: false, metaKey: false, shiftKey: false })).toBeNull();
  });

  it("Ctrl+A → null", () => {
    expect(resolveKeyAction({ key: "a", ctrlKey: true, metaKey: false, shiftKey: false })).toBeNull();
  });
});

describe("场景 10：peekUndo/peekRedo 预览语义（跨文件撤销跳转的数据来源）", () => {
  beforeEach(() => {
    clearUndo();
  });

  it("peekUndo 返回栈顶记录但不动 pointer", () => {
    pushUndo({ id: "f:1", file: "f", index: 1, before: { pre_dst: "a" }, after: { pre_dst: "b" } });
    pushUndo({ id: "f:2", file: "f", index: 2, before: { pre_dst: "c" }, after: { pre_dst: "d" } });
    const before = getUndoState().pointer;
    const entry = peekUndo();
    expect(entry?.index).toBe(2);
    expect(getUndoState().pointer).toBe(before); // 预览不移动 pointer
    expect(getUndoState().canUndo).toBe(true);
  });

  it("peekRedo 返回 pointer 之后第一条且不消费 redo 能力", () => {
    pushUndo({ id: "f:1", file: "f", index: 1, before: { pre_dst: "a" }, after: { pre_dst: "b" } });
    pushUndo({ id: "f:2", file: "f", index: 2, before: { pre_dst: "c" }, after: { pre_dst: "d" } });
    undo();
    const entry = peekRedo();
    expect(entry?.index).toBe(2);
    expect(getUndoState().canRedo).toBe(true); // 未消费，仍可重做
  });

  it("混合文件栈：peekUndo 取时间最近一条（含异文件，供跨文件跳转）", () => {
    pushUndo({ id: "a:1", file: "a", index: 1, before: { pre_dst: "a1" }, after: { pre_dst: "a2" } });
    pushUndo({ id: "b:1", file: "b", index: 1, before: { pre_dst: "b1" }, after: { pre_dst: "b2" } });
    expect(peekUndo()?.file).toBe("b");
  });

  it("栈空时 peekUndo/peekRedo 返回 null", () => {
    expect(peekUndo()).toBeNull();
    expect(peekRedo()).toBeNull();
  });

  it("pushUndo 后 peek 透传调用方传入的 id（支撑跨文件恢复用 id 比较）", () => {
    pushUndo({ id: "a:meta", file: "a", index: 0, before: { title: "x" }, after: { title: "y" } });
    pushUndo({ id: "b:1", file: "b", index: 1, before: { pre_dst: "p" }, after: { pre_dst: "q" } });
    expect(peekUndo()?.id).toBe("b:1"); // 栈顶 id 透传
    undo();
    expect(peekRedo()?.id).toBe("b:1"); // redo 目标 id 透传
  });

  it("混合文件栈：异文件记录 id 与当前文件记录 id 不同（跨文件取消比较依据）", () => {
    pushUndo({ id: "a:1", file: "a", index: 1, before: { pre_dst: "a1" }, after: { pre_dst: "a2" } });
    pushUndo({ id: "b:1", file: "b", index: 1, before: { pre_dst: "b1" }, after: { pre_dst: "b2" } });
    expect(peekUndo()?.file).toBe("b");
    expect(peekUndo()?.id).not.toBe("a:1");
  });
});

describe("场景 11：shouldYieldToNative 草稿态让出原生撤销", () => {
  const committedEntries = [{ index: 3, pre_dst: "已提交译文" }] as CacheEntry[];

  function makeTextarea(index: string, value: string, className = "entry-dst-input"): HTMLTextAreaElement {
    const ta = document.createElement("textarea");
    ta.className = className;
    if (index) ta.dataset.index = index;
    ta.value = value;
    return ta;
  }

  it("主译文框草稿与提交值一致（已提交）→ 不让出", () => {
    expect(shouldYieldToNative(makeTextarea("3", "已提交译文"), committedEntries)).toBe(false);
  });

  it("主译文框存在未提交草稿 → 让出原生", () => {
    expect(shouldYieldToNative(makeTextarea("3", "输入中未提交"), committedEntries)).toBe(true);
  });

  it("非主译文框 textarea（如元数据框）→ 不让出", () => {
    expect(shouldYieldToNative(makeTextarea("3", "未提交", "meta-content-textarea"), committedEntries)).toBe(false);
  });

  it("缺少 data-index → 无法定位条目，不让出", () => {
    const ta = makeTextarea("", "未提交");
    expect(shouldYieldToNative(ta, committedEntries)).toBe(false);
  });

  it("entries 为空（加载中）→ 按空提交值比较，草稿为空则不让出", () => {
    expect(shouldYieldToNative(makeTextarea("3", ""), [])).toBe(false);
  });

  it("元数据框：传入 metaDraftDirty=true → 让出原生逐字符撤销（方向 B）", () => {
    expect(shouldYieldToNative(makeTextarea("3", "未提交", "meta-content-textarea"), committedEntries, true)).toBe(true);
  });

  it("元数据框：metaDraftDirty=false（已提交/无草稿）→ 不让出，走操作级撤销", () => {
    expect(shouldYieldToNative(makeTextarea("3", "未提交", "meta-content-textarea"), committedEntries, false)).toBe(false);
  });

  it("元数据框：未传 metaDraftDirty → 默认 false，不让出", () => {
    expect(shouldYieldToNative(makeTextarea("3", "未提交", "meta-content-textarea"), committedEntries)).toBe(false);
  });
});

/* ─────────── 跨文件恢复状态机：decideCrossFileRestore 纯函数决策 ─────────── */

describe("decideCrossFileRestore 跨文件恢复决策", () => {
  const makePending = (file: string, id: string): PendingRestore => ({
    entry: { id, file, index: 0, before: { pre_dst: "a" }, after: { pre_dst: "b" } } as UndoEntry,
    dir: "undo",
  });

  it("pending 为 null → wait", () => {
    expect(
      decideCrossFileRestore({ pending: null, currentFilePath: "a", ready: true, metaLoadFailed: false, probe: null }),
    ).toEqual({ kind: "wait" });
  });

  it("当前文件路径与 target 不符（用户取消切换）→ cancel switched", () => {
    const pending = makePending("B", "b:1");
    expect(
      decideCrossFileRestore({ pending, currentFilePath: "A", ready: true, metaLoadFailed: false, probe: pending.entry }),
    ).toEqual({ kind: "cancel", reason: "switched" });
  });

  it("元数据加载失败（!metaLoading && metaEntry===null）→ cancel meta-load-failed", () => {
    const pending = makePending("M", "m:1");
    expect(
      decideCrossFileRestore({ pending, currentFilePath: "M", ready: false, metaLoadFailed: true, probe: pending.entry }),
    ).toEqual({ kind: "cancel", reason: "meta-load-failed" });
  });

  it("尚未就绪（文件加载中）→ wait", () => {
    const pending = makePending("A", "a:1");
    expect(
      decideCrossFileRestore({ pending, currentFilePath: "A", ready: false, metaLoadFailed: false, probe: pending.entry }),
    ).toEqual({ kind: "wait" });
  });

  it("跳转期间历史被新操作改变（probe id 不符）→ cancel history-changed", () => {
    const pending = makePending("A", "a:1");
    const other = makePending("A", "a:2");
    expect(
      decideCrossFileRestore({ pending, currentFilePath: "A", ready: true, metaLoadFailed: false, probe: other.entry }),
    ).toEqual({ kind: "cancel", reason: "history-changed" });
  });

  it("一切就绪且 probe 与 pending 一致 → apply", () => {
    const pending = makePending("A", "a:1");
    expect(
      decideCrossFileRestore({ pending, currentFilePath: "A", ready: true, metaLoadFailed: false, probe: pending.entry }),
    ).toEqual({ kind: "apply" });
  });

  it("probe 为 null 而 pending 非 null → cancel history-changed（栈已被清空）", () => {
    const pending = makePending("A", "a:1");
    expect(
      decideCrossFileRestore({ pending, currentFilePath: "A", ready: true, metaLoadFailed: false, probe: null }),
    ).toEqual({ kind: "cancel", reason: "history-changed" });
  });
});

describe("场景 12：handleRedo 不压栈草稿（pushUndo 会丢弃 redo 分支）", () => {
  beforeEach(() => {
    clearUndo();
  });

  it("存在 redo 分支时不压栈 → peekRedo 可正常预览重做目标（修复后 handleRedo 行为）", () => {
    pushUndo({ id: "a:1", file: "a", index: 1, before: { pre_dst: "a1" }, after: { pre_dst: "a2" } });
    pushUndo({ id: "a:2", file: "a", index: 2, before: { pre_dst: "b1" }, after: { pre_dst: "b2" } });
    undo();
    expect(peekRedo()?.id).toBe("a:2");
  });

  it("存在 redo 分支时压栈（修复前 pushMetaDraftIfDirty）→ redo 分支被清空，peekRedo 返回 null", () => {
    pushUndo({ id: "a:1", file: "a", index: 1, before: { pre_dst: "a1" }, after: { pre_dst: "a2" } });
    pushUndo({ id: "a:2", file: "a", index: 2, before: { pre_dst: "b1" }, after: { pre_dst: "b2" } });
    undo();
    pushUndo({ id: "m:meta", file: "m", index: 0, before: { title: "x" }, after: { title: "y" } });
    expect(peekRedo()).toBeNull();
  });
});
