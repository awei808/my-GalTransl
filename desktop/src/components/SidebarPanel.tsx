import { Match, Switch, createSignal, createEffect, createMemo, onCleanup, Show, For } from "solid-js";
import { appState, setAppState, getActiveConfigFileName } from "../stores/appStore";
import type { AppState } from "../stores/appStore";
import { toast } from "../stores/toastStore";
import { pushUndo } from "../stores/undoStore";
import { searchCache, replaceCache, fetchProjectProblems, fetchProjectAltTranslations, deleteCacheFiles, fetchProjectFiles, revealInFileManager, recheckAllCacheProblems } from "../lib/api/project";
import { buildReplaceUndoEntries } from "../lib/replaceUndo";
import { confirm } from "../stores/confirmStore";
import { startCacheWatcher, stopCacheWatcher } from "../lib/cacheWatcher";
import { getErrorMessage } from "../lib/errors";
import { problemTypesOf } from "../lib/problems";
import { fetchProblemTypes } from "../lib/api/general";
import { ProblemTypeFilterDropdown } from "./ProblemTypeFilterDropdown";
import type {
  FileNode,
  ProblemEntry,
  AltTransEntry,
  CacheSearchResult,
  CacheSearchField,
  ProblemTypeInfo,
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

  // 选中文件变化时，自动展开其所在的目录链（如 pass1_cache/sub/xx.json → 展开 pass1_cache、pass1_cache/sub）。
  // 兼容侧栏手点：目录本身点击走 toggle 展开/收起，不受此 effect 影响；仅外部跳转（activeFilePath 变化）触发。
  createEffect(() => {
    const sel = selected();
    if (!sel) return;
    const parts = sel.split("/");
    if (parts.length < 2) return;
    // 目标可能是文件或目录：目录自身不展开（保持用户展开/收起控制），只展开其祖先
    const dirs: string[] = [];
    for (let i = 1; i < parts.length; i++) {
      dirs.push(parts.slice(0, i).join("/"));
    }
    setExpanded((prev) => {
      const next = new Set(prev);
      let changed = false;
      for (const d of dirs) {
        if (!next.has(d)) {
          next.add(d);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  });

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
  // 正在执行「替换单个」的条目 key（`${filename}:${index}`），防止同一条目重复点击并发替换
  const [replacingKeys, setReplacingKeys] = createSignal<Set<string>>(new Set());

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
    const f = field();
    if (!pid || !q) {
      toast.warning("请先输入查找内容");
      return;
    }
    if (f === "problem") {
      toast.warning("问题字段不支持替换，请切换字段后再试");
      return;
    }
    setReplacing(true);
    try {
      // 先执行 dryRun 确认数量（dry_run 响应携带替换前原值 entries，作为撤销 before 快照）
      const dryRes = await replaceCache(pid, q, r, f, true);
      if (dryRes.total_matches === 0) {
        toast.info("未找到可替换的匹配项");
        setReplacing(false);
        return;
      }

      // 全部替换直接写磁盘缓存文件：确认弹窗告知用户后再执行
      const res = await confirm.show({
        title: "确认全部替换",
        message: `共命中 ${dryRes.total_matches} 处（${dryRes.total_files} 个文件）。将直接写入磁盘缓存文件，非校对面板临时修改，无法撤回，是否继续？`,
        tone: "warning",
        confirmText: "替换并写盘",
        cancelText: "取消",
      });
      if (!res.confirmed) {
        setReplacing(false);
        return;
      }

      // 执行真实替换（响应携带替换后 entries，作为撤销 after 快照）
      const real = await replaceCache(pid, q, r, f, false);
      toast.success(`已替换 ${real.total_matches} 个匹配项，涉及 ${real.total_files} 个文件`);

      // 替换成功后构造撤销栈：before=替换前原值，after=替换后值，仅入栈实际发生变化的条目
      for (const entry of buildReplaceUndoEntries(dryRes, real)) {
        pushUndo(entry);
      }

      // 重新搜索
      await handleSearch();
    } catch (e) {
      toast.error(`替换失败: ${getErrorMessage(e)}`);
    } finally {
      setReplacing(false);
    }
  }

  /** 替换单个：仅替换校对页当前打开文件中该条目的匹配文本（纯前端，不写盘，保存后生效） */
  function handleReplaceOne(r: CacheSearchResult) {
    const pid = appState.activeProjectId;
    const q = query().trim();
    const rText = replaceText();
    const f = field();
    if (!pid || !q) {
      toast.warning("请先输入查找内容");
      return;
    }
    if (f === "problem") {
      toast.warning("问题字段不支持替换，请切换字段后再试");
      return;
    }
    // 纯前端替换只作用于校对页当前打开文件：未打开文件或目标条目属于其他文件时提示
    if (!appState.activeFilePath) {
      toast.warning("请先在校对页打开要替换的文件");
      return;
    }
    if (r.filename !== appState.activeFilePath) {
      toast.warning("该条目属于其他文件，请先在校对页打开该文件后再替换");
      return;
    }
    const key = `${r.filename}:${r.index}`;
    if (replacingKeys().has(key)) return;
    setReplacingKeys((prev) => new Set(prev).add(key));
    try {
      // 交由 ReviewPage 消费：内存替换 + 标脏 + 入撤销栈，不写盘
      setAppState("replaceRequest", {
        query: q,
        replacement: rText,
        field: f,
        targetFile: r.filename,
        onlyIndex: r.index,
      });
    } finally {
      setReplacingKeys((prev) => {
        const next = new Set(prev);
        next.delete(key);
        return next;
      });
    }
  }

  /** 文件内全部替换：仅替换校对页当前打开文件的全部匹配条目（纯前端，不写盘，保存后生效） */
  function handleReplaceInFile() {
    const pid = appState.activeProjectId;
    const q = query().trim();
    const r = replaceText();
    const f = field();
    const file = appState.activeFilePath;
    if (!pid || !q) {
      toast.warning("请先输入查找内容");
      return;
    }
    if (f === "problem") {
      toast.warning("问题字段不支持替换，请切换字段后再试");
      return;
    }
    if (!file || appState.activeView !== "review") {
      toast.warning("请先在校对审核页打开要替换的文件");
      return;
    }
    // 交由 ReviewPage 消费：内存替换 + 标脏 + 入撤销栈，不写盘
    setAppState("replaceRequest", { query: q, replacement: r, field: f, targetFile: file });
    // 替换结果由 ReviewPage 消费后 toast 反馈
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
    const patch: Partial<AppState> = {
      activeView: "review",
      reviewJumpToIndex: r.index,
    };
    // 仅当切换文件时才设 activeFilePath，同文件跳转不用重载
    if (r.filename !== appState.activeFilePath) {
      patch.activeFilePath = r.filename;
    }
    // 不改 sidebarTab：保持「查找替换」面板打开，避免卸载导致查找栏清空
    setAppState(patch);
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
            title="替换所有文件的全部匹配项，直接写入磁盘缓存，需确认"
          >
            {replacing() ? "替换中…" : "替换全部"}
          </button>
          <button
            class="btn btn--sm"
            onClick={handleReplaceInFile}
            disabled={appState.activeView !== "review" || !appState.activeFilePath}
            title="仅替换校对页当前打开文件的匹配项，不写盘，点「保存并重检」后写入磁盘"
          >
            文件内替换全部
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
                      {(r) => {
                        const replacingKey = `${r.filename}:${r.index}`;
                        const isReplacing = replacingKeys().has(replacingKey);
                        return (
                          <div class="find-result-item" onClick={() => jumpToResult(r)}>
                            <span class="find-result-index">#{r.index}</span>
                            <span class="find-result-preview">
                              {r.match_src ? r.post_src?.slice(0, 40) : ""}
                              {r.match_dst ? r.pre_dst?.slice(0, 40) : ""}
                              {r.match_problem ? r.problem?.slice(0, 40) : ""}
                            </span>
                            <button
                              class="find-result-replace"
                              title="替换该条目的匹配文本"
                              aria-label="替换该条目"
                              disabled={isReplacing}
                              onClick={(e) => {
                                e.stopPropagation();
                                void handleReplaceOne(r);
                              }}
                            >
                              {isReplacing ? "…" : "替换"}
                            </button>
                          </div>
                        );
                      }}
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
  // 多选问题类型筛选：空数组表示全部类型，多个类型为 AND 语义（与校对审核页一致）
  const [filterTypes, setFilterTypes] = createSignal<string[]>([]);
  const [problemTypes, setProblemTypes] = createSignal<ProblemTypeInfo[]>([]);
  // 全缓存重检进行中标志：重检期间禁用按钮防重入
  const [rechecking, setRechecking] = createSignal(false);
  // 已展开的文件集合（默认折叠，点击文件行右侧图标展开；内联读取以启用细粒度追踪）
  const [expandedFiles, setExpandedFiles] = createSignal<Set<string>>(new Set());
  function toggleFile(filename: string) {
    setExpandedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(filename)) next.delete(filename);
      else next.add(filename);
      return next;
    });
  }

  // 请求序号：problemVersion 重拉 / pid 切换时递增，使旧请求的过期响应不再覆盖新结果
  let problemSeq = 0;

  createEffect(() => {
    const pid = appState.activeProjectId;
    // 依赖 problemVersion：任一缓存文件变化都刷新问题列表（修复"非当前文件保存后不刷新"盲区）
    void appState.problemVersion;
    if (!pid || appState.sidebarTab !== "problems") {
      problemSeq++;  // 使在途响应立即过期
      setProblems([]);
      return;
    }
    const seq = ++problemSeq;
    void fetchProblemTypes().then((r) => {
      if (seq === problemSeq && r) setProblemTypes(r);
    });
    // 查整个项目的问题，不按当前文件过滤（问题列表按文件名分组，已足够区分）
    fetchProjectProblems(pid)
      .then((res) => {
        if (seq === problemSeq) setProblems(res.problems ?? []);
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

  // 按类型筛选后的问题列表（多选 AND 语义：须同时命中所有勾选类型，与校对审核页一致）
  const filteredProblems = createMemo(() => {
    const ts = filterTypes();
    if (ts.length === 0) return problems();
    return problems().filter((p) => ts.every((t) => problemTypesOf(p.problem).includes(t)));
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
    const patch: Partial<AppState> = {
      activeView: "review",
      reviewJumpToIndex: index,
    };
    // 仅当切换文件时才设 activeFilePath，同文件跳转不用重载
    if (filename !== appState.activeFilePath) {
      patch.activeFilePath = filename;
    }
    setAppState(patch);
  }

  /** 全缓存重检：确认弹窗提示勿编辑/保存，完成后刷新问题列表 */
  async function handleRecheckAll() {
    const pid = appState.activeProjectId;
    if (!pid || rechecking()) return;
    const result = await confirm.show({
      title: "全文件重检问题",
      message:
        "将对全部缓存译文文件重新运行问题检测并写回结果。\n重检期间请勿编辑或保存任何文件，以免产生冲突。",
      confirmText: "开始重检",
    });
    if (!result.confirmed) return;
    setRechecking(true);
    try {
      const res = await recheckAllCacheProblems(pid, getActiveConfigFileName());
      if (!res.success) {
        toast.warning(res.error ?? "全缓存重检失败，未修改任何文件");
        return;
      }
      toast.success(`已重检 ${res.rechecked ?? 0} 个文件`);
      // 复用 problemVersion 版本号，驱动问题列表/备选列表自动重新拉取
      setAppState("problemVersion", (v: number) => v + 1);
    } catch (e) {
      toast.error(`全缓存重检失败：${getErrorMessage(e)}`);
    } finally {
      setRechecking(false);
    }
  }

  return (
    <div class="sidebar-panel">
      <div class="sidebar-header sidebar-header--with-actions">
        问题检测
        <button
          class="problem-recheck-btn"
          title="全文件重检问题"
          aria-label="全文件重检问题"
          disabled={rechecking()}
          onClick={() => void handleRecheckAll()}
        >
          <svg
            class={rechecking() ? "problem-recheck-icon problem-recheck-icon--spin" : "problem-recheck-icon"}
            viewBox="0 0 24 24"
            width="14"
            height="14"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <path d="M21 12a9 9 0 1 1-2.64-6.36" />
            <path d="M21 3v6h-6" />
          </svg>
        </button>
      </div>
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
                  onClick={() => {
                    // 统计条点击为多选 toggle：与下拉框选中状态联动
                    const cur = filterTypes();
                    if (cur.includes(t)) setFilterTypes(cur.filter((x) => x !== t));
                    else setFilterTypes([...cur, t]);
                  }}
                >
                  {t} {n}
                </span>
              )}
            </For>
          </div>
          {/* 类型多选下拉（复用校对审核页组件与样式） */}
          <ProblemTypeFilterDropdown
            value={filterTypes}
            types={problemTypes}
            onChange={setFilterTypes}
          />
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
                  <span class="problem-filename-count">{entries.length}</span>
                  <button
                    class="problem-toggle"
                    data-open={expandedFiles().has(filename)}
                    aria-expanded={expandedFiles().has(filename)}
                    aria-label={
                        expandedFiles().has(filename) ? "收起问题列表" : "展开问题列表"
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
                  <Show when={expandedFiles().has(filename)}>
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

/* ── 查看备选（对照 ProblemList，分组默认折叠以防大列表卡顿） ── */
function AltList() {
  const [alts, setAlts] = createSignal<AltTransEntry[]>([]);
  // 已展开的文件集合（默认折叠，点击文件行右侧图标展开；内联读取以启用细粒度追踪）
  const [expandedFiles, setExpandedFiles] = createSignal<Set<string>>(new Set());
  function toggleFile(filename: string) {
    setExpandedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(filename)) next.delete(filename);
      else next.add(filename);
      return next;
    });
  }

  // 请求序号：problemVersion 重拉 / pid 切换时递增，使旧请求的过期响应不再覆盖新结果
  let altSeq = 0;

  createEffect(() => {
    const pid = appState.activeProjectId;
    // 复用问题列表版本号：任一缓存文件变化都刷新备选列表（覆盖外部修改盲区）
    void appState.problemVersion;
    if (!pid || appState.sidebarTab !== "alt") {
      altSeq++;  // 使在途响应立即过期
      setAlts([]);
      return;
    }
    const seq = ++altSeq;
    fetchProjectAltTranslations(pid)
      .then((res) => {
        if (seq === altSeq) setAlts(res.alts ?? []);
      })
      .catch(() => {});
  });

  // 按文件名分组
  const grouped = () => {
    const map = new Map<string, AltTransEntry[]>();
    for (const a of alts()) {
      const list = map.get(a.filename) ?? [];
      list.push(a);
      map.set(a.filename, list);
    }
    return [...map.entries()];
  };

  function jumpToEntry(filename: string, index: number) {
    const patch: Partial<AppState> = {
      activeView: "review",
      reviewJumpToIndex: index,
    };
    // 仅当切换文件时才设 activeFilePath，同文件跳转不用重载
    if (filename !== appState.activeFilePath) {
      patch.activeFilePath = filename;
    }
    setAppState(patch);
  }

  return (
    <div class="sidebar-panel">
      <div class="sidebar-header">查看备选</div>
      <div class="sidebar-content">
        <Show when={alts().length > 0} fallback={<p class="sidebar-placeholder">暂无备选译文</p>}>
          <div class="problem-stats">
            <span class="problem-stats-total">共 {alts().length} 条备选译文</span>
          </div>
          <For each={grouped()}>
            {([filename, entries]) => (
              <div class="problem-group">
                {/* 文件行：左侧文件名，右侧展开/收起切换图标 */}
                <div class="problem-filename-row">
                  <span class="problem-filename">{filename}</span>
                  <span class="problem-filename-count">{entries.length}</span>
                  <button
                    class="problem-toggle"
                    data-open={expandedFiles().has(filename)}
                    aria-expanded={expandedFiles().has(filename)}
                    aria-label={expandedFiles().has(filename) ? "收起备选列表" : "展开备选列表"}
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
                <Show when={expandedFiles().has(filename)}>
                  <For each={entries}>
                    {(entry) => (
                      <div
                        class="problem-entry"
                        onClick={() => jumpToEntry(entry.filename, entry.index)}
                      >
                        <span class="problem-index">#{entry.index}</span>
                        <span class="problem-desc">{entry.alt_dst?.slice(0, 50)}</span>
                      </div>
                    )}
                  </For>
                </Show>
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
        <Match when={tab() === "alt"}>
          <AltList />
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
