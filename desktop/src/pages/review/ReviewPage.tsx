import { createSignal, createEffect, Index, Show, For, onCleanup, onMount, createMemo } from "solid-js";
import { appState, markDirty, getActiveConfigFileName } from "../../stores/appStore";
import { pushUndo, clearUndo, undo, redo } from "../../stores/undoStore";
import { confirm } from "../../stores/confirmStore";
import {
  fetchCacheFile,
  saveCacheFile,
  fetchProjectMetadata,
  saveProjectMetadata,
} from "../../lib/api/project";
import type { CacheEntry, CacheFileResponse, MetadataEntry } from "../../lib/api/types";

/* ── 单条 CacheEntry 组件 ── */
function EntryCard(props: {
  entry: CacheEntry;
  expanded: boolean;
  onToggleExpand: () => void;
  onSkip: () => void;
  onDelete: () => void;
  onFieldChange: (field: string, value: string) => void;
  onBlur: () => void;
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
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              style="color:var(--color-status-error);flex-shrink:0"
            >
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
            onBlur={props.onBlur}
          />
        </div>

        {/* 右侧操作按钮 */}
        <div class="entry-actions">
          <button class="entry-btn" title="展开/收起全部字段" onClick={props.onToggleExpand}>
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <path d={props.expanded ? "M18 15l-6-6-6 6" : "M6 9l6 6 6-6"} />
            </svg>
            <span class="entry-btn-text">展开</span>
          </button>
          <button class="entry-btn" title="跳过该条目的检查" onClick={props.onSkip}>
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
              <circle cx="12" cy="12" r="3" />
            </svg>
            <span class="entry-btn-text">跳过</span>
          </button>
          <button class="entry-btn entry-btn--danger" title="删除该条目" onClick={props.onDelete}>
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" />
            </svg>
            <span class="entry-btn-text">删除</span>
          </button>
        </div>
      </div>

      {/* ── 展开全部字段 ── */}
      <Show when={props.expanded}>
        <div class="entry-expanded">
          {ALL_FIELDS.map((field) => {
            const val = e()[field.key];
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
                    onInput={(ev) => props.onFieldChange(field.key, ev.currentTarget.value)}
                    onBlur={props.onBlur}
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

/* ── 单条元数据组件（FileMetaData / BatchMetadata）── */
function MetadataCard(props: {
  entry: MetadataEntry;
  onFieldChange: (field: string, value: unknown) => void;
  onDelete: () => void;
  onBlur: () => void;
}) {
  const e = () => props.entry;
  const idValue = () => String(e().id ?? "");
  const fields = () => Object.keys(e()).filter((k) => k !== "id");
  // 数组字段按行展示；其余按字符串展示
  const display = (v: unknown) =>
    Array.isArray(v) ? v.map((x) => String(x)).join("\n") : v == null ? "" : String(v);
  const isArrayField = (v: unknown) => Array.isArray(v);

  return (
    <div class="meta-card">
      <div class="meta-card-head">
        <span class="meta-card-id" title="条目 id（不可编辑）">
          id: {idValue()}
        </span>
        {/* 右上角删除按钮 */}
        <button class="entry-btn entry-btn--danger" title="删除该条目" onClick={props.onDelete}>
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
          >
            <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" />
          </svg>
          <span class="entry-btn-text">删除</span>
        </button>
      </div>
      <div class="meta-fields">
        <For each={fields()}>
          {(field) => {
            const val = () => e()[field];
            return (
              <div class="meta-field">
                <span class="field-label">{field}</span>
                <Show
                  when={isArrayField(val())}
                  fallback={
                    <input
                      class="meta-input"
                      type="text"
                      value={display(val())}
                      onInput={(ev) => props.onFieldChange(field, ev.currentTarget.value)}
                      onBlur={props.onBlur}
                    />
                  }
                >
                  <textarea
                    class="meta-textarea"
                    rows="3"
                    value={display(val())}
                    onInput={(ev) =>
                      props.onFieldChange(
                        field,
                        ev.currentTarget.value.split("\n"),
                      )
                    }
                    onBlur={props.onBlur}
                  />
                </Show>
              </div>
            );
          }}
        </For>
      </div>
    </div>
  );
}

/* CacheEntry 18 字段的中文标签 */
const ALL_FIELDS: Array<{ key: keyof CacheEntry; label: string }> = [
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

/* 构造一条空白 CacheEntry（用于「新增条目」） */
function createBlankEntry(index: number): CacheEntry {
  return {
    index,
    name: "",
    pre_src: "",
    post_src: "",
    pre_dst: "",
    proofread_dst: "",
    trans_by: "",
    proofread_by: "",
    problem: "",
    trans_conf: 0,
    doub_content: "",
    unknown_proper_noun: "",
    pre_jp: "",
    post_jp: "",
    pre_zh: "",
    proofread_zh: "",
    post_zh_preview: "",
    post_dst_preview: "",
  };
}

/* ── ReviewPage 主组件 ── */
export function ReviewPage() {
  const [entries, setEntries] = createSignal<CacheEntry[]>([]);
  const [loading, setLoading] = createSignal(false);
  const [expandedSet, setExpandedSet] = createSignal<Set<number>>(new Set());
  const [jumpValue, setJumpValue] = createSignal("");

  // ── 模式由打开的文件路径隐式决定，无需手动切换 ──
  // 元数据文件（FileMetaData.json / BatchMetadata.json）位于 pass1_cache / pass2_cache，
  // 与平铺在 transl_cache 下的译文缓存不在同目录；按其文件名 basename 即可判定模式。
  type ReviewMode = "translate" | "metadata";
  type MetadataFileName = "FileMetaData.json" | "BatchMetadata.json";
  const METADATA_FILES: readonly MetadataFileName[] = ["FileMetaData.json", "BatchMetadata.json"];
  function metadataNameOf(path: string | null | undefined): MetadataFileName | null {
    if (!path) return null;
    const base = path.split(/[\\/]/).pop() ?? path;
    return (METADATA_FILES as readonly string[]).includes(base) ? (base as MetadataFileName) : null;
  }
  const reviewMode = createMemo<ReviewMode>(() =>
    metadataNameOf(appState.activeFilePath) ? "metadata" : "translate",
  );
  const metaName = createMemo<MetadataFileName>(
    () => metadataNameOf(appState.activeFilePath) ?? "FileMetaData.json",
  );
  const [metaEntries, setMetaEntries] = createSignal<MetadataEntry[]>([]);
  const [metaLoading, setMetaLoading] = createSignal(false);
  let metaSavePending = false;

  // ── 文件内查找 ──
  const [findOpen, setFindOpen] = createSignal(false);
  const [findQuery, setFindQuery] = createSignal("");

  // 根据查找条件过滤条目
  const filteredEntries = () => {
    const q = findQuery().toLowerCase().trim();
    if (!q) return entries();
    return entries().filter(
      (e) =>
        (e.pre_src && e.pre_src.toLowerCase().includes(q)) ||
        (e.pre_dst && e.pre_dst.toLowerCase().includes(q)) ||
        (e.problem && e.problem.toLowerCase().includes(q)),
    );
  };

  function handleFindInFile() {
    setFindOpen(!findOpen());
    if (findOpen()) setFindQuery("");
  }

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
  function handleMenuUndo() {
    handleUndo();
  }
  function handleMenuRedo() {
    handleRedo();
  }

  onMount(() => {
    document.addEventListener("keydown", handleKeyDown);
    document.addEventListener("galtransl:undo", handleMenuUndo);
    document.addEventListener("galtransl:redo", handleMenuRedo);
    document.addEventListener("galtransl:find-in-file", handleFindInFile);
  });
  onCleanup(() => {
    document.removeEventListener("keydown", handleKeyDown);
    document.removeEventListener("galtransl:undo", handleMenuUndo);
    document.removeEventListener("galtransl:redo", handleMenuRedo);
    document.removeEventListener("galtransl:find-in-file", handleFindInFile);
  });

  function handleUndo() {
    const entry = undo();
    if (!entry) return;
    const currentFile = appState.activeFilePath;
    if (entry.file !== currentFile) return;

    const isAdd = Object.keys(entry.before).length === 0 && Object.keys(entry.after).length > 0;

    setEntries((prev) => {
      const next = [...prev];
      const idx = next.findIndex((e) => e.index === entry.index);

      if (isAdd) {
        // 新增的撤销 = 移除该条目
        if (idx !== -1) next.splice(idx, 1);
        return next;
      }
      if (idx === -1) {
        // 被删除的条目：恢复（插入到正确位置）
        if (Object.keys(entry.before).length > 0) {
          next.splice(entry.index, 0, entry.before as unknown as CacheEntry);
        }
        return next;
      }
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

    const isAdd = Object.keys(entry.before).length === 0 && Object.keys(entry.after).length > 0;

    setEntries((prev) => {
      const next = [...prev];
      const idx = next.findIndex((e) => e.index === entry.index);

      if (isAdd) {
        // 新增的重做 = 重新插入
        if (idx === -1) {
          next.splice(entry.index, 0, entry.after as unknown as CacheEntry);
        }
        return next;
      }
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
    // 元数据模式下不加载翻译缓存文件
    if (reviewMode() !== "translate") return;
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
        setExpandedSet(new Set<number>());
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
          setExpandedSet(new Set<number>());
        })
        .catch(() => {})
        .finally(() => setLoading(false));
    }
  }

  // ── 元数据加载 / 保存 / 删除 ──
  createEffect(() => {
    const pid = appState.activeProjectId;
    const name = metaName();
    if (reviewMode() !== "metadata" || !pid) {
      setMetaEntries([]);
      return;
    }
    setMetaLoading(true);
    fetchProjectMetadata(pid, name)
      .then((res) => setMetaEntries(res.entries ?? []))
      .catch(() => setMetaEntries([]))
      .finally(() => setMetaLoading(false));
  });

  function handleMetaFieldChange(index: number, field: string, value: unknown) {
    setMetaEntries((prev) => {
      const next = prev.slice();
      next[index] = { ...next[index], [field]: value };
      return next;
    });
  }

  async function saveMeta() {
    if (metaSavePending) return;
    metaSavePending = true;
    const pid = appState.activeProjectId;
    if (!pid) {
      metaSavePending = false;
      return;
    }
    try {
      await saveProjectMetadata(pid, metaName(), metaEntries());
    } catch {
      // 静默处理
    } finally {
      metaSavePending = false;
    }
  }

  function handleMetaDelete(index: number) {
    const entry = metaEntries()[index];
    if (!entry) return;
    const idLabel = entry.id ? `id 为「${String(entry.id)}」` : `第 ${index + 1} 条`;
    confirm
      .show({
        title: "删除元数据条目",
        message: `确定要删除${idLabel}的元数据条目吗？此操作不可撤销。`,
        tone: "danger",
        confirmText: "删除",
      })
      .then((r) => {
        if (r.confirmed) {
          setMetaEntries((prev) => prev.filter((_, i) => i !== index));
          saveMeta();
        }
      });
  }

  function toggleExpand(index: number) {
    setExpandedSet((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }

  function handleFieldChange(entryIndex: number, field: string, value: string) {
    const before = { ...entries()[entryIndex] };
    setEntries((prev) => {
      const next = [...prev];
      next[entryIndex] = { ...next[entryIndex], [field]: value };
      return next;
    });

    // 记录到 undo
    pushUndo({
      id: `${appState.activeFilePath}:${entryIndex}`,
      file: appState.activeFilePath ?? "",
      index: entryIndex,
      before: { [field]: before[field as keyof CacheEntry] },
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
      before: deleted,
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

  /** 新增一条空白条目到当前文件末尾，并保存 */
  async function handleAddEntry() {
    const pid = appState.activeProjectId;
    const file = appState.activeFilePath;
    if (!pid || !file) return;

    const maxIndex = entries().reduce((m, e) => Math.max(m, e.index), -1);
    const newEntry = createBlankEntry(maxIndex + 1);

    setEntries((prev) => [...prev, newEntry]);
    pushUndo({
      id: `${file}:add:${newEntry.index}`,
      file,
      index: newEntry.index,
      before: {},
      after: newEntry,
      description: "新增条目",
    });
    markDirty(file);

    await handleBlur();
    // 滚动到新条目
    setTimeout(() => {
      const el = document.getElementById(`entry-${newEntry.index}`);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 60);
  }

  /** 失焦保存：将当前文件的所有条目保存到后端，然后重新获取以刷新问题检测 */
  let savePending = false;

  async function handleBlur() {
    if (savePending) return;
    savePending = true;

    const pid = appState.activeProjectId;
    const file = appState.activeFilePath;
    if (!pid || !file) {
      savePending = false;
      return;
    }

    try {
      // 保存所有条目到后端（传入真实配置文件名，config.inc.yaml 项目也能正确重建 problem）
      await saveCacheFile(pid, file, entries(), getActiveConfigFileName());
      // 重新获取（后端返回含最新 problem 数据）
      const res = await fetchCacheFile(pid, file);
      setEntries(res.entries ?? []);
    } catch {
      // 静默处理
    } finally {
      savePending = false;
    }
  }

  const file = () => appState.activeFilePath;

  return (
    <div class="page page-review">
      {/* ── 工具栏 ── */}
      <div class="review-toolbar">
        <Show when={reviewMode() === "translate"}>
        <Show when={file()}>
          <span class="review-filename">{file()}</span>
        </Show>

        {/* 增删改快捷按钮 */}
        <Show when={file()}>
          <div class="review-actions">
            <button
              class="btn btn--sm btn--primary"
              onClick={handleAddEntry}
              title="在当前文件末尾新增一条空白条目"
            >
              <svg
                width="13"
                height="13"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2.4"
                style="flex-shrink:0"
              >
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
              新增条目
            </button>
          </div>
        </Show>

        {/* 文件内查找 */}
        <Show when={findOpen()}>
          <div class="find-in-file">
            <input
              class="find-input"
              placeholder="查找（原文/译文/问题）"
              value={findQuery()}
              onInput={(e) => setFindQuery(e.currentTarget.value)}
              onKeyDown={(e) => e.key === "Escape" && handleFindInFile()}
              autofocus
            />
            <Show when={findQuery().trim()}>
              <span class="find-in-file-count">
                {filteredEntries().length}/{entries().length}
              </span>
            </Show>
            <button class="find-in-file-close" onClick={handleFindInFile}>
              ×
            </button>
          </div>
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
          <span class="review-count">{entries().length} 条</span>
        </Show>
        </Show>{/* /translate mode */}

        <Show when={reviewMode() === "metadata"}>
          <span class="review-filename">{metaName()}</span>
          <Show when={metaEntries().length > 0}>
            <span class="review-count">{metaEntries().length} 条</span>
          </Show>
        </Show>
      </div>

      {/* ── 条目列表 ── */}
      <div class="review-list">
        {/* 元数据模式：渲染元数据 JSON 条目 */}
        <Show when={reviewMode() === "metadata"}>
          <Show when={!metaLoading()} fallback={<p class="review-placeholder">加载中…</p>}>
            <Show
              when={metaEntries().length > 0}
              fallback={
                <p class="review-placeholder">
                  {appState.activeProjectId ? "该元数据文件暂无条目" : "请先打开翻译项目"}
                </p>
              }
            >
              <Index each={metaEntries()}>
                {(entrySignal, i) => (
                  <MetadataCard
                    entry={entrySignal()}
                    onFieldChange={(f, v) => handleMetaFieldChange(i, f, v)}
                    onDelete={() => handleMetaDelete(i)}
                    onBlur={saveMeta}
                  />
                )}
              </Index>
            </Show>
          </Show>
        </Show>

        {/* 翻译校对模式 */}
        <Show when={reviewMode() !== "metadata"}>
        <Show when={!loading()} fallback={<p class="review-placeholder">加载中…</p>}>
          <Show
            when={filteredEntries().length > 0}
            fallback={
              <p class="review-placeholder">
                {appState.activeProjectId && !file()
                  ? "请在侧栏中选择一个文件"
                  : findQuery().trim()
                    ? "未找到匹配条目"
                    : "该文件暂无条目"}
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
            {/* 用 <Index> 按索引复用 DOM：handleFieldChange 每次生成新的 entry 对象，
                若用 <For>（按引用复用）会把正在编辑的那条 <input> 销毁重建，导致失焦 / IME 中断。
                <Index> 保留节点，仅更新 props 与绑定，输入焦点不丢。 */}
            <Index each={filteredEntries()}>
              {(entrySignal, i) => {
                const entry = entrySignal();
                return (
                  <div id={`entry-${entry.index}`}>
                    <EntryCard
                      entry={entry}
                      expanded={expandedSet().has(i)}
                      onToggleExpand={() => toggleExpand(i)}
                      onSkip={() => handleSkip(i)}
                      onDelete={() => handleDelete(i)}
                      onFieldChange={(field, value) => handleFieldChange(i, field, value)}
                      onBlur={handleBlur}
                    />
                  </div>
                );
              }}
            </Index>
          </Show>
        </Show>
        </Show>
      </div>
    </div>
  );
}
