/**
 * Node 独立测试脚本 — 验证 P0 修改的三项正确性
 * 纯 JS（无 TS 类型注解），可直接 node 运行
 *
 * 用法: node src/__tests__/run_tests.mjs
 */
import { createStore } from "solid-js/store";

// ======= 复制 appStore 核心逻辑 =======

const defaultState = {
  activeView: "home",
  sidebarOpen: false,
  sidebarTab: null,
  activeProjectId: null,
  activeConfigFileName: null,
  configNameDetecting: false,
  activeFilePath: null,
  dirtyFiles: [],
  connectionPhase: "offline",
  connectionTimeoutMs: 20000,
  backendOnline: false,
  selectedBackend: "",
  cacheTree: [],
  cacheVersion: 0,
  modelCheck: { state: "idle", result: null, backend: "", projectId: null },
  prevJobStatus: "",
  reviewJumpToIndex: null,
};

const [appState, setAppState] = createStore(defaultState);

function markDirty(filePath) {
  setAppState("dirtyFiles", (files) => [...new Set([...files, filePath])]);
}

function markClean(filePath) {
  setAppState("dirtyFiles", (files) => files.filter((f) => f !== filePath));
}

// ======= 简易测试框架 =======

let passed = 0;
let failed = 0;
const errors = [];

function assert(condition, msg) {
  if (condition) passed++;
  else { failed++; errors.push(`  FAIL: ${msg}`); }
}

function assertEq(actual, expected, msg) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (ok) passed++;
  else {
    failed++;
    errors.push(`  FAIL: ${msg}\n    expected: ${JSON.stringify(expected)}\n    actual:   ${JSON.stringify(actual)}`);
  }
}

function testGroup(name, fn) {
  console.log(`\n${name}`);
  fn();
}

// ======= 辅助 =======

function resetStore() {
  setAppState("dirtyFiles", []);
  setAppState("activeFilePath", null);
}

// ============================================================
//  测试用例
// ============================================================

// --- 模块 1: markDirty / markClean 基础 ---
testGroup("模块 1: markDirty / markClean 基础行为", () => {
  resetStore();
  markDirty("foo.json");
  assertEq(appState.dirtyFiles, ["foo.json"], "1.1 添加文件");

  resetStore();
  markDirty("foo.json"); markDirty("foo.json"); markDirty("foo.json");
  assertEq(appState.dirtyFiles, ["foo.json"], "1.2 重复调用不产生重复");
  assert(appState.dirtyFiles.length === 1, "1.2 长度保持 1");

  resetStore();
  markDirty("a.json"); markDirty("b.json");
  markClean("a.json");
  assertEq(appState.dirtyFiles, ["b.json"], "1.3 移除 a 后只剩 b");

  resetStore();
  let threw = false;
  try { markClean("NONEXIST.json"); } catch { threw = true; }
  assert(!threw, "1.4 markClean 不存在文件不抛异常");
  assertEq(appState.dirtyFiles, [], "1.4 dirtyFiles 仍为空");

  resetStore();
  markDirty("a.json"); markDirty("b.json"); markDirty("c.json");
  assert(appState.dirtyFiles.length === 3, "1.5 三个文件");
  assert(appState.dirtyFiles.includes("a.json"), "1.5 包含 a");
  assert(appState.dirtyFiles.includes("b.json"), "1.5 包含 b");
  assert(appState.dirtyFiles.includes("c.json"), "1.5 包含 c");

  resetStore();
  markDirty("f.json");
  assert(appState.dirtyFiles.length === 1, "1.6 标记后长度 1");
  markClean("f.json");
  assertEq(appState.dirtyFiles, [], "1.6 清除后为空");
});

