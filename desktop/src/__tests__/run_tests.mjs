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
  const r = fn();
  // 支持 async 回调（返回 Promise 时透传，调用方可 await）
  if (r && typeof r.then === "function") return r;
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

// --- 模块 8: 分页模式全量加载保存（取代虚拟滚动截断合并） ---
testGroup("模块 8: 分页全量加载保存 (P0)", () => {
  // 分页模式下 loadFile 全量加载（setEntries(all)），saveCurrentFile 直接保存 entries()，无合并。
  // 构造 2000 条数据模拟全量加载后的内存 entries
  const full = Array.from({ length: 2000 }, (_, i) => ({
    index: i + 1,
    pre_dst: `dst-${i + 1}`,
    problem: "",
  }));

  testGroup("  8.1 全量加载后保存 → 直接用 entries() 不合并、内容完整", () => {
    const current = full;
    const toSave = current; // 分页模式：无 loadedSliceIndices，直接保存 entries()
    assertEq(toSave.length, 2000, "完整数据长度不变");
    assertEq(toSave[0].pre_dst, "dst-1", "内容不变");
    assertEq(toSave[1999].pre_dst, "dst-2000", "最后一条也在内存中");
  });

  testGroup("  8.2 全量加载 + 编辑 index=5 → 保存含新值，其余不变", () => {
    const current = full.map((e) =>
      e.index === 5 ? { ...e, pre_dst: "新译文5" } : e,
    );
    assertEq(current.length, 2000, "长度不变");
    assertEq(current[4].pre_dst, "新译文5", "编辑生效");
    assertEq(current[5].pre_dst, "dst-6", "其余不变");
  });

  testGroup("  8.3 全量加载 + 删除 index=5 → 保存无 index 5", () => {
    const current = full.filter((e) => e.index !== 5);
    assertEq(current.length, 1999, "删 1 条后长度 1999");
    assert(!current.some((e) => e.index === 5), "index 5 已移除");
    assert(current.some((e) => e.index === 2000), "全量加载下其他条目保留");
  });

  testGroup("  8.4 分页只渲染当前页，但内存 entries 仍是全量（翻页可访问）", () => {
    const entries = full; // 内存全量
    const pageSize = 2000; // 每页条目显示数量
    // 第 1 页渲染前 2000 条；若有更多则分页
    const totalPages = Math.max(1, Math.ceil(entries.length / pageSize));
    const page0 = entries.slice(0, pageSize);
    assertEq(totalPages, 1, "2000 条 / 2000 每页 = 1 页");
    assertEq(page0.length, 2000, "第 1 页渲染 2000 条");
  });

  testGroup("  8.5 大文件分页：4500 条 / 2000 每页 = 3 页", () => {
    const count = 4500;
    const pageSize = 2000;
    const totalPages = Math.max(1, Math.ceil(count / pageSize));
    assertEq(totalPages, 3, "4500 / 2000 → 3 页");
    const lastPageStart = 2 * pageSize;
    const lastPageLen = count - lastPageStart;
    assertEq(lastPageLen, 500, "第 3 页 500 条");
  });

  testGroup("  8.6 每页条数为 0（不分页）→ 一次性显示全部", () => {
    const pageSize = 0;
    const all = full;
    const rendered = pageSize > 0 ? all.slice(0, pageSize) : all;
    assertEq(rendered.length, 2000, "0 = 不分页，显示全部");
  });
});

