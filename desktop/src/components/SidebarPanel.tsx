import { Match, Switch, createSignal, createEffect, onCleanup, Show, For } from "solid-js";
import { appState, setAppState } from "../stores/appStore";
import { toast } from "../stores/toastStore";
import { pushUndo } from "../stores/undoStore";
import { fetchProjectFiles, searchCache, replaceCache } from "../lib/api/project";
import { fetchProjectProblems } from "../lib/api/project";
import { getErrorMessage } from "../lib/errors";
import type {
  FileEntry,
  ProblemEntry,
  CacheSearchResult,
  CacheSearchField,
} from "../lib/api/types";

/* ── 文件浏览器 ── */
function FileExplorer() {
  const [files, setFiles] = createSignal<FileEntry[]>([]);
  const [loading, setLoading] = createSignal(false);

  createEffect(() => {
    const pid = appState.activeProjectId;
    if (!pid || appState.activeView !== "review") {
      setFiles([]);
      return;
    }
    setLoading(true);
    fetchProjectFiles(pid)
      .then((res) => {
        setFiles(res.cache_files ?? []);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  });

  function selectFile(file: FileEntry) {
    setAppState("activeFilePath", file.name);
  }

  const selected = () => appState.activeFilePath;

  return (
    <div class="sidebar-panel">
      <div class="sidebar-header">文件浏览器</div>
      <div class="sidebar-content">
        <Show when={!loading()} fallback={<p class="sidebar-placeholder">加载中…</p>}>
          <Show
            when={files().length > 0}
            fallback={
              <p class="sidebar-placeholder">
                {appState.activeProjectId ? "暂无缓存文件" : "请先打开项目"}
              </p>
            }
          >
            {files().map((f) => (
              <div
                class={`file-tree-item ${selected() === f.name ? "selected" : ""} ${f.is_metadata ? "file-tree-item--meta" : ""}`}
                onClick={() => selectFile(f)}
                title={f.is_metadata ? "元数据文件（校对审核将以元数据模式打开）" : f.name}
              >
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="1.5"
                  style="flex-shrink:0"
                >
                  {f.is_file ? (
                    <path d="M6 2h8l4 4v16H6V2Zm8 0v4h4" />
                  ) : (
                    <path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z" />
                  )}
                </svg>
                <span class="file-tree-name">{f.name}</span>
                <Show when={f.is_metadata}>
                  <span class="file-tree-tag">元数据</span>
                </Show>
                <span class="file-tree-count">{f.entry_count}</span>
              </div>
            ))}
          </Show>
        </Show>
      </div>
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
    if (!pid || appState.sidebarTab !== "problems") {
      setProblems([]);
      return;
    }
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
      sidebarTab: "explorer",
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
