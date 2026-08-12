import { createSignal, createEffect, Show, For, Index, onCleanup, onMount, createMemo } from "solid-js";
import { appState, setAppState, markDirty, markClean, getActiveConfigFileName } from "../../stores/appStore";
import { confirm } from "../../stores/confirmStore";
import { pushUndo, clearUndo, undo, redo, peekUndo, peekRedo } from "../../stores/undoStore";
import type { UndoEntry } from "../../stores/undoStore";
import {
  fetchCacheFile,
  saveCacheFile,
  fetchPerFileMetadata,
  savePerFileMetadata,
  checkCacheProblems,
  fetchNameDict,
} from "../../lib/api/project";
import { getCachePageSizePreference } from "../../lib/api/preferences";
import { toast } from "../../stores/toastStore";
import { getErrorMessage } from "../../lib/errors";
import type { CacheEntry, MetadataEntry, MetadataType, ProblemTypeInfo } from "../../lib/api/types";
import { fetchProblemTypes } from "../../lib/api/general";
import { problemTypesOf } from "../../lib/problems";
import { isDarkTheme, themeDark } from "../../lib/theme";
import { PlotRoutePanel } from "./PlotRoutePanel";

/**
 * 判断当前是否应让出原生撤销/重做（草稿态）。
 * 主译文框或元数据框，焦点仍在 textarea 且内容未提交时，让出原生实现逐字符撤销；
 * 已提交（失焦）或其它编辑器走自定义操作级撤销。
 *
 * Args:
 *   activeEl: 当前聚焦元素。
 *   entries: 当前文件的翻译条目（用于比对主译文框已提交值）。
 *   metaDraftDirty: 元数据框是否有未提交草稿（与撤销基线不同），由调用方计算后传入。
 */
export function shouldYieldToNative(
  activeEl: Element | null,
  entries: CacheEntry[],
  metaDraftDirty?: boolean,
): boolean {
  if (!(activeEl instanceof HTMLTextAreaElement)) return false;
  // 元数据框：草稿未提交即让出原生逐字符撤销
  if (activeEl.classList.contains("meta-content-textarea")) return Boolean(metaDraftDirty);
  if (!activeEl.classList.contains("entry-dst-input")) return false;
  const serial = Number(activeEl.dataset.index);
  if (!Number.isFinite(serial)) return false;
  const committed = entries.find((e) => e.index === serial)?.pre_dst ?? "";
  return activeEl.value !== committed;
}

// 跨文件撤销/重做的在途恢复状态（在 ReviewPage 闭包内维护，导出类型供测试使用）
export interface PendingRestore {
  entry: UndoEntry;
  dir: "undo" | "redo";
}

// 跨文件恢复的最终决策结果
export type CrossFileDecision =
  | { kind: "apply" }
  | { kind: "wait" }
  | { kind: "cancel"; reason: "switched" | "meta-load-failed" | "history-changed" };

/**
 * 跨文件恢复 effect 的决策纯函数：根据在途状态、当前文件路径、就绪情况、元数据加载结果、栈顶探测，
 * 判定应"应用恢复 / 等待加载 / 取消（并给出原因）"。响应式读取（entries/metaEntry/metaLoading）
 * 由调用方在 effect 内完成，本函数只做纯决策，便于单元测试。
 *
 * Args:
 *   pending: 在途跨文件恢复状态，null 表示无。
 *   currentFilePath: 当前实际激活的文件路径（appState.activeFilePath）。
 *   ready: 目标文件内容是否已加载就绪（translate: loadedFile===target；metadata: metaEntry!==null）。
 *   metaLoadFailed: 元数据文件加载是否失败（!metaLoading && metaEntry===null）。
 *   probe: 跳转期间栈顶探测记录（peekUndo/peekRedo），用于校验历史是否被新操作取代。
 */
export function decideCrossFileRestore(args: {
  pending: PendingRestore | null;
  currentFilePath: string | null;
  ready: boolean;
  metaLoadFailed: boolean;
  probe: UndoEntry | null;
}): CrossFileDecision {
  if (!args.pending) return { kind: "wait" };
  if (args.currentFilePath !== args.pending.entry.file) return { kind: "cancel", reason: "switched" };
  if (args.metaLoadFailed) return { kind: "cancel", reason: "meta-load-failed" };
  if (!args.ready) return { kind: "wait" };
  if (args.probe?.id !== args.pending.entry.id) return { kind: "cancel", reason: "history-changed" };
  return { kind: "apply" };
}

/* 把换行控制符渲染为可见明文（\r\n / \n / \r），避免被 pre-wrap 直接解释成真实换行。
   翻译模式三处统一使用：原文、展开只读字段、译文编辑框（textarea）。 */
function toVisibleNewlines(s: unknown): string {
  if (s == null) return "";
  return String(s)
    .replace(/\r\n/g, "\\r\\n")
    .replace(/\n/g, "\\n")
    .replace(/\r/g, "\\r");
}

// 键盘快捷键分派（可导出纯函数，便于单元测试）
export type KeyAction = "undo" | "redo" | "save";

export interface KeyEventLike {
  key: string;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
}

export function resolveKeyAction(e: KeyEventLike): KeyAction | null {
  if (!e.ctrlKey && !e.metaKey) return null;
  // Ctrl+Shift+Z 时 key 为大写 "Z"，统一转小写判断，保证与系统惯例一致
  const key = e.key.toLowerCase();
  if (key === "z") return e.shiftKey ? "redo" : "undo";
  if (key === "y") return "redo";
  if (key === "s") return "save";
  return null;
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
  return generateColorAt(idx, isDarkTheme() ? DARK_THEME : LIGHT_THEME);
}

/** 显示层角色名翻译：name 可能是字符串或数组，仅在展示时应用替换表，不修改缓存数据 */
function displaySpeakerName(name: string | string[], nameDict: Record<string, string>): string {
  if (Array.isArray(name)) return name.map((n) => nameDict[n] ?? n).join(" / ");
  return nameDict[name] ?? name;
}

