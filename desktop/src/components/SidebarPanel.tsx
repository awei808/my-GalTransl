import { Match, Switch, createSignal, createEffect, createMemo, onCleanup, Show, For } from "solid-js";
import { appState, setAppState } from "../stores/appStore";
import { toast } from "../stores/toastStore";
import { pushUndo } from "../stores/undoStore";
import { searchCache, replaceCache, fetchProjectProblems, deleteCacheFiles, fetchProjectFiles, revealInFileManager } from "../lib/api/project";
import { confirm } from "../stores/confirmStore";
import { startCacheWatcher, stopCacheWatcher } from "../lib/cacheWatcher";
import { getErrorMessage } from "../lib/errors";
import { problemTypesOf } from "../lib/problems";
import type {
  FileNode,
  ProblemEntry,
  CacheSearchResult,
  CacheSearchField,
} from "../lib/api/types";

/** 是否运行在 Windows 平台（Tauri WebView 的 UA 含 Windows 标识）。非 Windows 不调用后端打开，仅 Toast 提示。 */
function isWindowsPlatform(): boolean {
  if (typeof navigator === "undefined") return false;
  return /Windows/i.test(navigator.userAgent);
}

/* ── 文件浏览器（类 VSCode 文件树） ── */
function TreeIcon(props: { node: FileNode }) {
  const n = () => props.node;
  return (
    <Show
      when={n().is_file}
      fallback={
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="flex-shrink:0;color:var(--color-text-tertiary)">
          <path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z" />
        </svg>
      }
    >
      <Show
        when={n().is_metadata}
        fallback={
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="flex-shrink:0;color:var(--color-text-tertiary)">
            <path d="M6 2h8l4 4v16H6V2Zm8 0v4h4" />
          </svg>
        }
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="flex-shrink:0;color:var(--color-accent)">
          <ellipse cx="12" cy="6" rx="8" ry="3" />
          <path d="M4 6v12c0 1.66 3.58 3 8 3s8-1.34 8-3V6" />
          <path d="M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3" />
        </svg>
      </Show>
    </Show>
  );
}

function TreeNode(props: {
  node: FileNode;
  depth: number;
  expanded: Set<string>;
  selected: string | null;
  onToggle: (p: string) => void;
  onSelect: (p: string) => void;
  onContextMenu?: (e: MouseEvent, path: string, name: string, isFile: boolean, isMetadata: boolean) => void;
}) {
  const n = () => props.node;
  const isOpen = () => props.expanded.has(n().path);
  const isSel = () => props.selected === n().path;

  return (
    <div class="tree-node">
      <div
        class={`tree-row ${isSel() ? "selected" : ""} ${n().is_metadata ? "tree-row--meta" : ""}`}
        style={{ "padding-left": `${8 + props.depth * 14}px` }}
        onClick={() => {
          if (n().is_file) props.onSelect(n().path);
          else props.onToggle(n().path);
        }}
        onContextMenu={(e) => {
          // 文件与文件夹均可弹出右键菜单（含元数据文件）
          props.onContextMenu?.(
            e,
            n().path,
            n().name,
            !!n().is_file,
            !!n().is_metadata,
          );
        }}
        title={n().is_metadata ? "元数据文件（校对审核将以元数据模式打开）" : n().path}
      >
        <span class="tree-twisty">{!n().is_file ? (isOpen() ? "▾" : "▸") : ""}</span>
        <span class="tree-icon">
          <TreeIcon node={n()} />
        </span>
        <span class="tree-name">{n().name}</span>
        <Show when={n().is_metadata}>
          <span class="file-tree-tag">元数据</span>
        </Show>
        <Show when={n().is_file && n().entry_count != null}>
          <span class="file-tree-count">{n().entry_count}</span>
        </Show>
        {/* 未保存修改圆点：dirtyFiles 仅含译文条目文件（markDirty 只由 translate 操作调用） */}
        <Show when={n().is_file && appState.dirtyFiles.includes(n().path)}>
          <span class="file-tree-dirty-dot" title="有未保存的修改"></span>
        </Show>
      </div>
      <Show when={!n().is_file && isOpen()}>
        <For each={n().children ?? []}>
          {(child) => (
            <TreeNode
              node={child}
              depth={props.depth + 1}
              expanded={props.expanded}
              selected={props.selected}
              onToggle={props.onToggle}
              onSelect={props.onSelect}
              onContextMenu={props.onContextMenu}
            />
          )}
        </For>
      </Show>
    </div>
  );
}

