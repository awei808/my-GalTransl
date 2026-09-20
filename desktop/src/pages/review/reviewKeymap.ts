/**
 * 校对审核页的键盘分派与跨文件撤销决策（0.4.10 从 ReviewPage.tsx 抽出）。
 *
 * 全部为纯函数/纯类型，便于单元测试：
 * - 键盘快捷键分派（resolveKeyAction）；
 * - 原生撤销让出判定（shouldYieldToNative / shouldBlurBeforeUndo /
 *   isMetaKvEditorElement）；
 * - 跨文件撤销恢复决策（decideCrossFileRestore + PendingRestore / CrossFileDecision）；
 * - H 区间分割线边界计算（computeHRangeBoundaries）。
 */
import type { CacheEntry, CacheHRange } from "../../lib/api/types";
import type { UndoEntry } from "../../stores/undoStore";

export function isMetaKvEditorElement(el: Element | null): boolean {
  return (
    el !== null &&
    (el.classList.contains("meta-kv-key") || el.classList.contains("meta-kv-value"))
  );
}

/**
 * 判断当前是否应让出原生撤销/重做（草稿态）。
 * 主译文框/展开字段在内容未提交时、元数据框在聚焦时，让出原生实现逐字符撤销；
 * 已提交（失焦）或其它编辑器走自定义操作级撤销。
 *
 * Args:
 *   activeEl: 当前聚焦元素。
 *   entries: 当前文件的翻译条目（用于比对主译文框已提交值）。
 */
export function shouldYieldToNative(activeEl: Element | null, entries: CacheEntry[]): boolean {
  // 元数据编辑器：纯原生撤销，聚焦即让出逐字符撤销（失焦自动保存后不再保留历史）
  if (isMetaKvEditorElement(activeEl)) return true;
  if (!(activeEl instanceof HTMLTextAreaElement)) return false;
  if (!activeEl.classList.contains("entry-dst-input") && !activeEl.classList.contains("field-value--editable")) {
    return false;
  }
  const serial = Number(activeEl.dataset.index);
  if (!Number.isFinite(serial)) return false;
  if (activeEl.classList.contains("entry-dst-input")) {
    const committed = entries.find((e) => e.index === serial)?.pre_dst ?? "";
    return activeEl.value !== committed;
  }
  // 展开字段草稿（pre_dst/proofread_dst/alt_dst）：未提交时让出原生逐字符撤销
  const key = activeEl.dataset.fieldKey;
  if (!key) return false;
  const entry = entries.find((e) => e.index === serial) as Record<string, unknown> | undefined;
  const committed = String(entry?.[key] ?? "");
  return activeEl.value !== committed;
}

/**
 * 撤销/重做/跨文件切换前需要先 blur 提交草稿的输入框判定。
 * 主译文框、展开字段（pre_dst/proofread_dst/alt_dst）均为草稿态，若未先提交就改 entries，
 * 聚焦中的旧草稿会在失焦时回写，覆盖撤销/重做结果。元数据框走原生撤销（方案 A），
 * 不做操作级撤销前的 blur（避免失焦破坏原生历史）。
 */
export function shouldBlurBeforeUndo(el: Element | null): boolean {
  return (
    el instanceof HTMLTextAreaElement &&
    (el.classList.contains("entry-dst-input") || el.classList.contains("field-value--editable"))
  );
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

/**
 * 计算当前页需要画 H 分割线的条目。
 *
 * 对每个 H 区间，仅当页内确有落在 [lo,hi] 内的条目时才产生边界：
 * - 页内第一条区间内条目 → starts 记录（其上方画开始线）
 * - 页内最后一条区间内条目 → ends 记录（其下方画结束线）
 * 区间整体在上一页/下一页（页内无交集）时不画任何线，避免「孤立分割线」误标。
 */
export function computeHRangeBoundaries(
  pageEntries: CacheEntry[],
  ranges: CacheHRange[],
): { starts: Map<number, CacheHRange>; ends: Map<number, CacheHRange> } {
  const starts = new Map<number, CacheHRange>();
  const ends = new Map<number, CacheHRange>();
  for (const r of ranges) {
    const startEntry = pageEntries.find((e) => {
      const idx = Number(e.index);
      return idx >= r.lo && idx <= r.hi;
    });
    if (startEntry) starts.set(Number(startEntry.index), r);
    for (let i = pageEntries.length - 1; i >= 0; i--) {
      const idx = Number(pageEntries[i].index);
      if (idx >= r.lo && idx <= r.hi) {
        ends.set(idx, r);
        break;
      }
    }
  }
  return { starts, ends };
}

/* ── 角色名颜色生成（黄金角度 + 感知补偿）── */