/* ── 单条 CacheEntry 组件 ── */
function EntryCard(props: {
  entry: CacheEntry;
  onSkip: () => void;
  onDelete: () => void;
  onFieldChange: (field: string, value: string) => void;
  onSwapAlt: () => void;
  onSave?: () => void;
  // 展开状态受控（父级持有，条目翻页卸载重建后仍能恢复）
  expanded: boolean;
  onToggleExpanded: () => void;
  // 可选：主译文框聚焦状态上报（虚拟滚动"钉住"编辑项用；分页模式不传）
  onFocusChange?: (focused: boolean) => void;
  // 角色名替换表（仅显示层使用，不写入缓存）
  nameDict: Record<string, string>;
}) {
  const e = () => props.entry;
  const hasProblem = () => !!e().problem;

  // 角色名颜色：同一名字确定性映射到同色（依赖 themeDark，主题切换时自动重算）
  const nameColor = createMemo(() => {
    themeDark();
    return getNameColor(String(e().name || ""));
  });

  // 本地译文草稿——键入时只更新此信号，不触发父级 entries 级联重算，仅在失焦时提交
  let dstRef: HTMLTextAreaElement | undefined;
  const [draftDst, setDraftDst] = createSignal(e().pre_dst ?? "");
  createEffect(() => {
    const v = e().pre_dst ?? "";
    // 译文框正聚焦（用户正在输入）时不覆盖草稿，避免 refetch 回填打断输入
    if (dstRef && document.activeElement === dstRef) return;
    setDraftDst(() => v);
  });

  return (
    <div class={`entry-card ${hasProblem() ? "has-problem" : ""} ${e().skip_check ? "skip-check" : ""}`}>
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
          <Show when={!hasProblem() && e().skip_check}>
            <span class="entry-skip-badge">⏭</span>
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
                {displaySpeakerName(e().name, props.nameDict)}
              </span>
            </Show>
            {toVisibleNewlines(e().pre_src)}
          </div>
          <textarea
            ref={dstRef}
            class="entry-dst-input"
            data-index={e().index}
            rows="2"
            value={draftDst()}
            onInput={(ev) => {
              setDraftDst(ev.currentTarget.value);
              // 打字时即标记"未保存"，避免脏指示滞后到失焦才出现
              if (appState.activeFilePath) markDirty(appState.activeFilePath);
            }}
            onFocus={() => props.onFocusChange?.(true)}
            onBlur={() => {
              // 失焦时把译文草稿提交到内存（实时进入 entries），保存由用户手动触发
              props.onFieldChange("pre_dst", draftDst());
              props.onFocusChange?.(false);
            }}
            onKeyDown={(e) => {
              if (e.key !== "Enter") return;
              e.preventDefault();
              const ta = e.currentTarget as HTMLTextAreaElement;
              const pos = ta.selectionStart;
              const newVal = ta.value.slice(0, pos) + "\n" + ta.value.slice(ta.selectionEnd);
              setDraftDst(newVal);
              if (appState.activeFilePath) markDirty(appState.activeFilePath);
              requestAnimationFrame(() => {
                ta.selectionStart = ta.selectionEnd = pos + 1;
              });
            }}
          />
        </div>

        {/* 右侧操作按钮 */}
        <div class="entry-actions">
          <Show when={e().alt_dst}>
            <button
              class="entry-btn entry-btn--swap"
              title="点击交换当前译文与备选译文（AI 改进轮给出的备选译文）"
              onClick={props.onSwapAlt}
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
              >
                <path d="M7 16V4m0 0L3 8m4-4l4 4" />
                <path d="M17 8v12m0 0l4-4m-4 4l-4-4" />
              </svg>
              <span class="entry-btn-text">备选译文</span>
            </button>
          </Show>
          <button
            class="entry-btn"
            title="展开/收起全部字段"
            onClick={props.onToggleExpanded}
          >
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
          <button
            class={`entry-btn ${e().skip_check ? "entry-btn--skip-active" : ""}`}
            title={e().skip_check ? "恢复对该条目的检查" : "跳过该条目的检查"}
            onClick={props.onSkip}
          >
            <Show
              when={e().skip_check}
              fallback={
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
              }
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
              >
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                <path d="M4 4l16 16" />
              </svg>
            </Show>
            <span class="entry-btn-text">{e().skip_check ? "正常检查" : "跳过检查"}</span>
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
                    data-field-key={field.key}
                    value={val != null ? String(val) : ""}
                    onInput={(ev) => props.onFieldChange(field.key, ev.currentTarget.value)}
                    onBlur={props.onSave}
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
  onDelete?: () => void;
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
        {/* 右上角删除按钮（仅多条目模式显示） */}
        <Show when={props.onDelete}>
          <button class="entry-btn entry-btn--danger" title="删除该条目" onClick={props.onDelete!}>
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
        </Show>
      </div>
      <textarea
        ref={taRef}
        class="meta-content-textarea"
        rows="20"
        value={content()}
        spellcheck={false}
        onInput={(e) => {
          setContent(e.currentTarget.value);
          props.onContentChange(e.currentTarget.value);
        }}
        onKeyDown={(e) => {
          if (e.key !== "Enter") return;
          e.preventDefault();
          const ta = e.currentTarget as HTMLTextAreaElement;
          const pos = ta.selectionStart;
          const newVal = ta.value.slice(0, pos) + "\n" + ta.value.slice(ta.selectionEnd);
          setContent(newVal);
          props.onContentChange(newVal);
          requestAnimationFrame(() => {
            ta.selectionStart = ta.selectionEnd = pos + 1;
          });
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
  { key: "alt_dst", label: "备选译文" },
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
  const [jumpValue, setJumpValue] = createSignal("");
  // 角色名替换表：仅在显示层翻译角色名，不写入缓存数据（缓存 name 保持原始值，保证缓存 key 稳定）
  const [nameDict, setNameDict] = createSignal<Record<string, string>>({});

  // ── 模式由打开文件所在的缓存子目录隐式决定，无需手动切换 ──
  // 缓存目录分工（见 CLAUDE.md / GalTransl.__init__）：
  //   pass0_cache → GlobalPrompt.json  （全局提示词，单对象）
  //   pass0_cache → GlobalPrompt     （单对象全局提示词）
  //   pass1_cache → *.meta.json       （per-file 文件级元数据）
  //   pass2_cache → *.batch.json      （per-file 批次级元数据）
  //   pass3_cache → *.txt.json        （翻译缓存，CacheEntry 数组）
  type ReviewMode = "translate" | "metadata";
  function modeInfoOf(path: string | null | undefined): {
    mode: ReviewMode;
    metaType: MetadataType;
    sourceFile: string;
  } {
    if (!path) return { mode: "translate", metaType: "filemeta", sourceFile: "" };
    const norm = path.replace(/\\/g, "/");
    const base = norm.split("/").pop() ?? "";
    if (norm.includes("pass0_cache/")) {
      // PlotRouteMap.json 为剧情路线图（mermaid 专用编辑器）；GlobalPrompt.json 仍走 globalprompt
      if (norm.endsWith("PlotRouteMap.json"))
        return { mode: "metadata", metaType: "plotroute", sourceFile: "" };
      return { mode: "metadata", metaType: "globalprompt", sourceFile: "" };
    }
    if (norm.includes("pass1_cache/")) {
      // 从 {filename}.meta.json 提取源文件名
      const src = base.replace(/\.meta\.json$/, "");
      return { mode: "metadata", metaType: "filemeta", sourceFile: src };
    }
    if (norm.includes("pass2_cache/")) {
      const src = base.replace(/\.batch\.json$/, "");
      return { mode: "metadata", metaType: "batchmeta", sourceFile: src };
    }
    return { mode: "translate", metaType: "filemeta", sourceFile: "" };
  }
  const reviewMode = createMemo<ReviewMode>(() => modeInfoOf(appState.activeFilePath).mode);
  const metaType = createMemo<MetadataType>(() => modeInfoOf(appState.activeFilePath).metaType);
  const metaSourceFile = createMemo(() => modeInfoOf(appState.activeFilePath).sourceFile);
  const [metaEntry, setMetaEntry] = createSignal<MetadataEntry | null>(null);
  const [metaLoading, setMetaLoading] = createSignal(false);
  let metaSavePending = false;
  // 当前元数据文件是否有未保存修改（编辑置 true，保存成功/加载新文件后复位 false）
  let metaDirty = false;
  // 元数据切换令牌：每次 effect 触发递增，过期切换闭包（保存/加载响应）直接丢弃
  let metaSwitchToken = 0;
  // 非法 JSON 已提示标志：连续非法输入只提示一次，避免 onInput 逐键 toast 轰炸
  let metaJsonInvalidShown = false;
  // 当前打开的元数据文件完整路径（切换保存时用于推导旧文件的 metaType/sourceFile）
  let metaLoadedFullPath = "";
  // 元数据撤销基线：最近一次提交态快照（编辑入栈基准，撤销/重做后同步更新）
  let metaUndoBase: MetadataEntry | null = null;
  // 磁盘态快照：最近一次加载/保存成功后的值（用于正确计算 dirty）
  let metaDiskSnapshot: MetadataEntry | null = null;

  // 深比较元数据对象：键序变化视为内容变化，与 textarea 文本编辑语义一致
  function metaEqual(a: MetadataEntry | null | undefined, b: MetadataEntry | null | undefined): boolean {
    return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
  }


  // ── 快捷筛选 ──
  const [filterProblemsOnly, setFilterProblemsOnly] = createSignal(false);
  const [filterProblemType, setFilterProblemType] = createSignal("all");
  const [filterSpeaker, setFilterSpeaker] = createSignal("all");
  const [filterAltOnly, setFilterAltOnly] = createSignal(false);
  const [problemTypes, setProblemTypes] = createSignal<ProblemTypeInfo[]>([]);

  // 根据快捷筛选过滤条目（文件内查找已改为 Ctrl+F 全局查找浮层，不再在此过滤）
  const filteredEntries = createMemo(() => {
    let list = entries();
    if (filterProblemsOnly()) list = list.filter((e) => !!e.problem);
    if (filterAltOnly()) list = list.filter((e) => !!e.alt_dst);
    const ptype = filterProblemType();
    if (ptype !== "all") list = list.filter((e) => problemTypesOf(e.problem).includes(ptype));
    const spk = filterSpeaker();
    if (spk === "__no_speaker__") list = list.filter((e) => !String(e.name ?? "").trim());
    else if (spk !== "all") list = list.filter((e) => (e.name ?? "") === spk);
    return list;
  });

  // 说话人列表与问题统计（供筛选下拉与计数展示）
  const speakers = createMemo(() =>
    Array.from(new Set(entries().map((e) => String(e.name ?? "").trim()).filter(Boolean))),
  );
  const problemCount = createMemo(() => filteredEntries().filter((e) => !!e.problem).length);
  const hasFilter = () =>
    filterProblemsOnly() ||
    filterAltOnly() ||
    filterProblemType() !== "all" ||
    filterSpeaker() !== "all";
  function clearFilters() {
    setFilterProblemsOnly(false);
    setFilterAltOnly(false);
    setFilterProblemType("all");
    setFilterSpeaker("all");
  }


  // ── 键盘快捷键（撤销/重做） ──
  function handleKeyDown(e: KeyboardEvent) {
    const action = resolveKeyAction(e);
    if (!action) return;
    // 草稿态（主译文框或元数据框，焦点在框内且内容未提交）：让出原生撤销/重做，实现输入中逐字符撤销
    const metaDraftDirty = metaUndoBase !== null && metaEntry() !== null && !metaEqual(metaEntry(), metaUndoBase);
    if ((action === "undo" || action === "redo") && shouldYieldToNative(document.activeElement, entries(), metaDraftDirty)) {
      return;
    }
    e.preventDefault();
    if (action === "undo") handleUndo();
    else if (action === "redo") handleRedo();
    else if (action === "save") {
      // handleRefresh 内部会先失焦同步草稿，再保存并重检
      if (reviewMode() === "translate") void handleRefresh();
      else void saveMeta();
    }
  }

  // ── 菜单事件（编辑→撤销/重做） ──
  function handleMenuUndo() {
    handleUndo();
  }
  function handleMenuRedo() {
    handleRedo();
  }

  // ── 菜单事件（文件→保存） ──
  function handleMenuSave() {
    // handleRefresh 内部会先失焦同步草稿，再保存并重检
    if (reviewMode() === "translate") void handleRefresh();
    else void saveMeta();
  }

  // 展开字段 textarea 的原生 Enter 处理（Solid 事件委托在 <Show> 内不工作，由 document 监听兜底）
  function handleExpandFieldEnter(e: KeyboardEvent) {
    if (e.key !== "Enter") return;
    const ta = e.target as HTMLElement;
    if (!ta.classList.contains("field-value--editable")) return;
    e.preventDefault();
    e.stopPropagation();
    const pos = (ta as HTMLTextAreaElement).selectionStart;
    const newVal = (ta as HTMLTextAreaElement).value.slice(0, pos) + "\n" + (ta as HTMLTextAreaElement).value.slice((ta as HTMLTextAreaElement).selectionEnd);
    (ta as HTMLTextAreaElement).value = newVal;
    ta.dispatchEvent(new InputEvent("input", { bubbles: true }));
    requestAnimationFrame(() => {
      (ta as HTMLTextAreaElement).selectionStart = (ta as HTMLTextAreaElement).selectionEnd = pos + 1;
    });
  }

  onMount(() => {
    document.addEventListener("keydown", handleKeyDown);
    document.addEventListener("galtransl:undo", handleMenuUndo);
    document.addEventListener("galtransl:redo", handleMenuRedo);
    document.addEventListener("galtransl:save", handleMenuSave);
    void fetchProblemTypes().then((r) => {
      if (r) setProblemTypes(r);
    });
  });

  // 展开字段 Enter 监听器：不依赖 onMount（HMR 后组件不重新挂载），用 createEffect 确保始终注册
  createEffect(() => {
    document.addEventListener("keydown", handleExpandFieldEnter);
  });
  onCleanup(() => {
    document.removeEventListener("keydown", handleKeyDown);
    document.removeEventListener("keydown", handleExpandFieldEnter);
    document.removeEventListener("galtransl:undo", handleMenuUndo);
    document.removeEventListener("galtransl:redo", handleMenuRedo);
    document.removeEventListener("galtransl:save", handleMenuSave);
    // 组件卸载时清除跳转标记，避免残留
    setAppState("reviewJumpToIndex", null);
    // 取消未完成的高亮定位 rAF，避免卸载后继续查询 DOM
    if (flashRAFId) cancelAnimationFrame(flashRAFId);
  });

  // 按 CacheEntry.index（序号）插入，保持条目按序号有序
  function insertBySerial(next: CacheEntry[], item: CacheEntry): CacheEntry[] {
    const pos = next.findIndex((e) => (e.index ?? 0) > (item.index ?? 0));
    if (pos === -1) next.push(item);
    else next.splice(pos, 0, item);
    return next;
  }

  // 主译文框草稿仅在失焦时提交到 entries 并入 undo 栈，
  // 撤销/重做前先失焦提交，否则输入中按 Ctrl+Z 时 undo 栈为空且原生撤销已被快捷键拦截
  function blurDraftInput(): void {
    const ae = document.activeElement;
    if (
      ae instanceof HTMLTextAreaElement &&
      (ae.classList.contains("entry-dst-input") || ae.classList.contains("meta-content-textarea"))
    ) {
      ae.blur();
    }
  }

  // 跨文件撤销/重做在途目标：跳转加载完成后自动恢复；跳转期间不消费 undo 栈，失败不丢记录
  let pendingRestore: PendingRestore | null = null;

  // 元数据未保存编辑先入栈（pushUndo 会清空 redo 栈），使当前编辑成为可撤销的第一步
  function pushMetaDraftIfDirty(): void {
    const currentFile = appState.activeFilePath ?? "";
    if (reviewMode() === "metadata" && metaDirty && metaEntry() && metaUndoBase && !metaEqual(metaUndoBase, metaEntry())) {
      pushUndo({
        id: `${currentFile}:meta`,
        file: currentFile,
        index: 0,
        before: metaUndoBase,
        after: metaEntry()!,
        description: "修改 元数据",
      });
    }
  }

  // 应用一条撤销/重做记录：按记录所属文件的模式分发（translate → entries，metadata → metaEntry）
  function applyUndoEntry(entry: UndoEntry, dir: "undo" | "redo"): void {
    // 合法性校验：撤销需 before、重做需 after，缺失则跳过（避免写入非法值）
    if (dir === "undo" ? !entry.before : !entry.after) {
      console.error(`[ReviewPage] ${dir}记录缺少快照，已跳过：${entry.id}`);
      return;
    }
    if (modeInfoOf(entry.file).mode === "metadata") {
      const target = dir === "undo" ? (entry.before as MetadataEntry) : (entry.after as MetadataEntry);
      setMetaEntry(target);
      metaUndoBase = target;
      metaDirty = !metaEqual(metaEntry(), metaDiskSnapshot);
      return;
    }
    const isAdd = Object.keys(entry.before).length === 0 && Object.keys(entry.after).length > 0;
    if (dir === "undo") {
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
    } else {
      setEntries((prev) => {
        const next = [...prev];
        const idx = next.findIndex((e) => e.index === entry.index);
        if (isAdd) {
          // 新增的重做 = 重新插入
          if (idx === -1) next.splice(entry.index, 0, entry.after as unknown as CacheEntry);
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
    entriesRev++;
    refreshDirtyState();
  }

  // 跨文件撤销/重做：先切换文件（复用现有 runSwitch 的未保存确认与加载），加载完成后由 effect 执行恢复
  async function startCrossFileRestore(entry: UndoEntry, dir: "undo" | "redo"): Promise<void> {
    if (pendingRestore || !entry.file) return;
    pendingRestore = { entry, dir };
    if (import.meta.env?.DEV) {
      console.info(`[ReviewPage] 跨文件${dir === "undo" ? "撤销" : "重做"}：跳转 ${appState.activeFilePath ?? ""} → ${entry.file}`);
    }
    setAppState("activeFilePath", entry.file);
  }

  function handleUndo() {
    blurDraftInput();
    pushMetaDraftIfDirty();
    const currentFile = appState.activeFilePath ?? "";
    const entry = peekUndo();
    if (!entry) {
      toast.info("没有更多可撤销的操作");
      return;
    }
    if (entry.file === currentFile) {
      undo();
      applyUndoEntry(entry, "undo");
    } else {
      void startCrossFileRestore(entry, "undo");
    }
  }

  function handleRedo() {
    blurDraftInput();
    // 不调用 pushMetaDraftIfDirty：压栈会丢弃 redo 分支，导致重做不可用（undo 后基线已同步，无需入栈草稿）
    const currentFile = appState.activeFilePath ?? "";
    const entry = peekRedo();
    if (!entry) {
      toast.info("没有更多可重做的操作");
      return;
    }
    if (entry.file === currentFile) {
      redo();
      applyUndoEntry(entry, "redo");
    } else {
      void startCrossFileRestore(entry, "redo");
    }
  }

  // 当 activeFilePath 变化时加载文件
  // 分页条数："每页条目显示数量"。默认 2000，可修改。
  // 见 lib/api/preferences.ts 的 getCachePageSizePreference。
  const [totalCount, setTotalCount] = createSignal(0);

  // ── 分页状态 ──
  // 每页条数 = 每页条目显示数量（0 表示不分页，一次性显示全部）
  const pageSize = () => getCachePageSizePreference();
  const [page, setPage] = createSignal(0);
  // 分页总数基于当前过滤后的条目集（filteredEntries），而非文件总条数 totalCount，
  // 避免过滤后每页条数与"共 X 条"不一致导致末页为空。
  const totalPages = () =>
    pageSize() > 0 ? Math.max(1, Math.ceil(filteredEntries().length / pageSize())) : 1;
  // 当前页条目切片（分页渲染用）
  const currentPageEntries = createMemo(() => {
    const all = filteredEntries();
    const ps = pageSize();
    if (ps <= 0) return all;
    const start = Math.min(page() * ps, all.length);
    return all.slice(start, start + ps);
  });
  // 过滤条件变化时重置到第 1 页；页码越界时钳制
  createEffect(() => {
    const total = filteredEntries().length;
    const maxPage = pageSize() > 0 ? Math.max(0, Math.ceil(total / pageSize()) - 1) : 0;
    if (page() > maxPage) setPage(maxPage);
  });
  // 过滤条件（问题/说话人/备选）变化时回到第 1 页
  createEffect(() => {
    filterProblemsOnly();
    filterAltOnly();
    filterProblemType();
    filterSpeaker();
    setPage(0);
  });
  // 滚动到当前页顶部（翻页后定位到列表起始）
  const goToPage = (p: number) => {
    setPage(p);
    scrollReviewToTop();
  };

  // 展开状态集合（业务 index）：分页翻页卸载重建后仍能恢复
  const [expandedSerials, setExpandedSerials] = createSignal<ReadonlySet<number>>(new Set());
  const toggleExpanded = (serial: number) => {
    setExpandedSerials((prev) => {
      const next = new Set(prev);
      if (next.has(serial)) next.delete(serial);
      else next.add(serial);
      return next;
    });
  };

  // 业务序号（entry.index）→ filteredEntries 下标；未命中返回 -1
  const filteredIndexFromSerial = (serial: number): number =>
    filteredEntries().findIndex((e) => e.index === serial);

  // 滚动当前页列表到顶部（分页翻页后定位到列表起始）
  const scrollReviewToTop = () => {
    requestAnimationFrame(() => {
      document.querySelector(".review-list")?.scrollTo({ top: 0 });
    });
  };

  // 高亮指定业务序号对应的条目（分页模式下目标在当前页渲染后逐帧重试）
  // 重试有帧数上限 + rAF 句柄可取消：避免目标被过滤后无限循环，卸载后停止查询 DOM
  let flashRAFId = 0;
  const flashEntry = (serial: number): void => {
    let frames = 0;
    const maxFrames = 120; // 约 2 秒（60fps），超过即放弃
    const tryFlash = () => {
      const el = document.querySelector(`.review-list [data-index="${serial}"] .entry-card`) as HTMLElement | null;
      if (!el) {
        // 目标未渲染（分页切换后）：逐帧重试，超过上限放弃
        if (frames++ < maxFrames) flashRAFId = requestAnimationFrame(tryFlash);
        return;
      }
      el.scrollIntoView({ block: "center" });
      el.classList.remove("entry-flash");
      void el.offsetWidth;
      el.classList.add("entry-flash");
      el.addEventListener("animationend", () => el.classList.remove("entry-flash"), { once: true });
    };
    flashRAFId = requestAnimationFrame(tryFlash);
  };

  // 跳转到指定业务序号：先翻到目标条目所在页，再滚动并高亮
  const scrollToSerial = (serial: number): boolean => {
    const fi = filteredIndexFromSerial(serial);
    if (fi < 0) return false;
    const ps = pageSize();
    const targetPage = ps > 0 ? Math.floor(fi / ps) : 0;
    if (targetPage !== page()) setPage(targetPage);
    flashEntry(serial);
    return true;
  };

  // 加载（或局部刷新）当前打开的翻译缓存文件
  // loadToken：每次发起加载自增，响应回来时若已被更新的请求取代则丢弃，
  // 同时校验 activeFilePath 仍匹配目标文件——防止 handleBlur 并发覆写后新文件响应对误丢弃。
  let loadToken = 0;
  let loadedFile = ""; // entries() 当前所代表的（最新一次成功加载的）文件路径
  async function loadFile(pid: string, file: string): Promise<void> {
    const targetFile = file;         // 快照：本次请求的目标文件
    const myToken = ++loadToken;
    setLoading(true);
    try {
      const res = await fetchCacheFile(pid, file);
      if (myToken !== loadToken) return;                        // token 过时
      if (appState.activeFilePath !== targetFile) return;       // 文件已切走
      const all = res.entries ?? [];
      setTotalCount(all.length);
      setPage(0); // 切换文件回到第 1 页
      setExpandedSerials(new Set<number>()); // 切换文件后清空展开状态，避免旧文件的 index 残留
      // 先记录 entries() 所属文件再 setEntries：loadedFile 是非响应式变量，
      // setEntries 会同步触发依赖 entries() 的 effect（含跳转 effect）重跑，
      // 若在 setEntries 之后才赋值 loadedFile，重跑时读到旧值会卡在跳转守卫。
      loadedFile = file;
      setEntries(all); // 分页模式下全量加载，渲染层按页切片显示
      baselineKey = snapshotKey(entries());                     // 重置编辑基线
    } catch {
      // 仅当本次请求仍是最新且文件未切走时，才清空避免显示旧文件残留
      if (myToken === loadToken && appState.activeFilePath === targetFile) {
        setEntries([]);
        loadedFile = "";
        setTotalCount(0);
        setPage(0);
        setExpandedSerials(new Set<number>());
        // 加载失败：取消指向该文件的在途跨文件撤销/重做（记录保留在栈中，用户可重试）
        if (pendingRestore && pendingRestore.entry.file === targetFile) pendingRestore = null;
      }
    } finally {
      if (myToken === loadToken) setLoading(false);
    }
  }

  // 跨文件撤销/重做：目标文件加载完成后自动执行最终恢复；取消/失败/历史被改动时放弃（记录保留在栈中）
  createEffect(() => {
    const pending = pendingRestore;
    if (!pending) return;
    const target = pending.entry.file;
    const info = modeInfoOf(target);
    // 响应式读取（必须留在 effect 内以触发重跑）：translate 依赖 entries 长度，metadata 依赖 metaEntry/metaLoading
    let ready = false;
    let metaLoadFailed = false;
    if (info.mode === "metadata") {
      void metaEntry();
      void metaLoading();
      if (metaLoadedFullPath === target && metaEntry() !== null) ready = true;
      else if (!metaLoading() && metaEntry() === null) metaLoadFailed = true;
    } else {
      void entries().length;
      ready = loadedFile === target;
    }
    const probe = pending.dir === "undo" ? peekUndo() : peekRedo();
    const decision = decideCrossFileRestore({
      pending,
      currentFilePath: appState.activeFilePath,
      ready,
      metaLoadFailed,
      probe,
    });
    if (decision.kind === "wait") return;
    if (decision.kind === "cancel") {
      pendingRestore = null;
      if (decision.reason === "history-changed" && import.meta.env?.DEV) {
        console.warn(`[ReviewPage] 跨文件${pending.dir}目标已被新操作改变，取消自动恢复`);
      }
      if (decision.reason !== "switched") toast.info("撤销/重做目标已变化，已取消自动恢复");
      return;
    }
    pendingRestore = null;
    // 先应用再移动指针：若 apply 抛异常则记录保留在栈中，避免"指针已移、UI 未变"的不一致
    try {
      applyUndoEntry(pending.entry, pending.dir);
    } catch (err) {
      console.error(`[ReviewPage] 跨文件${pending.dir}应用失败，保留记录：${String(err)}`);
      toast.error("撤销/重做应用失败，已保留记录");
      return;
    }
    if (pending.dir === "undo") undo();
    else redo();
    if (info.mode === "translate") setAppState("reviewJumpToIndex", pending.entry.index);
    if (import.meta.env?.DEV) {
      console.info(`[ReviewPage] 跨文件${pending.dir === "undo" ? "撤销" : "重做"}完成：${target}`);
    }
  });

  // 切换文件 / 进入翻译模式时加载；离开有未保存修改的文件前弹确认（保存/放弃/取消）
  let switching = false;
  async function runSwitch(pid: string): Promise<void> {
    if (switching) return;
    switching = true;
    try {
      // 离开 metadata 文件（metaDirty 未保存）：在加载目标前先确认，避免确认与加载并发
      if (metaDirty && metaEntry() && metaLoadedFullPath) {
        const res = await confirm.show({
          title: "未保存的修改",
          message: `文件 ${metaLoadedFullPath} 有未保存的修改，是否保存后再切换？`,
          confirmText: "保存",
          cancelText: "不保存",
          extraText: "取消",
          tone: "warning",
          dismissible: false,
        });
        if (res.action === "extra") {
          // 取消：还原 activeFilePath 留在原文件；metaDirty 保持（下次进入 metadata 时编辑保留）
          setAppState("activeFilePath", metaLoadedFullPath);
          return;
        }
        if (res.confirmed) {
          try {
            const prevInfo = modeInfoOf(metaLoadedFullPath);
            // metaEntry() 在外层 if (metaDirty && metaEntry() && metaLoadedFullPath) 已保证非空
            await savePerFileMetadata(pid, prevInfo.metaType, prevInfo.sourceFile, metaEntry()!);
            // 保存即新的撤销起点（与 saveMeta 一致）：防旧文件残留记录在切回时造成撤销错位
            metaUndoBase = metaEntry();
            metaDiskSnapshot = metaEntry();
            clearUndo();
            metaDirty = false;
          } catch (e) {
            toast.error(`保存 ${metaLoadedFullPath} 失败：${getErrorMessage(e)}`);
            return; // 失败中止切换，留在 metadata
          }
        } else {
          metaDirty = false; // 不保存 → 丢弃
        }
      }
      while (true) {
        const target = appState.activeFilePath;
        if (!target || loadedFile === target) break;
        const prevFile = loadedFile;
        if (prevFile && appState.dirtyFiles.includes(prevFile)) {
          const res = await confirm.show({
            title: "未保存的修改",
            message: `文件 ${prevFile} 有未保存的修改，是否保存后再切换？`,
            confirmText: "保存",
            cancelText: "不保存",
            extraText: "取消",
            tone: "warning",
            dismissible: false,
          });
          // 确认期间文件又被切换：放弃本次，由循环下一轮处理最新目标
          if (appState.activeFilePath !== target) break;
          // 点“取消”：还原 activeFilePath，留在原文件（不保存、不切走）
          if (res.action === "extra") {
            setAppState("activeFilePath", prevFile);
            break;
          }
          if (res.confirmed) {
            try {
              // 等待在途手动保存完成（saveInFlight 在 saveCurrentFile 的 finally 必定释放），
              // 带超时兜底，避免网络挂起时无限等待；防止与在途保存并发双写同一文件
              const deadline = Date.now() + 3000;
              while (saveInFlight && Date.now() < deadline) {
                await new Promise((r) => setTimeout(r, 30));
              }
              if (appState.activeFilePath !== target) break; // 等待期间又切走
              await saveCacheFile(pid, prevFile, entries().slice(), getActiveConfigFileName());
              markClean(prevFile);
            } catch (e) {
              // 保存失败不阻塞切换（dirty 保留，用户可重试）
              toast.error(`保存 ${prevFile} 失败：${getErrorMessage(e)}`);
            }
          } else {
            // 选择“不保存”：丢弃 → 清脏，避免切回时误弹确认
            markClean(prevFile);
          }
        }
        if (appState.activeFilePath !== target) break; // 期间又变，重新循环处理最新
        await loadFile(pid, target);
      }
    } finally {
      switching = false;
    }
  }

  /** 离开当前 dirty 文件前的确认（跨模式切换专用，不依赖目标模式）。加载交给对应模式的 effect */
  async function leaveConfirm(pid: string): Promise<void> {
    if (switching) return;
    // 同步捕获：loadFile effect 先于 metadata effect 执行，此刻 loadedFile 仍是 translate 文件
    const prevFile = loadedFile;
    if (!prevFile || !appState.dirtyFiles.includes(prevFile)) return;
    switching = true;
    try {
      const res = await confirm.show({
        title: "未保存的修改",
        message: `文件 ${prevFile} 有未保存的修改，是否保存后再切换？`,
        confirmText: "保存",
        cancelText: "不保存",
        extraText: "取消",
        tone: "warning",
        dismissible: false,
      });
      if (appState.activeFilePath === prevFile) return; // 弹窗期间已切回原文件
      if (res.action === "extra") {
        setAppState("activeFilePath", prevFile); // 取消：留在原文件
        return;
      }
      if (res.confirmed) {
        try {
          // 等待在途手动保存完成（带超时），防止并发双写
          const deadline = Date.now() + 3000;
          while (saveInFlight && Date.now() < deadline) {
            await new Promise((r) => setTimeout(r, 30));
          }
          await saveCacheFile(pid, prevFile, entries().slice(), getActiveConfigFileName());
          markClean(prevFile);
        } catch (e) {
          // 保存失败不阻塞切换（dirty 保留）
          toast.error(`保存 ${prevFile} 失败：${getErrorMessage(e)}`);
        }
      } else {
        markClean(prevFile); // 不保存 → 丢弃 → 清脏
      }
    } finally {
      switching = false;
    }
  }

  // 角色名替换表加载：项目切换时刷新（仅显示层使用）
  createEffect(() => {
    const pid = appState.activeProjectId;
    if (!pid) {
      setNameDict({});
      return;
    }
    void fetchNameDict(pid)
      .then((res) => setNameDict(res.name_dict ?? {}))
      .catch(() => setNameDict({}));
  });

  createEffect(() => {
    const pid = appState.activeProjectId;
    const file = appState.activeFilePath;
    if (!pid || !file) {
      setEntries([]);
      loadedFile = "";
      setTotalCount(0);
      setPage(0);
      clearUndo();
      return;
    }
    // 目标为 translate 文件：完整切换流程（离开确认 + 加载）
    if (modeInfoOf(file).mode === "translate") {
      void runSwitch(pid);
    } else {
      // 目标为 metadata 文件：仅做“离开当前 dirty translate 文件”确认（加载交给 metadata effect）
      void leaveConfirm(pid);
    }
  });

  // 缓存监控：仅对译文缓存文件做外部大小变化刷新；元数据文件不经过 loadFile（fetchCacheFile 是译文缓存专用，
  // 且 metadata 的外部刷新已由 metadata effect 的 cacheVersion 追踪处理）
  createEffect(() => {
    const v = appState.cacheVersion;
    const pid = appState.activeProjectId;
    const file = appState.activeFilePath;
    // 翻译校对界面完全禁用自动重载：避免保存后后端广播触发整表 loadFile 造成闪烁（改由手动「刷新」按钮）
    if (reviewMode() === "translate") return;
    if (!pid || !file) return;
    if (v === 0) return; // 初始进入由上方加载 effect 处理
    // 本地有未保存修改时跳过自动刷新，避免覆盖乐观删除/编辑或复活已删条目
    if (appState.dirtyFiles.includes(file)) return;
    // 元数据文件不通过 loadFile 刷新（错误语义会拉取 metadata 作为缓存解析）
    if (modeInfoOf(file).mode !== "translate") return;
    loadFile(pid, file);
  });

  // 侧边栏问题列表跳转：文件加载完成后自动滚动到具体条目
  // 依赖 entries() 与 activeFilePath：跨文件跳转时先 return 等待，文件加载完成
  // （setEntries 触发）后 effect 重跑，此时 loadedFile 已匹配即可执行跳转。
  createEffect(() => {
    const idx = appState.reviewJumpToIndex;
    if (idx === null) return;
    // 补充响应式依赖：文件加载完成（setEntries）或切换文件后重跑跳转尝试
    void entries().length;
    void appState.activeFilePath;
    // 文件尚未加载完成：等下一轮（loadedFile 在 loadFile 成功后先于 setEntries 更新）
    if (entries().length === 0 || loadedFile !== appState.activeFilePath) return;
    if (filteredIndexFromSerial(idx) >= 0) {
      scrollToSerial(idx);
      setAppState("reviewJumpToIndex", null);
      if (import.meta.env?.DEV) {
        console.debug(`[ReviewPage] 跳转到 #${idx}（文件 ${loadedFile}）`);
      }
    } else {
      // 目标被过滤或不存在：清除标记，避免 effect 每次条目变化都做无效重试
      setAppState("reviewJumpToIndex", null);
      if (import.meta.env?.DEV) {
        console.debug(`[ReviewPage] 跳转目标 #${idx} 不存在或已被过滤，清除标记`);
      }
    }
  });

  // ── 元数据加载 / 保存（per-file 模式）──
  createEffect(() => {
    const pid = appState.activeProjectId;
    const type = metaType();
    const srcFile = metaSourceFile();
    const myToken = ++metaSwitchToken; // 任何触发都使旧切换闭包失效
    if (reviewMode() !== "metadata" || !pid) {
      // 离开 metadata：未保存修改的确认已由 runSwitch（translate 目标）在加载目标前处理。
      // 这里不清理 metaEntry/metaDirty（runSwitch 需要读取 metaEntry 来保存），
      // 也不重置 loadedFile：该变量供翻译模式（loadFile/saveCurrentFile/runSwitch）使用，
      // 若在此清空，cacheWatcher bump cacheVersion 时会短路后续保存，导致 dirty 无法清除
      return;
    }
    void appState.cacheVersion; // 仅 metadata 模式追踪：外部改动元数据文件时自动刷新
    void (async () => {
      // 切换判断必须用完整路径而非纯源文件名：pass1/pass2 缓存可能含同名源文件（如 00_01），
      // 纯名相同会误判为"同文件"而跳过加载，导致界面残留旧数据、blur 后旧数据被写入新文件
      if (metaLoadedFullPath && metaLoadedFullPath !== appState.activeFilePath) {
        // 切换元数据文件：有改动先等待落盘，防 fire-and-forget 的丢失窗口
        if (metaDirty && metaEntry()) {
          // 等待在途失焦保存（saveMeta）完成，防并发写同一文件
          const deadline = Date.now() + 3000;
          while (metaSavePending && Date.now() < deadline) {
            await new Promise((r) => setTimeout(r, 30));
          }
          try {
            // 保存旧文件：filename/type 基于旧文件完整路径（metaLoadedFullPath）推导。
            // 不能用 loadedFile（纯源文件名，无目录信息，modeInfoOf 会退回空 sourceFile）
            const prevInfo = modeInfoOf(metaLoadedFullPath || loadedFile);
            // metaEntry() 在外层 if (metaDirty && metaEntry()) 已保证非空
            await savePerFileMetadata(pid, prevInfo.metaType, prevInfo.sourceFile, metaEntry()!);
            if (myToken !== metaSwitchToken) return; // 保存期间又切换
            metaDirty = false;
            clearUndo(); // 保存即新的撤销起点（与 saveMeta 一致），防旧文件残留记录造成撤销错位
          } catch (e) {
            // 保存失败：中止切换，保住未保存编辑（metaDirty 保持 true）
            toast.error(`保存 ${metaLoadedFullPath} 失败：${getErrorMessage(e)}`);
            return;
          }
        }
      } else if (metaDirty) {
        // 同文件外部刷新（cacheVersion）：有未保存编辑时跳过自动刷新，避免覆盖
        return;
      }
      if (myToken !== metaSwitchToken) return; // 过期切换闭包丢弃
      loadedFile = srcFile;
      setMetaLoading(true);
      try {
        const res = await fetchPerFileMetadata(pid, type, srcFile);
        if (myToken !== metaSwitchToken) return; // 过期响应不写 metaEntry
        setMetaEntry(res.entry ?? null);
        metaDirty = false; // 新文件即磁盘态，未编辑
        metaUndoBase = res.entry ?? null; // 重置撤销基线
        metaDiskSnapshot = res.entry ?? null; // 重置磁盘态快照
        metaJsonInvalidShown = false; // 新文件加载后重置非法 JSON 提示标志
        // activeFilePath 至此非空（正在加载目标文件）；?? "" 仅作类型收窄，与声明类型一致
        metaLoadedFullPath = appState.activeFilePath ?? ""; // 记录当前文件完整路径，供下次切换保存推导
      } catch {
        if (myToken !== metaSwitchToken) return;
        setMetaEntry(null);
        metaDirty = false;
        metaUndoBase = null;
        metaDiskSnapshot = null;
      } finally {
        if (myToken === metaSwitchToken) setMetaLoading(false);
      }
    })();
  });

  function handleMetaContentChange(_index: number, text: string) {
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(text);
    } catch {
      // 非法 JSON：首次出现时提示一次，持续非法不重复
      if (!metaJsonInvalidShown) {
        metaJsonInvalidShown = true;
        toast.warning("元数据 JSON 格式错误，该次修改暂未保存");
      }
      return;
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      // 合法 JSON 但非对象（数组/标量）：同样拦截并提示
      if (!metaJsonInvalidShown) {
        metaJsonInvalidShown = true;
        toast.warning("元数据内容需为 JSON 对象，该次修改暂未保存");
      }
      return;
    }
    metaJsonInvalidShown = false; // 恢复合法 → 重置提示标志
    setMetaEntry((prev) => {
      if (!prev) return parsed;
      // 单对象的 GlobalPrompt/PlotRouteMap 不注入空 id（用户手写的 id 仍保留）；per-file 元数据保留只读 id
      if (metaType() === "globalprompt" || metaType() === "plotroute") {
        return { ...parsed };
      }
      const id = prev.id ?? "";
      return { ...parsed, id };
    });
    metaDirty = true; // 有效编辑 → 标记未保存
  }

  async function saveMeta() {
    if (metaSavePending) return;
    metaSavePending = true;
    const pid = appState.activeProjectId;
    const srcFile = metaSourceFile();
    const entry = metaEntry();
    // 守卫：保存目标必须是当前实际打开的文件。
    // metaLoadedFullPath 是完整路径，经 modeInfoOf 提取 metaType/sourceFile 后分别与当前目标比较。
    // 必须同时校验 metaType：pass1/pass2 可能含同名源文件，仅比纯源文件名会漏判，
    // 导致 pass1 数据被 POST 写入 pass2 文件（切换瞬间 metaSourceFile 相同、metaType 不同）
    const loadedInfo = modeInfoOf(metaLoadedFullPath);
    if (!pid || !entry || loadedInfo.sourceFile !== metaSourceFile() || loadedInfo.metaType !== metaType()) {
      if (import.meta.env?.DEV) {
        console.debug(
          `[ReviewPage] 元数据保存被守卫拦截（目标已切换）, loaded=${metaLoadedFullPath}, target=${metaSourceFile()}/${metaType()}`,
        );
      }
      metaSavePending = false;
      return;
    }
    try {
      await savePerFileMetadata(pid, metaType(), srcFile, entry);
      metaDiskSnapshot = entry; // 更新磁盘态快照
      // 撤销基线取界面当前值而非保存值 entry：防保存响应返回时 metaEntry 已被撤销改写（竞态）导致基线错位
      metaUndoBase = metaEntry();
      clearUndo(); // 保存即新的撤销起点：清空保存前历史，撤销最多回到最近保存态
      // 按快照重算 dirty：防保存响应返回时 metaEntry 已被撤销改写（竞态）而错误清脏
      metaDirty = !metaEqual(metaEntry(), metaDiskSnapshot);
      if (import.meta.env?.DEV) {
        console.debug(`[ReviewPage] 元数据已保存并重置撤销栈, file=${srcFile}`);
      }
    } catch (e) {
      toast.error(`元数据保存失败：${getErrorMessage(e)}`);
    } finally {
      metaSavePending = false;
    }
  }

  // 最近一次加载/保存时的可编辑字段基线，用于内容还原后自动恢复 clean
  let baselineKey = "";

  // 换行规范化：CRLF/CR 统一为 LF。textarea 会把 CRLF 规范化为 LF，
  // 若不规范化，同一译文在条目数据（CRLF）与编辑框（LF）间恒被判为"变化"，导致误标脏。
  function normNewlines(s: string): string {
    return s.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  }

  function snapshotKey(list: CacheEntry[]): string {
    // 仅取用户可编辑字段，排除 problem/post_dst_preview 等会因 recheck 变化但无需保存的字段
    return JSON.stringify(
      list.map((e) => ({
        index: e.index,
        pre_dst: normNewlines(e.pre_dst ?? ""),
        proofread_dst: normNewlines(e.proofread_dst ?? ""),
        alt_dst: normNewlines(e.alt_dst ?? ""),
        skip_check: !!e.skip_check,
      })),
    );
  }

  function refreshDirtyState() {
    const file = appState.activeFilePath;
    if (!file) return;
    if (snapshotKey(entries()) === baselineKey) markClean(file);
    else markDirty(file);
  }

  function handleFieldChange(serial: number, field: string, value: string) {
    const pos = entries().findIndex((e) => e.index === serial);
    if (pos === -1) return;
    const current = entries()[pos];
    // 值未变化（按换行规范化比较）：不更新、不入 undo，但仍按基线校正脏状态
    // （输入内容后再删除还原：onInput 已标脏，此处内容与基线一致应恢复 clean）
    if (normNewlines(String(current[field as keyof CacheEntry] ?? "")) === normNewlines(value)) {
      refreshDirtyState();
      return;
    }
    const before = { ...current };
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

    // 内容与基线比对，决定 dirty/clean（值还原为原值时自动恢复 clean）
    refreshDirtyState();
    entriesRev++;
  }

  function handleSwapAlt(serial: number) {
    // 先 blur 提交主译文框草稿（onBlur 同步写入 entries），避免聚焦编辑中的草稿覆盖交换结果
    (document.activeElement as HTMLElement | null)?.blur();
    const pos = entries().findIndex((e) => e.index === serial);
    if (pos === -1) return;
    const current = entries()[pos];
    const pre = current.pre_dst ?? "";
    const alt = current.alt_dst;
    if (!alt) return; // 无备选译文则不提供交换
    setEntries((prev) => {
      const next = [...prev];
      next[pos] = { ...next[pos], pre_dst: alt, alt_dst: pre };
      return next;
    });
    // 交换同时改两字段：before/after 均为双字段对象，undo/redo 整对象合并还原
    pushUndo({
      id: `${appState.activeFilePath}:${serial}`,
      file: appState.activeFilePath ?? "",
      index: serial,
      before: { pre_dst: pre, alt_dst: alt },
      after: { pre_dst: alt, alt_dst: pre },
      description: "交换备选译文",
    });
    refreshDirtyState();
    entriesRev++;
  }

  function handleSkip(serial: number) {
    // 切换 skip_check（布尔值），配合后端 rebuild 中的 find_problems 跳过逻辑
    // 切到跳过时同时清除 problem 标记；取消跳过则不清除，让下次 save 时 rebuild 重新检测
    setEntries((prev) =>
      prev.map((e) => {
        if (e.index !== serial) return e;
        if (e.skip_check) {
          // 取消跳过：删除标记，保留 problem 不变（下次 save 时 rebuild 会重新检测）
          const { skip_check, ...rest } = e;
          return rest;
        }
        // 跳过：标记 + 清除 problem
        return { ...e, skip_check: true, problem: "" };
      }),
    );
    refreshDirtyState();
    entriesRev++;
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
    refreshDirtyState();
    entriesRev++;
  }

  function handleJump() {
    const raw = jumpValue().trim();
    if (!raw) {
      toast.warning("跳转失败：请输入条目序号");
      return;
    }
    const val = parseInt(raw, 10);
    if (isNaN(val) || val < 1) {
      toast.warning("跳转失败：条目序号需为正整数");
      return;
    }
    if (filteredIndexFromSerial(val) < 0) {
      toast.warning(`跳转失败：条目 #${val} 不存在`);
      return;
    }
    // 分页：scrollToSerial 内部自动翻到目标所在页并高亮
    scrollToSerial(val);
    const fileName = (appState.activeFilePath ?? "").split("/").pop() || "";
    toast.success(`跳转到 ${fileName} 第 ${val} 条成功`);
  }

  /** 手动保存：把内存中的最新条目落盘（循环到无并发改动为止，确保最终一定写入），再按 index 合并后端重建的 problem */
  let saveInFlight = false;
  let entriesRev = 0; // 每次本地修改 entries 自增，用于判断落盘期间是否有新改动

  async function saveCurrentFile(): Promise<void> {
    const pid = appState.activeProjectId;
    const myFile = loadedFile; // entries() 当前真正所属的文件，不取 activeFilePath（切文件时可能已变）
    if (!pid || !myFile || appState.activeFilePath !== myFile) return;
    // 已有保存在进行：本次直接返回，由在途保存根据其完成时的 entriesRev 决定是否再存，避免单布尔排队丢失
    if (saveInFlight) return;
    saveInFlight = true;

    try {
      // 循环落盘：每次保存最新 entries；若保存期间又有本地改动（连续删除/编辑），则以最新数据再存一次，
      // 直到“保存瞬间与完成瞬间无新改动”，保证最终落盘的是最新状态
      let revAfterSave = entriesRev;
      // 虚拟滚动截断时缓存后端完整数据，供保存循环内复用（用户编辑期间后端文件未变）
      while (true) {
        const myRev = entriesRev;
        // entries 已失效（loadFile 失败清空 loadedFile）时中止保存，避免写残缺数据
        if (loadedFile !== myFile) return;
        // 分页模式全量加载，直接保存内存中的全部条目
        const toSave = entries();
        const resp = await saveCacheFile(pid, myFile, toSave, getActiveConfigFileName());
        // 检查后端返回：保存未确认成功时不 markClean，避免误报"已保存"
        if (resp && resp.success === false) {
          toast.error(`保存 ${myFile} 失败：后端未确认成功`);
          return;
        }
        if (appState.activeFilePath !== myFile) return;
        revAfterSave = entriesRev;
        if (entriesRev === myRev) break;
      }
      markClean(myFile);
      baselineKey = snapshotKey(entries()); // 保存成功，重置编辑基线
      // 按 index 局部合并后端重建的 problem（不要求长度相等，删除后其余条目也能刷新问题），不整表替换避免闪烁。
      // 合并失败不影响保存结果（保存已成功），仅静默跳过，避免误报"保存失败"。
      try {
        const res = await fetchCacheFile(pid, myFile);
        if (appState.activeFilePath !== myFile) return;
        if (entriesRev !== revAfterSave) return; // 期间又有改动：不覆盖，交给下次保存处理
        setEntries((prev) => {
          const backend = res.entries ?? [];
          const byIndex = new Map(backend.map((e) => [e.index, e]));
          return prev.map((e) => {
            const b = byIndex.get(e.index);
            return b ? { ...e, problem: b.problem } : e;
          });
        });
      } catch {
        // 合并刷新失败：保存已成功，无需报错
      }
    } catch (e) {
      // 保存失败：提示用户（dirty 保持，markClean 未执行）
      toast.error(`保存 ${myFile} 失败：${getErrorMessage(e)}`);
    } finally {
      saveInFlight = false;
    }
  }

  // 保存并重检进行中标志：按钮/Ctrl+S/菜单保存三入口共用，重入时静默忽略（首次执行已含保存+重检全部意图）
  let refreshInFlight = false;

  /** 保存并重检：先自动保存未保存的更改（若有，保存触发后端 rebuild 重检写盘），再强制重新运行问题检测并写盘（persist=true），确保侧栏同步最新结果 */
  async function handleRefresh() {
    if (refreshInFlight) return;
    refreshInFlight = true;
    try {
      (document.activeElement as HTMLElement | null)?.blur(); // 同步主译文框草稿到 entries
      const pid = appState.activeProjectId;
      const myFile = loadedFile; // entries() 当前真正所属的文件，不取 activeFilePath（切文件时可能已变）
      if (!pid || !myFile || appState.activeFilePath !== myFile) return;
      if (appState.dirtyFiles.includes(myFile)) {
        // 等待在途保存完成（saveInFlight 在 saveCurrentFile 的 finally 必定释放），带超时兜底，
        // 避免保存被 saveInFlight 守卫跳过导致磁盘未落盘
        const deadline = Date.now() + 3000;
        while (saveInFlight && Date.now() < deadline) {
          await new Promise((r) => setTimeout(r, 30));
        }
        await saveCurrentFile(); // 自动保存；保存触发后端 rebuild 重检
      }
      if (appState.activeFilePath !== myFile) return;
      await recheckProblems(pid, myFile, true); // 重检并写盘（非 dirty 时也同步侧栏）
    } finally {
      refreshInFlight = false;
    }
  }

  /** 对当前条目强制重新问题检测（POST /cache/check，persist 时写盘），按 index 合并 problem 结果 */
  async function recheckProblems(pid: string, myFile: string, persist = false) {
    try {
      // 分页模式全量加载，直接检测内存中的全部条目
      const full = entries();
      const resp = await checkCacheProblems(pid, myFile, full, getActiveConfigFileName(), persist);
      if (!resp || !resp.success) {
        toast.warning("重新问题检测失败，已保留原检测结果");
        return;
      }
      const byIndex = new Map(resp.results.map((r) => [r.index, r.problem ?? ""]));
      setEntries((prev) =>
        prev.map((e) => (byIndex.has(e.index) ? { ...e, problem: byIndex.get(e.index) ?? "" } : e)),
      );
    } catch {
      toast.warning("重新问题检测失败，已保留原检测结果");
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


        {/* 快捷筛选：只看有问题 / 备选 / 类型 / 说话人 */}
        <div class="review-filter-bar">
          <button
            class={`review-filter-chip ${filterProblemsOnly() ? "review-filter-chip--active" : ""}`}
            onClick={() => setFilterProblemsOnly(!filterProblemsOnly())}
          >
            只看有问题
          </button>
          <button
            class={`review-filter-chip ${filterAltOnly() ? "review-filter-chip--active" : ""}`}
            onClick={() => setFilterAltOnly(!filterAltOnly())}
          >
            只看备选
          </button>
          <select
            class="review-filter-select"
            value={filterProblemType()}
            onChange={(e) => setFilterProblemType(e.currentTarget.value)}
          >
            <option value="all">全部类型</option>
            <For each={problemTypes()}>
              {(t) => <option value={t.name}>{t.name}</option>}
            </For>
          </select>
          <select
            class="review-filter-select"
            value={filterSpeaker()}
            onChange={(e) => setFilterSpeaker(e.currentTarget.value)}
          >
            <option value="all">全部说话人</option>
            <option value="__no_speaker__">旁白独白</option>
            <For each={speakers()}>
              {(s) => <option value={s}>{displaySpeakerName(s, nameDict())}</option>}
            </For>
          </select>
          <Show when={hasFilter()}>
            <span class="review-filter-count">
              {filteredEntries().length}/{entries().length} 条
              {problemCount() > 0 ? ` · 问题 ${problemCount()}` : ""}
            </span>
          </Show>
          <Show when={hasFilter()}>
            <button class="review-filter-clear" onClick={clearFilters}>
              清除筛选
            </button>
          </Show>
        </div>

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
        <Show when={appState.dirtyFiles.includes(appState.activeFilePath ?? "")}>
          <span style="color:var(--color-status-warning);margin-left:8px" title="有未保存的修改">
            ● 未保存
          </span>
        </Show>
        <button
          class="btn btn--sm"
          title="保存当前文件的修改，并重新运行问题检测；检测结果写回缓存并同步到问题检测侧栏"
          onClick={() => void handleRefresh()}
        >
          保存并重检问题
        </button>
        </Show>{/* /translate mode */}

        <Show when={reviewMode() === "metadata"}>
          <span class="review-filename">
            {metaType() === "globalprompt"
              ? "GlobalPrompt"
              : metaType() === "plotroute"
                ? "PlotRouteMap"
                : metaSourceFile()}
          </span>
          <Show when={metaEntry()}>
            <span class="review-count">1 条</span>
          </Show>
        </Show>
      </div>

      {/* ── 条目列表 ── */}
      {/* review-list 为条目列表滚动容器：同时承载元数据与翻译校对两种模式，overflow-y:auto */}
      <div class="review-list">
        {/* 元数据模式：渲染 per-file 元数据 JSON 条目 */}
        <Show when={reviewMode() === "metadata"}>
          <Show when={!metaLoading()} fallback={<p class="review-placeholder">加载中…</p>}>
            <Show
              when={metaEntry() != null}
              fallback={
                <p class="review-placeholder">
                  {appState.activeProjectId ? "该文件没有元数据条目" : "请先打开翻译项目"}
                </p>
              }
            >
              {metaType() === "plotroute" ? (
                <PlotRoutePanel
                  projectId={appState.activeProjectId ?? ""}
                  entry={metaEntry()!}
                  index={0}
                  onContentChange={(t) => handleMetaContentChange(0, t)}
                  onBlur={saveMeta}
                />
              ) : (
                <MetadataCard
                  entry={metaEntry()!}
                  index={0}
                  onContentChange={(t) => handleMetaContentChange(0, t)}
                  onBlur={saveMeta}
                />
              )}
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
                  : filteredEntries().length === 0
                    ? "未找到匹配条目"
                    : "该文件暂无条目"}
              </p>
            }
          >
            {/* 分页信息栏：仅当条目数超过每页条数（需分页）时才显示 */}
            <Show when={totalPages() > 1}>
              <div class="review-pagination">
                共 {filteredEntries().length} 条
                {filteredEntries().length !== totalCount() && <span>（文件共 {totalCount()} 条）</span>}
                ，每页 {pageSize() > 0 ? pageSize() : "全部"} 条，共 {totalPages()} 页
              </div>
            </Show>
            {/* 当前页条目全量渲染（分页模式，<Index> 简单可靠，高度自适应无重叠） */}
            <div class="review-list-full">
              <Index each={currentPageEntries()}>
                {(entrySignal) => (
                  <div data-index={entrySignal().index}>
                    <EntryCard
                      entry={entrySignal()}
                      nameDict={nameDict()}
                      expanded={expandedSerials().has(entrySignal().index)}
                      onToggleExpanded={() => toggleExpanded(entrySignal().index)}
                      onSkip={() => handleSkip(entrySignal().index)}
                      onDelete={() => handleDelete(entrySignal().index)}
                      onSwapAlt={() => handleSwapAlt(entrySignal().index)}
                      onFieldChange={(field, value) => handleFieldChange(entrySignal().index, field, value)}
                    />
                  </div>
                )}
              </Index>
            </div>
            {/* 分页控件：仅当需分页时才显示 */}
            <Show when={totalPages() > 1}>
              <div class="review-pagination">
                <button
                  class="btn btn--sm"
                  onClick={() => goToPage(page() - 1)}
                  disabled={page() <= 0}
                >
                  上一页
                </button>
                <span class="review-pagination-info">
                  第 {page() + 1} / {totalPages()} 页
                </span>
                <button
                  class="btn btn--sm"
                  onClick={() => goToPage(page() + 1)}
                  disabled={page() >= totalPages() - 1}
                >
                  下一页
                </button>
              </div>
            </Show>
          </Show>
        </Show>
        </Show>
      </div>
    </div>
  );
}