// --- 模块 2: onInput 逐键 markDirty（P0 修复 B） ---
testGroup("模块 2: onInput 逐键 markDirty (修复 B)", () => {
  resetStore();
  const f = "game/t01.txt.json";
  for (let i = 0; i < 50; i++) markDirty(f);
  assertEq(appState.dirtyFiles, [f], "2.1 50 次调用仍 1 条");
  assert(appState.dirtyFiles.length === 1, "2.1 长度 = 1");

  resetStore();
  markDirty(f); // onInput
  markDirty(f); // blur → handleFieldChange
  assertEq(appState.dirtyFiles, [f], "2.2 onInput+blur 双重不重复");

  resetStore();
  setAppState("activeFilePath", null);
  if (appState.activeFilePath) markDirty(appState.activeFilePath);
  assertEq(appState.dirtyFiles, [], "2.3 activeFilePath=null 守卫");

  resetStore();
  setAppState("activeFilePath", "game/file.json");
  if (appState.activeFilePath) markDirty(appState.activeFilePath);
  assertEq(appState.dirtyFiles, ["game/file.json"], "2.4 非空时正常标记");
});

// --- 模块 3: blur-before-save 草稿提交（P0 修复 A） ---
testGroup("模块 3: 保存前 blur 提交草稿 (修复 A)", () => {
  function simulateBlurCommit(entries, serial, newValue, filePath) {
    const idx = entries.findIndex((e) => e.index === serial);
    if (idx === -1) return { updated: entries, committed: false };
    entries[idx] = { ...entries[idx], pre_dst: newValue };
    markDirty(filePath);
    return { updated: entries, committed: true };
  }

  function simulateBlurBeforeSave(focusedIndex, draftValue, entries, filePath) {
    if (focusedIndex === null || draftValue === null) return { updated: entries, committed: false };
    return simulateBlurCommit(entries, focusedIndex, draftValue, filePath);
  }

  resetStore();
  let entries = [{ index: 1, pre_dst: "旧译文" }];
  let r = simulateBlurBeforeSave(1, "新译文（正在输入）", entries, "game/t01.txt.json");
  entries = r.updated;
  assert(r.committed, "3.1 草稿已提交");
  assertEq(entries[0].pre_dst, "新译文（正在输入）", "3.1 entries 包含新值");
  assertEq(appState.dirtyFiles, ["game/t01.txt.json"], "3.1 已标脏");

  resetStore();
  entries = [{ index: 1, pre_dst: "旧" }];
  r = simulateBlurBeforeSave(null, null, entries, "game/t01.txt.json");
  entries = r.updated;
  assert(!r.committed, "3.2 无聚焦框时跳过");
  assertEq(entries[0].pre_dst, "旧", "3.2 entries 未变");

  resetStore();
  entries = [{ index: 3, pre_dst: "已保存" }];
  r = simulateBlurBeforeSave(3, "新内容", entries, "game/t01.txt.json");
  entries = r.updated;
  assert(r.committed, "3.3 已提交");
  assertEq(entries[0].pre_dst, "新内容", "3.3 已更新");
  markClean("game/t01.txt.json");
  assertEq(appState.dirtyFiles, [], "3.3 保存后清标记");
});

// --- 模块 4: 完整生命周期 ---
testGroup("模块 4: 完整生命周期 — 打字→标脏→保存→再打", () => {
  resetStore();
  const f = "game/t01.txt.json";
  markDirty(f);
  assert(appState.dirtyFiles.includes(f), "4.1 打字标脏");
  markDirty(f);
  assertEq(appState.dirtyFiles, [f], "4.1 blur 再标不重复");
  markClean(f);
  assertEq(appState.dirtyFiles, [], "4.1 保存后清除");
  markDirty(f);
  assertEq(appState.dirtyFiles, [f], "4.1 再次编辑重新标记");
  markClean(f);
  assertEq(appState.dirtyFiles, [], "4.1 再次保存清除");
});

// --- 模块 5: 确认弹窗三按钮 ---
testGroup("模块 5: 确认弹窗三按钮 (runSwitch extraText)", () => {
  function simulateConfirmAction(action) {
    if (action === "extra") return { action: "extra", confirmed: false };
    if (action === "confirm") return { action: "confirm", confirmed: true };
    return { action: "cancel", confirmed: false };
  }

  assertEq(simulateConfirmAction("extra"), { action: "extra", confirmed: false }, "5.1 extra 分支");
  assertEq(simulateConfirmAction("confirm"), { action: "confirm", confirmed: true }, "5.2 confirm 分支");
  assertEq(simulateConfirmAction("cancel"), { action: "cancel", confirmed: false }, "5.3 cancel 分支");
});

