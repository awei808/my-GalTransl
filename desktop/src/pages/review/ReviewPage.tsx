import { createSignal, createEffect, Index, Show, onCleanup, onMount, createMemo } from "solid-js";
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

/* 把换行控制符渲染为可见明文（\r\n / \n / \r），避免被 pre-wrap 直接解释成真实换行。
   翻译模式三处统一使用：原文、展开只读字段、译文编辑框（textarea）。 */
function toVisibleNewlines(s: unknown): string {
  if (s == null) return "";
  return String(s)
    .replace(/\r\n/g, "\\r\\n")
    .replace(/\n/g, "\\n")
    .replace(/\r/g, "\\r");
}

/* ── 角色名颜色生成（黄金角度 + 感知补偿）── */

interface ThemeConfig {
  baseColor: string;
  mode: "light" | "dark";
}

const LIGHT_THEME: ThemeConfig = { baseColor: "#0066cc", mode: "light" };
const DARK_THEME: ThemeConfig = { baseColor: "#0099ff", mode: "dark" };

/* 前 20 种颜色（由上方算法对 index 0-19 精确计算后固化，避免每次重算并锁定观感）；
   index >= 20 时回退到算法计算（见 generateColorAt）。 */
const LIGHT_PALETTE_20: string[] = [
  "#0066cc", "#cc002a", "#00cc11", "#4d00cc", "#c58e20",
  "#00ccc4", "#e000a8", "#5fba12", "#0022cc", "#cc1a00",
  "#00cc55", "#9f00e0", "#c5c520", "#0090cc", "#cc0055",
  "#27ba12", "#2200cc", "#cc5e00", "#00cc99", "#e000d6",
];
const DARK_PALETTE_20: string[] = [
  "#0099ff", "#ff004f", "#1ae817", "#4600ff", "#ff9100",
  "#00f0ce", "#ff1adc", "#8ce817", "#0044ff", "#ff0700",
  "#00ff51", "#a51aff", "#dbc924", "#00c1f0", "#ff0083",
  "#46e817", "#1200ff", "#ff5c00", "#00ffa6", "#f21aff",
];

function hexToHsl(hex: string): [number, number, number] {
  const r = parseInt(hex.slice(1, 3), 16) / 255;
  const g = parseInt(hex.slice(3, 5), 16) / 255;
  const b = parseInt(hex.slice(5, 7), 16) / 255;
  const max = Math.max(r, g, b),
    min = Math.min(r, g, b);
  const l = (max + min) / 2;
  let h = 0,
    s = 0;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    switch (max) {
      case r:
        h = ((g - b) / d + (g < b ? 6 : 0)) / 6;
        break;
      case g:
        h = ((b - r) / d + 2) / 6;
        break;
      case b:
        h = ((r - g) / d + 4) / 6;
        break;
    }
  }
  return [h * 360, s * 100, l * 100];
}