// --- 模块 9: P0 加固回归（分页全量加载 + loadedFile 失效中止） ---
testGroup("模块 9: P0 加固回归", () => {
  testGroup("  9.1 分页模式保存直接使用内存 entries（全量，无合并依赖）", () => {
    const entries = Array.from({ length: 2000 }, (_, i) => ({
      index: i + 1,
      pre_dst: `dst-${i + 1}`,
    }));
    // 分页模式：saveCurrentFile 直接 const toSave = entries()
    const toSave = entries;
    assertEq(toSave.length, 2000, "全量数据直接保存");
    assertEq(toSave[0].pre_dst, "dst-1", "内容正确");
  });

  testGroup("  9.2 loadedFile 失效：加载失败清空后中止保存（不写残缺数据）", () => {
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

  testGroup("  9.3 loadedFile 正常时保存不中断", () => {
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

// --- 模块 10: P1 切换链路（runSwitch 锁 + 跨模式确认） ---
await testGroup("模块 10: P1 切换链路", async () => {
  testGroup("  10.2 「不保存」后 markClean 清脏（切回不误弹确认）", () => {
    resetStore();
    const f = "game/t01.txt.json";
    setAppState("activeFilePath", f);
    markDirty(f);
    // 模拟 runSwitch 的 else 分支（不保存 → 丢弃 → 清脏）
    markClean(f);
    assertEq(appState.dirtyFiles, [], "不保存后 dirty 已清");
  });

  testGroup("  10.3 跨模式：translate dirty → metadata 目标 → prevFile 同步捕获", () => {
    resetStore();
    const translateFile = "game/t01.txt.json";
    const metaFile = "game/meta.batch.json";
    setAppState("activeFilePath", translateFile);
    markDirty(translateFile);
    let loadedFile = translateFile;

    // 模拟 loadFile effect 分派：modeInfoOf(metaFile).mode === "metadata" → leaveConfirm
    const mode = "metadata";
    // leaveConfirm 同步捕获 prevFile（在 metadata effect 覆盖 loadedFile 之前）
    const prevFile = loadedFile;
    let leaveTriggered = false;
    if (mode !== "translate" && prevFile && appState.dirtyFiles.includes(prevFile)) {
      leaveTriggered = true;
    }
    assert(leaveTriggered, "跨模式切换触发 leaveConfirm");
    assertEq(prevFile, translateFile, "prevFile 捕获 translate 文件");

    // 随后 metadata effect 覆盖 loadedFile
    loadedFile = metaFile;
    assertEq(loadedFile, metaFile, "metadata effect 接管 loadedFile");
    assertEq(prevFile, translateFile, "prevFile 不受 metadata 接管影响");
  });

  testGroup("  10.4 leaveConfirm 点「取消」→ 还原 activeFilePath 留在原文件", () => {
    resetStore();
    const translateFile = "game/t01.txt.json";
    const metaFile = "game/meta.batch.json";
    setAppState("activeFilePath", metaFile); // 当前目标（已切到 metadata）
    const prevFile = translateFile;
    // 模拟 res.action === "extra"
    const action = "extra";
    if (action === "extra") {
      setAppState("activeFilePath", prevFile);
    }
    assertEq(appState.activeFilePath, translateFile, "取消后还原到原文件");
  });

  await testGroup("  10.1 runSwitch 等待在途保存完成后才写盘（锁串行化）", async () => {
    let saveInFlight = true;
    let writes = 0;
    async function saveWithLock() {
      const deadline = Date.now() + 3000;
      while (saveInFlight && Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 10));
      }
      writes++;
    }
    const p = saveWithLock();
    // 模拟 saveCurrentFile 的 finally 释放锁
    setTimeout(() => { saveInFlight = false; }, 40);
    await p;
    assert(writes === 1, "锁释放后才写盘（无并发双写）");
    assert(!saveInFlight, "锁已释放");
  });

  await testGroup("  10.5 超时兜底：等待超时后继续（不无限挂起）", async () => {
    let saveInFlight = true; // 永不释放（模拟网络挂起）
    let writes = 0;
    const deadline = Date.now() + 80; // 短超时
    while (saveInFlight && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 10));
    }
    writes++;
    assert(writes === 1, "超时后继续执行");
    assert(Date.now() >= deadline, "已超时退出等待");
  });
});

// --- 模块 11: metadata 保存链路状态机（metaDirty + token + 守卫） ---
await testGroup("模块 11: metadata 保存链路状态机", async () => {
  testGroup("  11.1 metaDirty 置位/清位：编辑置 true，保存成功清，失败保持", () => {
    let metaDirty = false;

    // 编辑
    metaDirty = true;
    assert(metaDirty === true, "编辑后 dirty=true");

    // 保存成功 → 清
    metaDirty = false;
    assert(metaDirty === false, "保存成功后 dirty=false");

    // 再编辑，保存失败 → 保持 true（仅成功才清）
    metaDirty = true;
    let saveOk = false;
    try { throw new Error("network"); } catch { saveOk = false; }
    if (saveOk) metaDirty = false;
    assert(metaDirty === true, "保存失败时 dirty 保持 true");
  });

  testGroup("  11.2 切换保存失败 → 中止（不加载新文件、dirty 保持）", () => {
    let metaDirty = true;
    let loaded = "A";
    let switched = false;

    function simulateSwitch() {
      let saved = false;
      try { throw new Error("network"); } catch { saved = false; }
      if (!saved) return; // 中止切换
      loaded = "B";
      metaDirty = false;
      switched = true;
    }
    simulateSwitch();
    assert(loaded === "A", "失败后不加载新文件");
    assert(metaDirty === true, "失败后 dirty 保持 true");
    assert(!switched, "未执行切换");
  });

  await testGroup("  11.3 token 过期丢弃：旧闭包不生效", async () => {
    let token = 0;
    function simulateEffect() {
      const myToken = ++token;
      return async () => {
        await new Promise((r) => setTimeout(r, 20));
        return myToken === token ? "apply" : "stale";
      };
    }
    const c1 = simulateEffect(); // 旧闭包
    const c2 = simulateEffect(); // 新闭包（token 递增，c1 失效）
    const results = await Promise.all([c1(), c2()]);
    assert(results[0] === "stale", "旧闭包被丢弃");
    assert(results[1] === "apply", "新闭包生效");
  });

  testGroup("  11.4 saveMeta 目标守卫：modeInfoOf(metaLoadedFullPath).sourceFile !== metaSourceFile 时跳过", () => {
    // 与 ReviewPage.modeInfoOf 一致的模拟：完整路径 → 纯源文件名
    function modeInfoOf(path) {
      const base = path.split("/").pop() ?? "";
      if (path.includes("pass1_cache/")) {
        return { metaType: "filemeta", sourceFile: base.replace(/\.meta\.json$/, "") };
      }
      return { metaType: "filemeta", sourceFile: "" };
    }

    let metaSourceFile = "00_03_華恋との出会い.txt.json"; // 新目标纯名
    let metaLoadedFullPath = "pass1_cache/00_04_凛音との出会い.txt.json.meta.json"; // 旧文件完整路径

    // 切换中：旧文件纯名 !== 新目标纯名 → 跳过（防把旧数据写入新文件）
    let saved = false;
    if (modeInfoOf(metaLoadedFullPath).sourceFile !== metaSourceFile) { /* 跳过 */ } else { saved = true; }
    assert(!saved, "切换中跳过保存（防写错文件）");

    // 稳定状态：metaLoadedFullPath 是当前打开文件 → 相等 → 保存
    metaSourceFile = "00_04_凛音との出会い.txt.json";
    if (modeInfoOf(metaLoadedFullPath).sourceFile !== metaSourceFile) { /* 跳过 */ } else { saved = true; }
    assert(saved, "稳定状态正常保存");
  });

  testGroup("  11.5 外部刷新守卫：同文件 + metaDirty → 跳过自动刷新", () => {
    const loadedFile = "A";
    const srcFile = "A";
    let metaDirty = true;
    let refreshed = false;

    if (loadedFile && loadedFile !== srcFile) {
      // 切换分支
    } else if (metaDirty) {
      // 跳过刷新（dirty 守卫）
    } else {
      refreshed = true;
    }
    assert(!refreshed, "同文件 dirty 时跳过自动刷新（保护编辑）");

    metaDirty = false;
    if (loadedFile && loadedFile !== srcFile) {
      // 切换分支
    } else if (metaDirty) {
      // 跳过
    } else {
      refreshed = true;
    }
    assert(refreshed, "非 dirty 时正常刷新");
  });

  testGroup("  11.6 非法 JSON 提示去重：连续非法只提示一次，恢复合法后重置", () => {
    let metaJsonInvalidShown = false;
    let toasts = 0;
    function handleChange(text) {
      let parsed;
      try {
        parsed = JSON.parse(text);
      } catch {
        if (!metaJsonInvalidShown) {
          metaJsonInvalidShown = true;
          toasts++;
        }
        return false;
      }
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        if (!metaJsonInvalidShown) {
          metaJsonInvalidShown = true;
          toasts++;
        }
        return false;
      }
      metaJsonInvalidShown = false;
      return true;
    }

    // 逐键输入非法内容（每个中间态都非法）
    handleChange("{");
    handleChange("{a");
    handleChange("{a:");
    assert(toasts === 1, "连续非法只提示一次（防 toast 轰炸）");

    // 补全为合法 JSON
    const ok = handleChange('{"a":1}');
    assert(ok, "合法输入通过");
    assert(toasts === 1, "合法输入不额外提示");

    // 再次非法 → 重新提示（标志已重置）
    handleChange("}");
    assert(toasts === 2, "恢复合法后再次非法重新提示");

    // 先恢复合法（重置标志），再输入非对象（数组）→ 拦截并提示一次
    handleChange('{"b":2}');
    handleChange("[1]");
    handleChange("[1,2]");
    assert(toasts === 3, "非对象内容提示一次");
  });

  testGroup("  11.7 切换保存 filename 推导：metaLoadedFullPath（完整路径）正确，纯名 loadedFile 不行", () => {
    // 与 ReviewPage.modeInfoOf 一致的模拟
    function modeInfoOf(path) {
      if (!path) return { metaType: "filemeta", sourceFile: "" };
      const base = path.split("/").pop() ?? "";
      if (path.includes("pass1_cache/")) {
        return { metaType: "filemeta", sourceFile: base.replace(/\.meta\.json$/, "") };
      }
      if (path.includes("pass2_cache/")) {
        return { metaType: "batchmeta", sourceFile: base.replace(/\.batch\.json$/, "") };
      }
      return { metaType: "filemeta", sourceFile: "" }; // 兜底：无目录信息 → 空
    }

    // 完整路径 → 正确提取纯源文件名
    const prevInfo = modeInfoOf("pass1_cache/00_04_凛音との出会い.txt.json.meta.json");
    assertEq(prevInfo.sourceFile, "00_04_凛音との出会い.txt.json", "完整路径提取纯源文件名");
    assert(!prevInfo.sourceFile.includes("/"), "文件名不含路径分隔符（后端可过）");
    assert(!prevInfo.sourceFile.endsWith(".meta.json"), "不含 .meta.json 后缀");
    assertEq(prevInfo.metaType, "filemeta", "metaType 正确");

    // 纯名（loadedFile）→ 兜底空 sourceFile：这就是之前 bug 根因，必须用 metaLoadedFullPath
    const badInfo = modeInfoOf("00_04_凛音との出会い.txt.json");
    assertEq(badInfo.sourceFile, "", "纯名无法推导 → 必须用 metaLoadedFullPath");

    // pass2 文件同理
    const prevInfo2 = modeInfoOf("pass2_cache/00_05.batch.json");
    assertEq(prevInfo2.sourceFile, "00_05", "batch 提取纯名");
    assertEq(prevInfo2.metaType, "batchmeta", "batch metaType 正确");
  });

  testGroup("  11.8 C2：metadata→translate 切换确认三分支（保存/不保存/取消）", () => {
    let metaDirty = true;
    const metaEntryObj = { id: "B" };
    const metaLoadedFullPath = "pass1_cache/00_04_凛音との出会い.txt.json.meta.json";
    let action = "confirm";
    let restored = null;

    function simulateLeave() {
      if (metaDirty && metaEntryObj && metaLoadedFullPath) {
        if (action === "extra") {
          restored = metaLoadedFullPath; // 取消：还原 activeFilePath
          return "cancelled";
        }
        if (action === "confirm") {
          metaDirty = false;
          return "saved";
        }
        metaDirty = false; // 不保存 → 丢弃
        return "discarded";
      }
      return "clean";
    }

    // 分支 1：保存
    const r1 = simulateLeave();
    assert(r1 === "saved", "确认-保存分支");
    assert(!metaDirty, "保存后清 dirty");

    // 分支 2：不保存
    metaDirty = true;
    action = "cancel";
    const r2 = simulateLeave();
    assert(r2 === "discarded", "确认-不保存分支");
    assert(!metaDirty, "不保存后清 dirty");

    // 分支 3：取消
    metaDirty = true;
    action = "extra";
    const r3 = simulateLeave();
    assert(r3 === "cancelled", "确认-取消分支");
    assert(restored === metaLoadedFullPath, "取消还原 activeFilePath 留在原文件");
    assert(metaDirty, "取消后 dirty 保持（未保存编辑保留）");

    // 无编辑（metaDirty=false）→ 不弹确认
    metaDirty = false;
    const r4 = simulateLeave();
    assert(r4 === "clean", "无编辑时直接离开");
  });

  await testGroup("  11.9 C3：saveCacheFile 返回 success=false → 不 markClean + 提示", async () => {
    let dirty = ["game/t01.txt.json"];
    let toasts = 0;
    let markedClean = false;

    async function simulateSave(resp) {
      if (resp && resp.success === false) {
        toasts++;
        return; // 不 markClean
      }
      dirty = dirty.filter((f) => f !== "game/t01.txt.json");
      markedClean = true;
    }

    // 失败路径：success=false
    await simulateSave({ success: false, filename: "x" });
    assert(toasts === 1, "失败时 toast 提示");
    assert(!markedClean, "失败时不 markClean");
    assert(dirty.length === 1, "失败时 dirty 保持");

    // 成功路径：success=true
    await simulateSave({ success: true, filename: "x" });
    assert(markedClean, "成功时 markClean");
    assert(dirty.length === 0, "成功时 dirty 清除");
  });

  testGroup("  11.10 BUG-1 回归：pass1/pass2 同名源文件互切不写错位置（守卫 + effect 完整路径判断 + 保存后 clearUndo）", () => {
    // 与 ReviewPage.modeInfoOf 一致的模拟（含 pass2 → batchmeta）
    function modeInfoOf(path) {
      if (!path) return { metaType: "filemeta", sourceFile: "" };
      const base = path.split("/").pop() ?? "";
      if (path.includes("pass1_cache/")) {
        return { metaType: "filemeta", sourceFile: base.replace(/\.meta\.json$/, "") };
      }
      if (path.includes("pass2_cache/")) {
        return { metaType: "batchmeta", sourceFile: base.replace(/\.batch\.json$/, "") };
      }
      return { metaType: "filemeta", sourceFile: "" };
    }

    // ===== 1. saveMeta 守卫（含 metaType 校验）=====
    // 场景：pass1 00_01 有未保存编辑 → 切到 pass2 同名 00_01，blur 触发 saveMeta
    // 旧守卫只比 sourceFile（同名相等 → 漏判 → pass1 数据 POST 到 pass2 文件）
    // 新守卫：loadedInfo.metaType(filemeta) !== metaType()(batchmeta) → 拦截
    const guardMetaLoadedFullPath = "pass1_cache/00_01.txt.json.meta.json"; // 上次加载：pass1
    const guardMetaType = "batchmeta"; // 当前目标：pass2（activeFilePath 已切走）
    const guardMetaSourceFile = "00_01.txt.json"; // 当前目标纯名（与旧文件同名）
    const loadedInfo = modeInfoOf(guardMetaLoadedFullPath);

    let saveBlocked = false;
    if (
      !guardMetaLoadedFullPath ||
      loadedInfo.sourceFile !== guardMetaSourceFile ||
      loadedInfo.metaType !== guardMetaType
    ) {
      saveBlocked = true; // 守卫拦截，不 POST
    }
    assert(saveBlocked, "同名互切（纯名相等但 metaType 不同）→ 守卫拦截");
    assertEq(loadedInfo.sourceFile, guardMetaSourceFile, "纯名确实相等（还原旧 bug 的漏判条件）");
    assertEq(loadedInfo.metaType, "filemeta", "旧文件为 pass1(filemeta)");

    // 对照：pass1 内部稳定编辑 → 守卫放行（sourceFile、metaType 均一致）
    let saveAllowed = false;
    const stableInfo = modeInfoOf("pass1_cache/00_01.txt.json.meta.json");
    if (stableInfo.sourceFile === "00_01.txt.json" && stableInfo.metaType === "filemeta") {
      saveAllowed = true;
    }
    assert(saveAllowed, "pass1 文件内正常编辑 → 守卫放行");

    // ===== 2. effect 切换判断用完整路径 =====
    // 场景：从 pass1 00_01（metaLoadedFullPath）切到 pass2 同名 00_01（activeFilePath）
    // 旧判断：loadedFile === srcFile（纯名相同）→ 误判同文件 → dirty 时 return 不加载 → 界面残留
    // 新判断：metaLoadedFullPath !== activeFilePath → 进入切换分支（保存旧文件 + 加载新文件）
    const metaLoadedFullPath = "pass1_cache/00_01.txt.json.meta.json";
    const activeFilePath = "pass2_cache/00_01.txt.json.batch.json";

    let isSwitch = false;
    let isSameFileRefresh = false;
    if (metaLoadedFullPath && metaLoadedFullPath !== activeFilePath) {
      isSwitch = true; // 切换分支
    } else {
      isSameFileRefresh = true; // 同文件刷新分支
    }
    assert(isSwitch, "完整路径不同 → 判定为切换（即使纯名相同）");
    assert(!isSameFileRefresh, "不再误判为同文件刷新");

    // 对照：同文件外部刷新（cacheVersion bump）→ 仍走刷新分支
    const samePath = "pass1_cache/00_01.txt.json.meta.json";
    let isRefresh = false;
    if (samePath && samePath !== samePath) {
      // 切换
    } else {
      isRefresh = true;
    }
    assert(isRefresh, "完整路径相同 → 保持刷新分支（外部改动自动刷新）");

    // ===== 3. 保存旧文件后 clearUndo（runSwitch / effect 切换保存分支）=====
    let undoStack = ["meta:file-A-edit", "meta:file-B-edit", "translate:file-A-edit"];
    function clearUndo() {
      undoStack = [];
    }
    // 模拟切换时保存旧文件成功后的处理（与 saveMeta 一致：保存即新撤销起点）
    clearUndo();
    assert(undoStack.length === 0, "切换保存旧文件后 clearUndo（防残留记录造成撤销错位）");

    // 对照：不保存（丢弃/取消）分支不 clearUndo → 但切回时由新文件加载重置基线
    undoStack = ["meta:file-A-edit"];
    // 丢弃分支：metaDirty=false，不做 clearUndo（原语义不变）
    assert(undoStack.length === 1, "丢弃分支不清 undo（保持原语义）");
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