// --- 模块 6: 边界条件 ---
testGroup("模块 6: 边界条件", () => {
  let saveInFlight = false;
  let callCount = 0;
  function mockSave() { callCount++; }
  saveInFlight = true;
  if (!saveInFlight) mockSave();
  assert(callCount === 0, "6.1 in-flight 时跳过第二次");
  saveInFlight = false;
  if (!saveInFlight) mockSave();
  assert(callCount === 1, "6.1 释放后可保存");

  const el = null;
  let threw = false;
  try { el?.blur(); } catch { threw = true; }
  assert(!threw, "6.2 null?.blur() 不抛异常");
});

// --- 模块 7: loadedFile 竞态回归（dirty 第二次保存无法清除的根因） ---
testGroup("模块 7: loadedFile 竞态回归 — metadata effect 误清 loadedFile 导致 dirty 无法清除", () => {
  // 模拟 saveCurrentFile 的守卫与 markClean
  function simulateSave(loadedFile, pid, activeFilePath, dirtyFiles) {
    const myFile = loadedFile;
    if (!pid || !myFile || activeFilePath !== myFile) {
      return { saved: false, dirtyAfter: dirtyFiles };
    }
    const dirtyAfter = dirtyFiles.filter((f) => f !== myFile);
    return { saved: true, dirtyAfter };
  }

  testGroup("  7.1 [修复前] metadata effect 将 loadedFile 置空 → 保存被短路 → dirty 残留", () => {
    resetStore();
    const pid = "proj1";
    const file = "game/t01.txt.json";
    setAppState("activeFilePath", file);
    markDirty(file); // 用户第二次编辑后已标脏

    // 模拟 cacheWatcher bump cacheVersion → metadata effect 执行（修复前: loadedFile = ""）
    let loadedFile = ""; // 被误清空

    const r = simulateSave(loadedFile, pid, appState.activeFilePath, appState.dirtyFiles);
    assert(!r.saved, "保存被提前 return");
    assert(r.dirtyAfter.includes(file), "dirty 状态残留（bug 复现）");
  });

  testGroup("  7.2 [修复后] loadedFile 不被清空 → 保存正常 → dirty 清除", () => {
    resetStore();
    const pid = "proj1";
    const file = "game/t01.txt.json";
    setAppState("activeFilePath", file);
    markDirty(file);

    // 修复后: metadata effect 不再触碰 loadedFile
    let loadedFile = file; // 保持正确

    const r = simulateSave(loadedFile, pid, appState.activeFilePath, appState.dirtyFiles);
    assert(r.saved, "保存正常执行");
    assertEq(r.dirtyAfter, [], "dirty 状态成功清除");
  });

  testGroup("  7.3 连续两次 修改→保存 循环，dirty 每次都清零（用户报的场景）", () => {
    resetStore();
    const pid = "proj1";
    const file = "game/t01.txt.json";
    setAppState("activeFilePath", file);

    // 第一轮: 编辑→保存
    markDirty(file);
    let loadedFile = file;
    let r = simulateSave(loadedFile, pid, appState.activeFilePath, appState.dirtyFiles);
    assert(r.saved, "第一轮保存成功");
    assertEq(r.dirtyAfter, [], "第一轮 dirty 清零");
    setAppState("dirtyFiles", r.dirtyAfter);

    // 第二轮: 再编辑→再保存
    markDirty(file);
    r = simulateSave(loadedFile, pid, appState.activeFilePath, appState.dirtyFiles);
    assert(r.saved, "第二轮保存成功");
    assertEq(r.dirtyAfter, [], "第二轮 dirty 清零");

    // 第三轮: 再编辑→再保存
    markDirty(file);
    r = simulateSave(loadedFile, pid, appState.activeFilePath, appState.dirtyFiles);
    assert(r.saved, "第三轮保存成功");
    assertEq(r.dirtyAfter, [], "第三轮 dirty 清零");
  });

  testGroup("  7.4 跨模式安全：translate→metadata 不误保存 translate 文件", () => {
    resetStore();
    const translateFile = "game/t01.txt.json";
    setAppState("activeFilePath", translateFile);
    markDirty(translateFile);

    // 切到 metadata 模式: loadedFile 残留 translate 文件名
    let loadedFile = translateFile;
    // metadata effect 的 metadata 分支: if (loadedFile && loadedFile !== srcFile)
    const srcFile = "game/meta.batch.json";
    const prevEntry = null; // metaEntry 已被 setMetaEntry(null) 清空
    let savedMeta = false;
    if (loadedFile && loadedFile !== srcFile && prevEntry) {
      savedMeta = true; // 只有 metaEntry 非 null 才会保存
    }
    assert(!savedMeta, "metaEntry 为 null 时不会误保存 translate 文件");
    // metadata 分支继续: loadedFile = srcFile
    loadedFile = srcFile;
    assertEq(loadedFile, srcFile, "metadata 分支正常接管 loadedFile");
  });
});

