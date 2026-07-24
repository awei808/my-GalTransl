import { Match, Switch, createSignal, createEffect, onCleanup, Show, For } from "solid-js";
import { appState, setAppState } from "../stores/appStore";
import { toast } from "../stores/toastStore";
import { pushUndo } from "../stores/undoStore";
import { searchCache, replaceCache, fetchProjectProblems, deleteCacheFiles, fetchProjectFiles } from "../lib/api/project";
import { confirm } from "../stores/confirmStore";
import { startCacheWatcher, stopCacheWatcher } from "../lib/cacheWatcher";
import { getErrorMessage } from "../lib/errors";
import type {
  FileNode,
  ProblemEntry,
  CacheSearchResult,
  CacheSearchField,
} from "../lib/api/types";

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
  onContextMenu?: (e: MouseEvent, path: string, name: string) => void;
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
          // 仅对文件节点（含元数据文件）弹出右键菜单，目录不触发
          if (n().is_file) props.onContextMenu?.(e, n().path, n().name);
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
  const [ctxMenu, setCtxMenu] = createSignal<{ x: number; y: number; path: string; name: string } | null>(null);

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

  function openCtxMenu(e: MouseEvent, path: string, name: string) {
    e.preventDefault();
    e.stopPropagation();
    setCtxMenu({ x: e.clientX, y: e.clientY, path, name });
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
      {/* 文件右键菜单 */}
      <Show when={ctxMenu()}>
        <div
          class="ctx-menu"
          style={{ left: `${ctxMenu()!.x}px`, top: `${ctxMenu()!.y}px` }}
          onClick={(e) => e.stopPropagation()}
        >
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

  createEffect(() => {
    const pid = appState.activeProjectId;
    void appState.cacheVersion; // 依赖：缓存变化 → 刷新问题列表
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

  // 按文件名分组
  const grouped = () => {
    const map = new Map<string, ProblemEntry[]>();
    for (const p of problems()) {
      const list = map.get(p.filename) ?? [];
      list.push(p);
      map.set(p.filename, list);
    }
    return [...map.entries()];
  };

  function jumpToEntry(filename: string) {
    setAppState({
      activeView: "review",
      activeFilePath: filename,
      // 不切换 sidebarTab：用户点击问题条目后应留在问题面板继续查阅，
      // 而非被强制切到文件浏览器。文件浏览器可在需要时手动切换。
    });
    // TODO: navigate to specific index
  }

  return (
    <div class="sidebar-panel">
      <div class="sidebar-header">问题检测</div>
      <div class="sidebar-content">
        <Show when={grouped().length > 0} fallback={<p class="sidebar-placeholder">暂无问题</p>}>
          <For each={grouped()}>
            {([filename, entries]) => (
              <div class="problem-group">
                <div class="problem-filename">{filename}</div>
                <For each={entries}>
                  {(entry) => (
                    <div class="problem-entry" onClick={() => jumpToEntry(entry.filename)}>
                      <span class="problem-index">#{entry.index}</span>
                      <span class="problem-desc">{entry.problem?.slice(0, 50)}</span>
                    </div>
                  )}
                </For>
              </div>
            )}
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
