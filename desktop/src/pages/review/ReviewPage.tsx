import {
  createSignal,
  createEffect,
  For,
  Show,
  onCleanup,
  onMount,
} from "solid-js";
import { appState, setAppState, markDirty, markClean } from "../../stores/appStore";
import { pushUndo, clearUndo, undo, redo, getUndoState } from "../../stores/undoStore";
import { fetchCacheFile, saveCacheFile } from "../../lib/api/project";
import type { CacheEntry, CacheFileResponse } from "../../lib/api/types";

/* ── 单条 CacheEntry 组件 ── */
function EntryCard(props: {
  entry: CacheEntry;
  expanded: boolean;
  onToggleExpand: () => void;
  onSkip: () => void;
  onDelete: () => void;
  onFieldChange: (field: string, value: string) => void;
}) {
  const e = () => props.entry;
  const hasProblem = () => !!e().problem;

  return (
    <div class={`entry-card ${hasProblem() ? "has-problem" : ""}`}>
      {/* ── 默认 3 行 ── */}
      <div class="entry-default">
        {/* 问题行 */}
        <div class="entry-problem">
          <Show when={hasProblem()}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="color:var(--color-status-error);flex-shrink:0">
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
            <span class="entry-problem-text">{e().problem}</span>
          </Show>
          <span class="entry-index">#{e().index}</span>
        </div>

        {/* 原文行 / 译文行 — 并排 */}
        <div class="entry-text-row">
          <div class="entry-src">{e().pre_src}</div>
          <input
            class="entry-dst-input"
            type="text"
            value={e().pre_dst}
            onInput={(ev) => props.onFieldChange("pre_dst", ev.currentTarget.value)}
          />
        </div>

        {/* 右侧操作按钮 */}
        <div class="entry-actions">
          <button
            class="entry-btn"
            title="展开字段"
            onClick={props.onToggleExpand}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d={props.expanded ? "M18 15l-6-6-6 6" : "M6 9l6 6 6-6"} />
            </svg>
          </button>
          <button class="entry-btn" title="跳过检查" onClick={props.onSkip}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
              <circle cx="12" cy="12" r="3" />
            </svg>
          </button>
          <button class="entry-btn entry-btn--danger" title="删除" onClick={props.onDelete}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" />
            </svg>
          </button>
        </div>
      </div>

      {/* ── 展开全部字段 ── */}
      <Show when={props.expanded}>
        <div class="entry-expanded">
          {ALL_FIELDS.map((field) => {
            const val = (e() as any)[field.key];
            const isEditable = field.key === "pre_dst" || field.key === "proofread_dst";
            return (
              <div class="entry-field">
                <span class="field-label">{field.label}</span>
                <Show
                  when={isEditable && val != null}
                  fallback={
                    <span class="field-value field-value--readonly">
                      {val != null ? String(val) : "—"}
                    </span>
                  }
                >
                  <input
                    class="field-value field-value--editable"
                    type="text"
                    value={String(val ?? "")}
                    onInput={(ev) =>
                      props.onFieldChange(field.key, ev.currentTarget.value)
                    }
                  />
                </Show>
              </div>
            );
          })}
        </div>
      </Show>
    </div>
  );
}

/* CacheEntry 18 字段的中文标签 */
const ALL_FIELDS = [
  { key: "index", label: "索引" },
  { key: "name", label: "说话人" },
  { key: "pre_src", label: "译前原文" },
  { key: "post_src", label: "译后原文" },
  { key: "pre_dst", label: "译前译文" },
  { key: "proofread_dst", label: "校对译文" },
  { key: "trans_by", label: "翻译引擎" },
  { key: "proofread_by", label: "校对者" },
  { key: "problem", label: "问题" },
  { key: "trans_conf", label: "翻译置信度" },
  { key: "doub_content", label: "存疑内容" },
  { key: "unknown_proper_noun", label: "未知专名" },
  { key: "pre_jp", label: "预处理日语" },
  { key: "post_jp", label: "后处理日语" },
  { key: "pre_zh", label: "预处理中文" },
  { key: "proofread_zh", label: "校对中文" },
  { key: "post_zh_preview", label: "后处理中文预览" },
  { key: "post_dst_preview", label: "后处理译文预览" },
];