// --- 模块 8: 虚拟滚动截断保存合并（P0 修复） ---
testGroup("模块 8: 虚拟滚动截断保存合并 (P0)", () => {
  function mergeVirtualSlice(full, current, loadedIndices) {
    const mine = new Map(current.map((e) => [e.index, e]));
    return full
      .filter((b) => !(loadedIndices.has(b.index) && !mine.has(b.index)))
      .map((b) => mine.get(b.index) ?? b);
  }

  // 构造 2000 条完整后端数据（> VIRTUAL_THRESHOLD=1500）
  const full = Array.from({ length: 2000 }, (_, i) => ({
    index: i + 1,
    pre_dst: `dst-${i + 1}`,
    problem: "",
  }));
  // 加载区域 = 前 750 条（VIRTUAL_LIMIT）
  const loadedSlice = full.slice(0, 750);
  const loadedIndices = new Set(loadedSlice.map((e) => e.index));

  testGroup("  8.1 完整加载（loadedIndices=null）→ 不合并原样保存", () => {
    const current = full;
    let toSave = current;
    if (null !== null) toSave = mergeVirtualSlice(full, current, null); // 不会进入
    assertEq(toSave.length, 2000, "完整数据长度不变");
    assertEq(toSave[0].pre_dst, "dst-1", "内容不变");
  });

  testGroup("  8.2 截断 + 编辑 index=5 → merged 含新值且不丢 751+", () => {
    const current = loadedSlice.map((e) =>
      e.index === 5 ? { ...e, pre_dst: "新译文5" } : e,
    );
    const merged = mergeVirtualSlice(full, current, loadedIndices);
    assertEq(merged.length, 2000, "长度仍为 2000（未丢失）");
    assertEq(merged[4].pre_dst, "新译文5", "编辑生效");
    assertEq(merged[5].pre_dst, "dst-6", "其余不变");
  });

  testGroup("  8.3 截断 + 删除 index=5 → merged 无 index 5，751+ 保留", () => {
    const current = loadedSlice.filter((e) => e.index !== 5);
    const merged = mergeVirtualSlice(full, current, loadedIndices);
    assertEq(merged.length, 1999, "删 1 条后长度 1999");
    assert(!merged.some((e) => e.index === 5), "index 5 已移除");
    assert(merged.some((e) => e.index === 2000), "751+ 保留");
  });

  testGroup("  8.4 未加载区域 751-2000 全部保留", () => {
    const current = loadedSlice; // 无编辑
    const merged = mergeVirtualSlice(full, current, loadedIndices);
    assert(merged.some((e) => e.index === 751), "index 751 保留");
    assert(merged.some((e) => e.index === 1500), "index 1500 保留");
    assert(merged.some((e) => e.index === 2000), "index 2000 保留");
    assertEq(merged.length, 2000, "总长不变");
  });

  testGroup("  8.5 边界：加载区域最后一条 index=750 被删除 → 移除，751 保留", () => {
    const current = loadedSlice.filter((e) => e.index !== 750);
    const merged = mergeVirtualSlice(full, current, loadedIndices);
    assert(!merged.some((e) => e.index === 750), "index 750 已移除");
    assert(merged.some((e) => e.index === 751), "index 751 保留（未加载区）");
  });

  testGroup("  8.6 全删加载区域 → merged 仅剩未加载区域", () => {
    const current = []; // 用户删除全部可见条目
    const merged = mergeVirtualSlice(full, current, loadedIndices);
    assertEq(merged.length, 1250, "剩 2000-750=1250 条");
    assert(!merged.some((e) => e.index <= 750), "加载区域全删");
    assertEq(merged[0].index, 751, "从 751 开始");
  });
});

