import {
  Match,
  Switch,
  createSignal,
  createEffect,
  onCleanup,
  Show,
  For,
} from "solid-js";
import {
  appState,
  setAppState,
} from "../stores/appStore";
import { fetchProjectFiles } from "../lib/api/project";
import { fetchProjectProblems } from "../lib/api/project";
import type {
  FileEntry,
  ProblemEntry,
} from "../lib/api/types";

/* ── 文件浏览器 ── */
function FileExplorer() {
  const [files, setFiles] = createSignal<FileEntry[]>([]);
  const [loading, setLoading] = createSignal(false);
  const [expanded, setExpanded] = createSignal(true);

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
        <Show
          when={!loading()}
          fallback={
            <p class="sidebar-placeholder">加载中…</p>
          }
        >
          <Show
            when={files().length > 0}
            fallback={
              <p class="sidebar-placeholder">
                {appState.activeProjectId
                  ? "暂无缓存文件"
                  : "请先打开项目"}
              </p>
            }
          >
            {files().map((f) => (
              <div
                class={`file-tree-item ${selected() === f.name ? "selected" : ""}`}
                onClick={() => selectFile(f)}
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
                  <path d="M6 2h8l4 4v16H6V2Zm8 0v4h4" />
                </svg>
                <span class="file-tree-name">{f.name}</span>
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
  const [replace, setReplace] = createSignal("");

  function handleSearch() {
    // TODO: integrate searchCache API
  }

  function handleReplace() {
    // TODO: integrate replaceCache API
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
            onInput={(e) => setQuery(e.currentTarget.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          />
        </div>
        <div class="find-input-group">
          <input
            class="find-input"
            type="text"
            placeholder="替换为"
            value={replace()}
            onInput={(e) => setReplace(e.currentTarget.value)}
            onKeyDown={(e) => e.key === "Enter" && handleReplace()}
          />
        </div>
        <div class="find-actions">
          <button class="btn btn--sm" onClick={handleSearch}>
            查找
          </button>
          <button class="btn btn--sm" onClick={handleReplace}>
            替换全部
          </button>
        </div>
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

  function jumpToEntry(filename: string, index: number) {
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
        <Show
          when={grouped().length > 0}
          fallback={
            <p class="sidebar-placeholder">暂无问题</p>
          }
        >
          <For each={grouped()}>
            {([filename, entries]) => (
              <div class="problem-group">
                <div class="problem-filename">{filename}</div>
                <For each={entries}>
                  {(entry) => (
                    <div
                      class="problem-entry"
                      onClick={() =>
                        jumpToEntry(entry.filename, entry.index)
                      }
                    >
                      <span class="problem-index">#{entry.index}</span>
                      <span class="problem-desc">
                        {entry.problem?.slice(0, 50)}
                      </span>
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
      const newWidth = Math.max(
        180,
        Math.min(500, e.clientX - sidebarLeft)
      );
      root.style.setProperty(
        "--sidebar-expanded-width",
        `${newWidth}px`
      );
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