/* ── ReviewPage 主组件 ── */
export function ReviewPage() {
  const [entries, setEntries] = createSignal<CacheEntry[]>([]);
  const [loading, setLoading] = createSignal(false);
  const [expandedSet, setExpandedSet] = createSignal<Set<number>>(new Set());
  const [jumpValue, setJumpValue] = createSignal("");

  // ── 键盘快捷键（撤销/重做） ──
  function handleKeyDown(e: KeyboardEvent) {
    if (!e.ctrlKey && !e.metaKey) return;
    if (e.key === "z") {
      e.preventDefault();
      handleUndo();
    } else if (e.key === "y") {
      e.preventDefault();
      handleRedo();
    }
  }

  // ── 菜单事件（编辑→撤销/重做） ──
  function handleMenuUndo() { handleUndo(); }
  function handleMenuRedo() { handleRedo(); }

  onMount(() => {
    document.addEventListener("keydown", handleKeyDown);
    document.addEventListener("galtransl:undo", handleMenuUndo);
    document.addEventListener("galtransl:redo", handleMenuRedo);
  });
  onCleanup(() => {
    document.removeEventListener("keydown", handleKeyDown);
    document.removeEventListener("galtransl:undo", handleMenuUndo);
    document.removeEventListener("galtransl:redo", handleMenuRedo);
  });

  function handleUndo() {
    const entry = undo();
    if (!entry) return;
    const currentFile = appState.activeFilePath;
    if (entry.file !== currentFile) return;

    setEntries((prev) => {
      const next = [...prev];
      const idx = next.findIndex((e) => e.index === entry.index);

      if (idx === -1 && Object.keys(entry.before).length > 0) {
        // 被删除的条目：恢复（插入到正确位置）
        next.splice(entry.index, 0, entry.before as unknown as CacheEntry);
        return next;
      }
      if (idx === -1) return prev;
      // 字段编辑
      next[idx] = { ...next[idx], ...entry.before };
      return next;
    });
  }

  function handleRedo() {
    const entry = redo();
    if (!entry) return;
    const currentFile = appState.activeFilePath;
    if (entry.file !== currentFile) return;

    setEntries((prev) => {
      const next = [...prev];
      const idx = next.findIndex((e) => e.index === entry.index);

      if (Object.keys(entry.after).length === 0 && idx !== -1) {
        // 重做删除
        next.splice(idx, 1);
        return next;
      }
      if (idx === -1) return prev;
      next[idx] = { ...next[idx], ...entry.after };
      return next;
    });
  }

  // 当 activeFilePath 变化时加载文件
  const VIRTUAL_THRESHOLD = 1500;
  const VIRTUAL_LIMIT = 750;
  const [totalCount, setTotalCount] = createSignal(0);
  const [showAll, setShowAll] = createSignal(false);

  createEffect(() => {
    const pid = appState.activeProjectId;
    const file = appState.activeFilePath;
    if (!pid || !file) {
      setEntries([]);
      setTotalCount(0);
      setShowAll(false);
      clearUndo();
      return;
    }

    setLoading(true);
    fetchCacheFile(pid, file)
      .then((res: CacheFileResponse) => {
        const all = res.entries ?? [];
        setTotalCount(all.length);
        if (all.length > VIRTUAL_THRESHOLD && !showAll()) {
          setEntries(all.slice(0, VIRTUAL_LIMIT));
        } else {
          setEntries(all);
        }
        setExpandedSet(new Set());
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  });

  function handleShowAll() {
    setShowAll(true);
    // 触发重新加载
    const pid = appState.activeProjectId;
    const file = appState.activeFilePath;
    if (pid && file) {
      setLoading(true);
      fetchCacheFile(pid, file)
        .then((res: CacheFileResponse) => {
          setEntries(res.entries ?? []);
          setExpandedSet(new Set());
        })
        .catch(() => {})
        .finally(() => setLoading(false));
    }
  }

  function toggleExpand(index: number) {
    setExpandedSet((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }

  function handleFieldChange(
    entryIndex: number,
    field: string,
    value: string
  ) {
    const before = { ...entries()[entryIndex] };
    setEntries((prev) => {
      const next = [...prev];
      next[entryIndex] = { ...next[entryIndex], [field]: value };
      return next;
    });
    const after = { ...entries()[entryIndex], [field]: value };

    // 记录到 undo
    pushUndo({
      id: `${appState.activeFilePath}:${entryIndex}`,
      file: appState.activeFilePath ?? "",
      index: entryIndex,
      before: { [field]: (before as any)[field] },
      after: { [field]: value },
      description: `修改 ${ALL_FIELDS.find((f) => f.key === field)?.label ?? field}`,
    });

    // 标记 dirty
    if (appState.activeFilePath) markDirty(appState.activeFilePath);
  }

  function handleSkip(index: number) {
    // 跳过检查 = 去除 problem 标记
    handleFieldChange(index, "problem", "");
  }

  function handleDelete(index: number) {
    const deleted = entries()[index];
    if (!deleted) return;

    pushUndo({
      id: `${appState.activeFilePath}:${index}`,
      file: appState.activeFilePath ?? "",
      index,
      before: deleted as any,
      after: {},
      description: "删除条目",
    });

    setEntries((prev) => prev.filter((_, i) => i !== index));
    if (appState.activeFilePath) markDirty(appState.activeFilePath);
  }

  function handleJump() {
    const val = parseInt(jumpValue(), 10);
    if (isNaN(val)) return;
    const el = document.getElementById(`entry-${val}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }

  const file = () => appState.activeFilePath;

  return (
    <div class="page page-review">
      {/* ── 工具栏 ── */}
      <div class="review-toolbar">
        <Show when={file()}>
          <span class="review-filename">{file()}</span>
        </Show>
        <div class="review-jump-group">
          <input
            class="review-jump-input"
            type="number"
            placeholder="跳转到 #"
            value={jumpValue()}
            onInput={(e) => setJumpValue(e.currentTarget.value)}
            onKeyDown={(e) => e.key === "Enter" && handleJump()}
          />
          <button class="btn btn--sm" onClick={handleJump}>
            跳转
          </button>
        </div>
        <Show when={entries().length > 0}>
          <span class="review-count">
            {entries().length} 条
          </span>
        </Show>
      </div>

      {/* ── 条目列表 ── */}
      <div class="review-list">
        <Show
          when={!loading()}
          fallback={<p class="review-placeholder">加载中…</p>}
        >
          <Show
            when={entries().length > 0}
            fallback={
              <p class="review-placeholder">
                {appState.activeProjectId && !file()
                  ? "请在侧栏中选择一个文件"
                  : appState.activeProjectId
                    ? "该文件暂无条目"
                    : "请先打开项目"}
              </p>
            }
          >
            {/* 虚拟滚动提示 */}
            <Show when={totalCount() > VIRTUAL_THRESHOLD && !showAll()}>
              <div class="review-virtual-banner">
                共 {totalCount()} 条，当前仅显示前 {VIRTUAL_LIMIT} 条
                <button class="btn btn--sm" onClick={handleShowAll} style="margin-left:8px">
                  显示全部
                </button>
              </div>
            </Show>
            <For each={entries()}>
              {(entry, i) => (
                <div id={`entry-${entry.index}`}>
                  <EntryCard
                    entry={entry}
                    expanded={expandedSet().has(i())}
                    onToggleExpand={() => toggleExpand(i())}
                    onSkip={() => handleSkip(i())}
                    onDelete={() => handleDelete(i())}
                    onFieldChange={(field, value) =>
                      handleFieldChange(i(), field, value)
                    }
                  />
                </div>
              )}
            </For>
          </Show>
        </Show>
      </div>
    </div>
  );
}