function hslToHex(h: number, s: number, l: number): string {
  h = (((h % 360) + 360) % 360);
  s = Math.max(0, Math.min(100, s)) / 100;
  l = Math.max(0, Math.min(100, l)) / 100;
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l - c / 2;
  let r = 0,
    g = 0,
    b = 0;
  if (h < 60) {
    r = c;
    g = x;
  } else if (h < 120) {
    r = x;
    g = c;
  } else if (h < 180) {
    g = c;
    b = x;
  } else if (h < 240) {
    g = x;
    b = c;
  } else if (h < 300) {
    r = x;
    b = c;
  } else {
    r = c;
    b = x;
  }
  const toHex = (v: number) => Math.round((v + m) * 255).toString(16).padStart(2, "0");
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

function hashId(id: string | number): number {
  const str = String(id);
  let hash = 2166136261;
  for (let i = 0; i < str.length; i++) {
    hash ^= str.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

const GOLDEN_ANGLE = 137.5077640500378;

function perceptualAdjust(hue: number, baseSat: number, baseLight: number, mode: "light" | "dark") {
  const h = (((hue % 360) + 360) % 360);
  let sat = baseSat,
    light = baseLight;
  if (h >= 40 && h <= 80) {
    sat *= 0.72;
    if (mode === "light") light = Math.min(light + 5, 65);
  } else if (h > 80 && h <= 120) {
    sat *= 0.82;
  } else if (h >= 170 && h <= 200) {
    if (mode === "dark") light = Math.max(light - 3, 35);
  } else if (h >= 270 && h <= 320) {
    if (mode === "light") light = Math.min(light + 4, 60);
    if (mode === "dark") light = Math.max(light + 5, 50);
  }
  return { sat, light };
}

function generateColorAt(index: number, config: ThemeConfig): string {
  // 前 20 色走固化常量，超出再调用算法（复用下方 hexToHsl/hslToHex/perceptualAdjust）
  if (index >= 0 && index < 20) {
    return (config.mode === "dark" ? DARK_PALETTE_20 : LIGHT_PALETTE_20)[index];
  }
  const [baseHue, baseSat, baseLight] = hexToHsl(config.baseColor);
  const hue = (baseHue + index * GOLDEN_ANGLE) % 360;
  const { sat, light } = perceptualAdjust(hue, baseSat, baseLight, config.mode);
  return hslToHex(hue, sat, light);
}

/** 根据角色名确定性获取颜色（同一名字永远同色） */
function getNameColor(name: string): string {
  if (!name) return "#999";
  const idx = hashId(name) % 10000;
  // 检测当前主题
  const isDark = document.documentElement.classList.contains("dark") ||
    document.documentElement.getAttribute("data-theme") === "dark" ||
    window.matchMedia("(prefers-color-scheme: dark)").matches;
  return generateColorAt(idx, isDark ? DARK_THEME : LIGHT_THEME);
}

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

  // 角色名颜色：同一名字确定性映射到同色
  const nameColor = createMemo(() => getNameColor(String(e().name || "")));

  // 译文多行文本框：随内容自动增高（单行 <input> 会吞掉换行，故改为 <textarea>）
  let dstRef: HTMLTextAreaElement | undefined;
  const autoGrowDst = (el: HTMLTextAreaElement | undefined) => {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  };
  // 译文内容变化（键入 / 重载 / 排序切条）时重新适配高度
  createEffect(() => {
    void e().pre_dst;
    autoGrowDst(dstRef);
  });

  return (
    <div class={`entry-card ${hasProblem() ? "has-problem" : ""}`}>
      {/* ── 默认 3 行 ── */}
      <div class="entry-default">
        {/* 问题行 */}
        <div class="entry-problem">
          <span class="entry-index">#{e().index}</span>
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
        </div>

        {/* 原文行 / 译文行 — 并排 */}
        <div class="entry-text-row">
          <div class="entry-src">
            <Show when={e().name}>
              <span
                class="entry-name-badge"
                style={{ "background-color": nameColor(), color: "#fff" }}
              >
                {e().name}
              </span>
            </Show>
            {toVisibleNewlines(e().pre_src)}
          </div>
          <textarea
            ref={dstRef}
            class="entry-dst-input"
            rows="1"
            value={toVisibleNewlines(e().pre_dst)}
            onInput={(ev) => {
              props.onFieldChange("pre_dst", ev.currentTarget.value);
              autoGrowDst(ev.currentTarget);
            }}
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
                      {val != null ? toVisibleNewlines(val) : "—"}
                    </span>
                  }
                >
                  <textarea
                    class="field-value field-value--editable"
                    rows="2"
                    value={toVisibleNewlines(val ?? "")}
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

/* ── 单条元数据组件（FileMetaData / BatchMetadata）──
   简化：一个 id 小文本框 + 一个记录其余内容的大文本框（JSON）。 */
function MetadataCard(props: {
  entry: MetadataEntry;
  index: number;
  onContentChange: (text: string) => void;
  onDelete: () => void;
  onBlur: () => void;
}) {
  let taRef: HTMLTextAreaElement | undefined;
  const restJson = () => {
    const { id: _id, ...rest } = props.entry as Record<string, unknown>;
    try {
      return JSON.stringify(rest, null, 2);
    } catch {
      return "{}";
    }
  };
  const [content, setContent] = createSignal(restJson());
  // 外部 entry 变更（如保存后 store 更新）且文本框未聚焦时，同步显示
  createEffect(() => {
    void props.entry;
    if (taRef && document.activeElement !== taRef) setContent(restJson());
  });

  return (
    <div class="meta-card">
      <div class="meta-card-head">
        <span class="meta-id-text" title="条目 id（只读，不可修改）">
          id: {String((props.entry as Record<string, unknown>).id ?? "") || "—"}
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
      <textarea
        ref={taRef}
        class="meta-content-textarea"
        rows="10"
        value={content()}
        spellcheck={false}
        onInput={(e) => {
          setContent(e.currentTarget.value);
          props.onContentChange(e.currentTarget.value);
        }}
        onBlur={props.onBlur}
      />
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

/* ── ReviewPage 主组件 ── */
export function ReviewPage() {
  const [entries, setEntries] = createSignal<CacheEntry[]>([]);
  const [loading, setLoading] = createSignal(false);
  const [expandedSet, setExpandedSet] = createSignal<Set<number>>(new Set());
  const [jumpValue, setJumpValue] = createSignal("");

  // ── 模式由打开文件所在的缓存子目录隐式决定，无需手动切换 ──
  // 缓存目录分工（见 CLAUDE.md / GalTransl.__init__）：
  //   pass0_cache → GlobalPrompt.json  （全局提示词，单对象）
  //   pass1_cache → FileMetaData.json （文件级元数据）
  //   pass2_cache → BatchMetadata.json（批次级元数据）
  //   pass3_cache → *.txt.json        （翻译缓存，CacheEntry 数组）
  // 规则：pass0/pass1/pass2 三个缓存目录一律按元数据模式读取；
  //       仅 pass3_cache（翻译缓存）走翻译校对模式。
  type ReviewMode = "translate" | "metadata";
  type MetadataFileName = "FileMetaData.json" | "BatchMetadata.json" | "GlobalPrompt.json";
  function modeInfoOf(path: string | null | undefined): {
    mode: ReviewMode;
    metaName: MetadataFileName;
  } {
    if (!path) return { mode: "translate", metaName: "FileMetaData.json" };
    const norm = path.replace(/\\/g, "/");
    if (norm.includes("pass0_cache/")) return { mode: "metadata", metaName: "GlobalPrompt.json" };
    if (norm.includes("pass1_cache/")) return { mode: "metadata", metaName: "FileMetaData.json" };
    if (norm.includes("pass2_cache/")) return { mode: "metadata", metaName: "BatchMetadata.json" };
    // pass3_cache 及 transl_cache 根目录等 → 翻译校对模式
    return { mode: "translate", metaName: "FileMetaData.json" };
  }
  const reviewMode = createMemo<ReviewMode>(() => modeInfoOf(appState.activeFilePath).mode);
  const metaName = createMemo<MetadataFileName>(
    () => modeInfoOf(appState.activeFilePath).metaName,
  );
  const [metaEntries, setMetaEntries] = createSignal<MetadataEntry[]>([]);
  const [metaLoading, setMetaLoading] = createSignal(false);
  let metaSavePending = false;

  // 元数据显示顺序：默认按后端返回顺序；开启后按 id 升序
  const [metaSortAsc, setMetaSortAsc] = createSignal(false);
  // 计算用于显示的元数据列表（带其在 store 中的真实下标，便于增删改定位）
  const displayMeta = createMemo<Array<{ entry: MetadataEntry; storeIndex: number }>>(() => {
    const indexed = metaEntries().map((entry, storeIndex) => ({ entry, storeIndex }));
    if (!metaSortAsc()) return indexed;
    const toNum = (v: unknown) => {
      if (typeof v === "number") return v;
      const n = parseFloat(String(v ?? ""));
      return isNaN(n) ? null : n;
    };
    return indexed.slice().sort((a, b) => {
      const na = toNum(a.entry.id);
      const nb = toNum(b.entry.id);
      if (na != null && nb != null) return na - nb;
      return String(a.entry.id ?? "").localeCompare(String(b.entry.id ?? ""));
    });
  });

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

  // 按 CacheEntry.index（序号）插入，保持条目按序号有序
  function insertBySerial(next: CacheEntry[], item: CacheEntry): CacheEntry[] {
    const pos = next.findIndex((e) => (e.index ?? 0) > (item.index ?? 0));
    if (pos === -1) next.push(item);
    else next.splice(pos, 0, item);
    return next;
  }

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
        // 被删除的条目：恢复（按序号插入到正确位置）
        if (Object.keys(entry.before).length > 0 && (entry.before as Record<string, unknown>).index != null) {
          insertBySerial(next, entry.before as unknown as CacheEntry);
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

  // 加载（或局部刷新）当前打开的翻译缓存文件
  // loadToken：每次发起加载自增，响应回来时若已被更新的请求取代则丢弃，
  // 同时校验 activeFilePath 仍匹配目标文件——防止 handleBlur 并发覆写后新文件响应对误丢弃。
  let loadToken = 0;
  let loadedFile = ""; // entries() 当前所代表的（最新一次成功加载的）文件路径
  function loadFile(pid: string, file: string) {
    const targetFile = file;         // 快照：本次请求的目标文件
    const myToken = ++loadToken;
    setLoading(true);
    fetchCacheFile(pid, file)
      .then((res: CacheFileResponse) => {
        if (myToken !== loadToken) return;                        // token 过时
        if (appState.activeFilePath !== targetFile) return;       // 文件已切走
        const all = res.entries ?? [];
        setTotalCount(all.length);
        if (all.length > VIRTUAL_THRESHOLD && !showAll()) {
          setEntries(all.slice(0, VIRTUAL_LIMIT));
        } else {
          setEntries(all);
        }
        loadedFile = file;                                        // 记录 entries() 当前所属文件
        setExpandedSet(new Set<number>());
      })
      .catch(() => {
        // 仅当本次请求仍是最新且文件未切走时，才清空避免显示旧文件残留
        if (myToken === loadToken && appState.activeFilePath === targetFile) {
          setEntries([]);
          loadedFile = "";
          setTotalCount(0);
        }
      })
      .finally(() => { if (myToken === loadToken) setLoading(false); });
  }

  // 切换文件 / 进入翻译模式时加载
  createEffect(() => {
    const pid = appState.activeProjectId;
    const file = appState.activeFilePath;
    if (reviewMode() !== "translate") return;
    if (!pid || !file) {
      setEntries([]);
      loadedFile = "";
      setTotalCount(0);
      setShowAll(false);
      clearUndo();
      return;
    }
    loadFile(pid, file);
  });

  // 缓存监控：当前打开文件大小变化时，局部重新拉取并渲染（不重载整个文件列表/丢失滚动）
  createEffect(() => {
    const v = appState.cacheVersion;
    const pid = appState.activeProjectId;
    const file = appState.activeFilePath;
    if (reviewMode() !== "translate" || !pid || !file) return;
    if (v === 0) return; // 初始进入由上方加载 effect 处理
    loadFile(pid, file);
  });

  function handleShowAll() {
    setShowAll(true);
    const pid = appState.activeProjectId;
    const file = appState.activeFilePath;
    if (pid && file) loadFile(pid, file);
  }

  // ── 元数据加载 / 保存 / 删除 ──
  createEffect(() => {
    const pid = appState.activeProjectId;
    const name = metaName();
    void appState.cacheVersion; // 依赖：元数据文件大小变化时自动重载
    if (reviewMode() !== "metadata" || !pid) {
      setMetaEntries([]);
      loadedFile = "";
      return;
    }
    setMetaLoading(true);
    fetchProjectMetadata(pid, name)
      .then((res) => setMetaEntries(res.entries ?? []))
      .catch(() => setMetaEntries([]))
      .finally(() => setMetaLoading(false));
  });

  function handleMetaContentChange(index: number, text: string) {
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(text);
    } catch {
      return; // 解析失败暂不更新 store
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return;
    setMetaEntries((prev) => {
      const next = prev.slice();
      const id = next[index]?.id ?? "";
      next[index] = { id, ...parsed };
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

  function handleFieldChange(serial: number, field: string, value: string) {
    const pos = entries().findIndex((e) => e.index === serial);
    if (pos === -1) return;
    const before = { ...entries()[pos] };
    setEntries((prev) => {
      const next = [...prev];
      next[pos] = { ...next[pos], [field]: value };
      return next;
    });

    // 记录到 undo（统一以条目序号 entry.index 为身份，避免过滤/虚拟滚动下下标错位）
    pushUndo({
      id: `${appState.activeFilePath}:${serial}`,
      file: appState.activeFilePath ?? "",
      index: serial,
      before: { [field]: before[field as keyof CacheEntry] },
      after: { [field]: value },
      description: `修改 ${ALL_FIELDS.find((f) => f.key === field)?.label ?? field}`,
    });

    // 标记 dirty
    if (appState.activeFilePath) markDirty(appState.activeFilePath);
  }

  function handleSkip(serial: number) {
    // 跳过检查 = 去除 problem 标记
    handleFieldChange(serial, "problem", "");
  }

  function handleDelete(serial: number) {
    const pos = entries().findIndex((e) => e.index === serial);
    if (pos === -1) return;
    const deleted = entries()[pos];

    pushUndo({
      id: `${appState.activeFilePath}:${serial}`,
      file: appState.activeFilePath ?? "",
      index: serial,
      before: deleted,
      after: {},
      description: "删除条目",
    });

    // 按条目序号删除当前条目（而非数组下标，过滤/虚拟滚动下均正确）
    setEntries((prev) => prev.filter((e) => e.index !== serial));
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

  /** 失焦保存：将当前文件的所有条目保存到后端，然后重新获取以刷新问题检测 */
  let savePending = false;

  async function handleBlur() {
    if (savePending) return;
    savePending = true;

    const pid = appState.activeProjectId;
    const myFile = loadedFile; // entries() 当前真正所属的文件，不取 activeFilePath（切文件时可能已变）
    if (!pid || !myFile) {
      savePending = false;
      return;
    }
    // 若用户已切换到其他文件，entries() 可能已不代表 myFile，放弃保存以免用旧数据覆盖新文件
    if (appState.activeFilePath !== myFile) {
      savePending = false;
      return;
    }

    try {
      // 保存所有条目到后端（传入真实配置文件名，config.inc.yaml 项目也能正确重建 problem）
      await saveCacheFile(pid, myFile, entries(), getActiveConfigFileName());
      // 守卫：保存后再次校验，若期间已切到别的文件，停止并用旧数据污染界面
      if (appState.activeFilePath !== myFile) {
        savePending = false;
        return;
      }
      const res = await fetchCacheFile(pid, myFile);
      if (appState.activeFilePath !== myFile) {
        savePending = false;
        return;
      }
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
            <button
              class="btn btn--sm review-sort-btn"
              onClick={() => setMetaSortAsc(!metaSortAsc())}
              title="切换元数据显示顺序：默认顺序 / 按 id 排序"
            >
              {metaSortAsc() ? "默认排序" : "id排序"}
            </button>
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
            <Index each={displayMeta()}>
              {(itemSignal) => (
                <MetadataCard
                  entry={itemSignal().entry}
                  index={itemSignal().storeIndex}
                  onContentChange={(t) => handleMetaContentChange(itemSignal().storeIndex, t)}
                  onDelete={() => handleMetaDelete(itemSignal().storeIndex)}
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
              {(entrySignal) => {
                const entry = entrySignal();
                return (
                  <div id={`entry-${entry.index}`}>
                    <EntryCard
                      entry={entry}
                      expanded={expandedSet().has(entry.index)}
                      onToggleExpand={() => toggleExpand(entry.index)}
                      onSkip={() => handleSkip(entry.index)}
                      onDelete={() => handleDelete(entry.index)}
                      onFieldChange={(field, value) => handleFieldChange(entry.index, field, value)}
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