function FileExplorer() {
  const [expanded, setExpanded] = createSignal<Set<string>>(new Set());
  const [ctxMenu, setCtxMenu] = createSignal<{ x: number; y: number; path: string; name: string; isFile: boolean; isMetadata: boolean } | null>(null);

  createEffect(() => {
    const pid = appState.activeProjectId;
    if (!pid || appState.activeView !== "review") {
      stopCacheWatcher();
      return;
    }
    startCacheWatcher(pid);
  });
  onCleanup(() => stopCacheWatcher());

  // 右键菜单：在菜单外点击 / 按 Esc / 再次右键时关闭
  createEffect(() => {
    if (!ctxMenu()) return;
    const close = () => setCtxMenu(null);
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "Escape") setCtxMenu(null);
    };
    window.addEventListener("click", close);
    window.addEventListener("contextmenu", close);
    window.addEventListener("keydown", onKey);
    onCleanup(() => {
      window.removeEventListener("click", close);
      window.removeEventListener("contextmenu", close);
      window.removeEventListener("keydown", onKey);
    });
  });

  function toggle(path: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  function openCtxMenu(e: MouseEvent, path: string, name: string, isFile: boolean, isMetadata: boolean) {
    e.preventDefault();
    e.stopPropagation();
    setCtxMenu({ x: e.clientX, y: e.clientY, path, name, isFile, isMetadata });
  }

  async function handleDeleteFile(path: string, name: string) {
    const pid = appState.activeProjectId;
    if (!pid) return;
    const result = await confirm.show({
      title: "删除文件",
      message: `确定要删除「${name}」吗？删除后需重跑流水线才能重新生成，此操作不可撤销。`,
      tone: "danger",
      confirmText: "删除",
    });
    if (!result.confirmed) return;
    try {
      const res = await deleteCacheFiles(pid, [path]);
      if (res.not_found_files && res.not_found_files.length > 0) {
        toast.error(`未找到或无法删除：${res.not_found_files.join("、")}`);
      } else {
        toast.success(`已删除：${name}`);
      }
      // 若删掉的是当前打开文件，清空选中，回到空态
      if (appState.activeFilePath === path) {
        setAppState("activeFilePath", null);
      }
      // 立即刷新文件树
      const files = await fetchProjectFiles(pid);
      setAppState("cacheTree", files.cache_files);
    } catch (err) {
      toast.error(`删除失败：${getErrorMessage(err)}`);
    }
  }

  async function handleReveal(path: string, isMetadata: boolean) {
    const pid = appState.activeProjectId;
    if (!pid) {
      toast.error("未选择项目，无法打开文件管理器");
      return;
    }
    if (!isWindowsPlatform()) {
      toast.warning("该功能暂不支持当前操作系统");
      return;
    }
    try {
      await revealInFileManager(pid, path, isMetadata);
    } catch (err) {
      toast.error(`无法打开文件管理器：${getErrorMessage(err)}`);
    }
  }

  const tree = () => appState.cacheTree;
  const selected = () => appState.activeFilePath;

  return (
    <div class="sidebar-panel">
      <div class="sidebar-header">文件浏览器</div>
      <div class="sidebar-content">
        <Show
          when={tree().length > 0}
          fallback={
            <p class="sidebar-placeholder">
              {appState.activeProjectId ? "加载中…" : "请先打开项目"}
            </p>
          }
        >
          <div class="file-tree">
            <For each={tree()}>
              {(node) => (
                <TreeNode
                  node={node}
                  depth={0}
                  expanded={expanded()}
                  selected={selected()}
                  onToggle={toggle}
                  onSelect={(p) => setAppState("activeFilePath", p)}
                  onContextMenu={openCtxMenu}
                />
              )}
            </For>
          </div>
        </Show>
      </div>
      {/* 文件/文件夹右键菜单 */}
      <Show when={ctxMenu()}>
        <div
          class="ctx-menu"
          style={{ left: `${ctxMenu()!.x}px`, top: `${ctxMenu()!.y}px` }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            class="ctx-menu__item"
            onClick={() => {
              const m = ctxMenu();
              setCtxMenu(null);
              if (m) handleReveal(m.path, m.isMetadata);
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" style="flex-shrink:0">
              <path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z" />
              <path d="M2 12h13M13 9l3 3-3 3" />
            </svg>
            在文件管理器中打开
          </button>
          <Show when={ctxMenu()!.isFile}>
            <button
              class="ctx-menu__item ctx-menu__item--danger"
              onClick={() => {
                const m = ctxMenu();
                setCtxMenu(null);
                if (m) handleDeleteFile(m.path, m.name);
              }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" style="flex-shrink:0">
                <path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14" />
              </svg>
              删除文件
            </button>
          </Show>
        </div>
      </Show>
    </div>
  );
}

/* ── 查找替换 ── */
function FindReplacePanel() {
  const [query, setQuery] = createSignal("");
  const [replaceText, setReplaceText] = createSignal("");
  const [field, setField] = createSignal<CacheSearchField>("all");
  const [results, setResults] = createSignal<CacheSearchResult[]>([]);
  const [searched, setSearched] = createSignal(false);
  const [searching, setSearching] = createSignal(false);
  const [replacing, setReplacing] = createSignal(false);

  let autoSearchTimer: ReturnType<typeof setTimeout> | undefined;

  function onQueryChange(value: string) {
    setQuery(value);
    clearTimeout(autoSearchTimer);
    autoSearchTimer = setTimeout(() => {
      if (value.trim()) handleSearch();
    }, 400);
  }

  async function handleSearch() {
    const pid = appState.activeProjectId;
    const q = query().trim();
    if (!pid || !q) {
      toast.warning("请先输入搜索内容");
      return;
    }
    setSearching(true);
    try {
      const res = await searchCache(pid, q, field(), 500);
      setResults(res.results ?? []);
      setSearched(true);
      if (res.total === 0) toast.info("未找到匹配结果");
      else toast.success(`找到 ${res.total} 个结果`);
    } catch (e) {
      toast.error(`搜索失败: ${getErrorMessage(e)}`);
    } finally {
      setSearching(false);
    }
  }

  async function handleReplace() {
    const pid = appState.activeProjectId;
    const q = query().trim();
    const r = replaceText();
    if (!pid || !q) {
      toast.warning("请先输入查找内容");
      return;
    }
    setReplacing(true);
    try {
      // 先执行 dryRun 确认数量
      const dryRes = await replaceCache(pid, q, r, "dst", true);
      if (dryRes.total_matches === 0) {
        toast.info("未找到可替换的匹配项");
        setReplacing(false);
        return;
      }

      // 记录到 undo
      for (const fd of dryRes.file_details) {
        if (fd.entries) {
          for (const e of fd.entries) {
            pushUndo({
              id: `${fd.filename}:${e.index}`,
              file: fd.filename,
              index: e.index,
              before: { pre_dst: e.pre_dst },
              after: { pre_dst: r },
              description: "查找替换",
            });
          }
        }
      }

      // 执行真实替换
      const res = await replaceCache(pid, q, r, "dst", false);
      toast.success(`已替换 ${res.total_matches} 个匹配项，涉及 ${res.total_files} 个文件`);
      // 重新搜索
      await handleSearch();
    } catch (e) {
      toast.error(`替换失败: ${getErrorMessage(e)}`);
    } finally {
      setReplacing(false);
    }
  }

  // 按文件名分组
  const grouped = () => {
    const map = new Map<string, CacheSearchResult[]>();
    for (const r of results()) {
      const list = map.get(r.filename) ?? [];
      list.push(r);
      map.set(r.filename, list);
    }
    return [...map.entries()];
  };

  function jumpToResult(r: CacheSearchResult) {
    setAppState({
      activeView: "review",
      activeFilePath: r.filename,
      sidebarTab: "explorer",
    });
  }

  return (
    <div class="sidebar-panel">
      <div class="sidebar-header">查找替换</div>
      <div class="sidebar-content">
        <div class="find-input-group">
          <input
            class="find-input"
            type="text"
            placeholder="查找"
            value={query()}
            onInput={(e) => onQueryChange(e.currentTarget.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          />
        </div>
        <div class="find-input-group">
          <input
            class="find-input"
            type="text"
            placeholder="替换为"
            value={replaceText()}
            onInput={(e) => setReplaceText(e.currentTarget.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          />
        </div>
        <div class="find-input-group">
          <select
            class="find-input"
            value={field()}
            onChange={(e) => setField(e.currentTarget.value as CacheSearchField)}
          >
            <option value="all">全部字段</option>
            <option value="src">原文</option>
            <option value="dst">译文</option>
            <option value="problem">问题</option>
          </select>
        </div>
        <div class="find-actions">
          <button class="btn btn--sm" onClick={handleSearch} disabled={searching()}>
            {searching() ? "搜索中…" : "查找"}
          </button>
          <button
            class="btn btn--sm"
            onClick={handleReplace}
            disabled={replacing() || results().length === 0}
          >
            {replacing() ? "替换中…" : "替换全部"}
          </button>
        </div>

        <Show when={searched()}>
          <Show
            when={results().length > 0}
            fallback={<p class="sidebar-placeholder">未找到匹配结果</p>}
          >
            <div class="find-results">
              <div class="find-results-header">共 {results().length} 个结果</div>
              <For each={grouped()}>
                {([filename, entries]) => (
                  <div class="find-result-group">
                    <div class="find-result-filename">{filename}</div>
                    <For each={entries}>
                      {(r) => (
                        <div class="find-result-item" onClick={() => jumpToResult(r)}>
                          <span class="find-result-index">#{r.index}</span>
                          <span class="find-result-preview">
                            {r.match_src ? r.post_src?.slice(0, 40) : ""}
                            {r.match_dst ? r.pre_dst?.slice(0, 40) : ""}
                            {r.match_problem ? r.problem?.slice(0, 40) : ""}
                          </span>
                        </div>
                      )}
                    </For>
                  </div>
                )}
              </For>
            </div>
          </Show>
        </Show>
      </div>
    </div>
  );
}

/* ── 问题检测 ── */
function ProblemList() {
  const [problems, setProblems] = createSignal<ProblemEntry[]>([]);
  const [filterType, setFilterType] = createSignal("all");
  // 已收起的文件集合（默认展开，点击文件行右侧图标收起；内联读取以启用细粒度追踪）

  const [collapsedFiles, setCollapsedFiles] = createSignal<Set<string>>(new Set());
  function toggleFile(filename: string) {
    setCollapsedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(filename)) next.delete(filename);
      else next.add(filename);
      return next;
    });
  }

  createEffect(() => {
    const pid = appState.activeProjectId;
    // 依赖 problemVersion：任一缓存文件变化都刷新问题列表（修复"非当前文件保存后不刷新"盲区）
    void appState.problemVersion;
    if (!pid || appState.sidebarTab !== "problems") {
      setProblems([]);
      return;
    }
    // 查整个项目的问题，不按当前文件过滤（问题列表按文件名分组，已足够区分）
    fetchProjectProblems(pid)
      .then((res) => {
        setProblems(res.problems ?? []);
      })
      .catch(() => {});
  });

  // 各类型出现次数统计
  const typeCounts = createMemo(() => {
    const map = new Map<string, number>();
    for (const p of problems()) {
      for (const t of problemTypesOf(p.problem)) {
        map.set(t, (map.get(t) ?? 0) + 1);
      }
    }
    return map;
  });

  // 按类型筛选后的问题列表（精确匹配类型名，避免"比日文长"误匹配"比日文长严格"）
  const filteredProblems = createMemo(() => {
    const ft = filterType();
    if (ft === "all") return problems();
    return problems().filter((p) => problemTypesOf(p.problem).includes(ft));
  });

  // 按文件名分组（基于筛选结果）
  const grouped = () => {
    const map = new Map<string, ProblemEntry[]>();
    for (const p of filteredProblems()) {
      const list = map.get(p.filename) ?? [];
      list.push(p);
      map.set(p.filename, list);
    }
    return [...map.entries()];
  };

  // 类型色标：按类型名哈希到固定色板
  const PROBLEM_COLORS = ["#e5484d", "#f76b15", "#eab308", "#3b82f6", "#8b5cf6", "#14b8a6"];
  function typeColor(name: string): string {
    let h = 0;
    for (const ch of name) h = (h * 31 + (ch.codePointAt(0) ?? 0)) >>> 0;
    return PROBLEM_COLORS[h % PROBLEM_COLORS.length];
  }

  function jumpToEntry(filename: string, index: number) {
    const patch: Record<string, unknown> = {
      activeView: "review",
      reviewJumpToIndex: index,
    };
    // 仅当切换文件时才设 activeFilePath，同文件跳转不用重载
    if (filename !== appState.activeFilePath) {
      patch.activeFilePath = filename;
    }
    setAppState(patch as any);
  }

  return (
    <div class="sidebar-panel">
      <div class="sidebar-header">问题检测</div>
      <div class="sidebar-content">
        <Show when={problems().length > 0}>
          {/* 统计条：总数 + 各类型计数 */}
          <div class="problem-stats">
            <span class="problem-stats-total">共 {problems().length} 处</span>
            <For each={[...typeCounts().entries()]}>
              {([t, n]) => (
                <span
                  class="problem-stat-chip"
                  style={{ color: typeColor(t) }}
                  onClick={() => setFilterType(filterType() === t ? "all" : t)}
                >
                  {t} {n}
                </span>
              )}
            </For>
          </div>
          {/* 类型筛选下拉 */}
          <select
            class="problem-filter-select"
            value={filterType()}
            onChange={(e) => setFilterType(e.currentTarget.value)}
          >
            <option value="all">全部类型</option>
            <For each={[...typeCounts().keys()]}>
              {(t) => <option value={t}>{t}</option>}
            </For>
          </select>
        </Show>
        <Show when={grouped().length > 0} fallback={<p class="sidebar-placeholder">暂无问题</p>}>
          <For each={grouped()}>
            {([filename, entries]) => {
              // 内联读取 collapsedFiles()：Solid 细粒度追踪需在 JSX 表达式中读取，
              // 若放在 For 回调顶部的 const 中不会被追踪，点击后 UI 不更新
              return (
                <div class="problem-group">
                  {/* 文件行：左侧文件名，右侧展开/收起切换图标 */}
                  <div class="problem-filename-row">
                    <span class="problem-filename">{filename}</span>
                    <button
                      class="problem-toggle"
                      data-open={!collapsedFiles().has(filename)}
                      aria-expanded={!collapsedFiles().has(filename)}
                      aria-label={
                        collapsedFiles().has(filename) ? "展开问题列表" : "收起问题列表"
                      }
                      onClick={() => toggleFile(filename)}
                    >
                      {/* chevron-down：展开朝下，收起经 CSS rotate(-90deg) 平滑变为朝右 */}
                      <svg
                        class="problem-toggle-icon"
                        viewBox="0 0 16 16"
                        width="14"
                        height="14"
                        aria-hidden="true"
                      >
                        <path
                          d="M4 6l4 4 4-4"
                          fill="none"
                          stroke="currentColor"
                          stroke-width="1.6"
                          stroke-linecap="round"
                          stroke-linejoin="round"
                        />
                      </svg>
                    </button>
                  </div>
                  <Show when={!collapsedFiles().has(filename)}>
                    <For each={entries}>
                      {(entry) => (
                        <div
                          class="problem-entry"
                          onClick={() => jumpToEntry(entry.filename, entry.index)}
                        >
                          <span
                            class="problem-colorbar"
                            style={{ background: typeColor(problemTypesOf(entry.problem)[0] ?? "其他") }}
                          />
                          <span class="problem-index">#{entry.index}</span>
                          <span class="problem-desc">{entry.problem?.slice(0, 50)}</span>
                        </div>
                      )}
                    </For>
                  </Show>
                </div>
              );
            }}
          </For>
        </Show>
      </div>
    </div>
  );
}

/* ── 空侧栏 ── */
function EmptySidebar() {
  return (
    <div class="sidebar-panel">
      <div class="sidebar-content">
        <p class="sidebar-placeholder">侧边栏</p>
      </div>
    </div>
  );
}

/* ── 侧边栏容器（含拖拽调整宽度） ── */
export function SidebarPanel() {
  const tab = () => appState.sidebarTab;
  const [dragging, setDragging] = createSignal(false);

  function handlePointerDown(e: PointerEvent) {
    e.preventDefault();
    setDragging(true);

    const root = document.documentElement;

    function handlePointerMove(e: PointerEvent) {
      const sidebarLeft = 48;
      const newWidth = Math.max(180, Math.min(500, e.clientX - sidebarLeft));
      root.style.setProperty("--sidebar-expanded-width", `${newWidth}px`);
    }

    function handlePointerUp() {
      setDragging(false);
      document.removeEventListener("pointermove", handlePointerMove);
      document.removeEventListener("pointerup", handlePointerUp);
    }

    document.addEventListener("pointermove", handlePointerMove);
    document.addEventListener("pointerup", handlePointerUp);
  }

  onCleanup(() => {
    setDragging(false);
  });

  return (
    <div class="sidebar-wrapper" style={{ position: "relative" }}>
      <Switch>
        <Match when={tab() === "explorer"}>
          <FileExplorer />
        </Match>
        <Match when={tab() === "find"}>
          <FindReplacePanel />
        </Match>
        <Match when={tab() === "problems"}>
          <ProblemList />
        </Match>
        <Match when={!tab()}>
          <EmptySidebar />
        </Match>
      </Switch>
      <div
        class={`sidebar-resize-handle ${dragging() ? "active" : ""}`}
        onPointerDown={handlePointerDown}
      />
    </div>
  );
}