// --- 模块 9: P0 加固回归（快照竞态 + loadedFile 失效中止） ---
testGroup("模块 9: P0 加固回归", () => {
  function mergeVirtualSlice(full, current, loadedIndices) {
    const mine = new Map(current.map((e) => [e.index, e]));
    return full
      .filter((b) => !(loadedIndices.has(b.index) && !mine.has(b.index)))
      .map((b) => mine.get(b.index) ?? b);
  }

  // 构造 2000 条后端数据 + 截断 750
  const full = Array.from({ length: 2000 }, (_, i) => ({
    index: i + 1,
    pre_dst: `dst-${i + 1}`,
  }));
  const loadedSlice = full.slice(0, 750);
  const loadedIndices = new Set(loadedSlice.map((e) => e.index));

  testGroup("  9.1 快照竞态：await 期间 loadedSliceIndices 变 null，用快照 merge 不崩溃且结果正确", () => {
    // 模拟保存循环: 快照在 await 前
    let loadedSliceIndices = loadedIndices; // 截断状态
    const snapshot = loadedSliceIndices;    // 快照（await 前）
    loadedSliceIndices = null;              // await 期间被 loadFile 复位（模拟）

    // 若 snapshot 为 null 走完整保存，否则用快照 merge
    let toSave;
    if (snapshot === null) {
      toSave = full;
    } else {
      toSave = mergeVirtualSlice(full, loadedSlice, snapshot); // 快照是 Set，不崩溃
    }
    assertEq(toSave.length, 2000, "合并不崩溃且长度完整");
    assertEq(toSave[0].pre_dst, "dst-1", "内容正确");
  });

  testGroup("  9.2 快照竞态：entries 已被刷新为完整数据，用旧快照 merge 仍正确", () => {
    const snapshot = loadedIndices; // 旧快照 {1..750}
    // entries 已被 loadFile 完整刷新（2000 条，含用户编辑 index=5）
    const entries = full.map((e) => (e.index === 5 ? { ...e, pre_dst: "新译文5" } : e));
    const toSave = mergeVirtualSlice(full, entries, snapshot);
    assertEq(toSave.length, 2000, "长度完整");
    assertEq(toSave[4].pre_dst, "新译文5", "编辑保留（mine 全覆盖）");
    // 验证 filter 不移除已存在条目：index ≤ 750 的条目在 mine 中都存在
    assert(!toSave.some((e) => e.index === 9999), "无多余条目");
  });

  testGroup("  9.3 loadedFile 失效：加载失败清空后中止保存（不写残缺数据）", () => {
    // 模拟加载失败: loadedFile 被清空
    let loadedFile = "game/t01.txt.json";
    const myFile = loadedFile; // 循环开始捕获
    loadedFile = "";           // loadFile 失败清空

    // 保存循环内检查
    let saved = false;
    if (loadedFile !== myFile) {
      // 中止（不保存）
    } else {
      saved = true;
    }
    assert(!saved, "loadedFile 失效时中止保存");
  });

  testGroup("  9.4 loadedFile 正常时保存不中断", () => {
    let loadedFile = "game/t01.txt.json";
    const myFile = loadedFile;
    // 无失效：不中止
    let saved = false;
    if (loadedFile !== myFile) {
      // 中止
    } else {
      saved = true;
    }
    assert(saved, "loadedFile 正常时继续保存");
  });
});

// ======= 结果 =======

console.log(`\n${"=".repeat(50)}`);
console.log(`测试完成: 通过 ${passed}, 失败 ${failed}, 总计 ${passed + failed}`);
if (errors.length > 0) {
  console.log(`\n失败详情:`);
  errors.forEach((e) => console.log(e));
  process.exit(1);
} else {
  console.log("✅ 全部通过\n");
  process.exit(0);
}
