/**
 * 视图筛选多选下拉（0.4.10 从 ReviewPage.tsx 抽出）。
 *
 * 只看问题句 / 只看备选 / 只看H场景，可组合（AND 语义）。
 */
import { createSignal, createEffect, onCleanup, Show } from "solid-js";

/* ── 视图筛选多选下拉：只看问题句 / 只看备选 / 只看H场景（可组合 AND）── */
export function ViewFilterDropdown(props: {
  problems: () => boolean;
  alts: () => boolean;
  hOnly: () => boolean;
  onProblems: (v: boolean) => void;
  onAlts: (v: boolean) => void;
  onHOnly: (v: boolean) => void;
}) {
  const [open, setOpen] = createSignal(false);
  let rootRef: HTMLDivElement | undefined;

  // 点击下拉区域外部时收起
  createEffect(() => {
    if (!open()) return;
    const onDoc = (e: MouseEvent) => {
      if (rootRef && !rootRef.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("click", onDoc);
    onCleanup(() => document.removeEventListener("click", onDoc));
  });

  const summary = () => {
    const parts: string[] = [];
    if (props.problems()) parts.push("问题句");
    if (props.alts()) parts.push("备选");
    if (props.hOnly()) parts.push("H场景");
    return parts.length === 0 ? "全部" : parts.join("+");
  };

  const anyActive = () => props.problems() || props.alts() || props.hOnly();

  return (
    <div ref={rootRef} class="review-filter-dropdown">
      <button
        class={`review-filter-dropdown-trigger ${anyActive() ? "review-filter-dropdown-trigger--active" : ""}`}
        onClick={(e) => {
          e.stopPropagation();
          setOpen(!open());
        }}
      >
        视图: {summary()}
        <span class="review-filter-dropdown-caret">▾</span>
      </button>
      <Show when={open()}>
        <div class="review-filter-dropdown-panel" onClick={(e) => e.stopPropagation()}>
          <label class="review-filter-option">
            <input
              type="checkbox"
              checked={props.problems()}
              onChange={(e) => props.onProblems(e.currentTarget.checked)}
            />
            <span>只看问题句</span>
          </label>
          <label class="review-filter-option">
            <input
              type="checkbox"
              checked={props.alts()}
              onChange={(e) => props.onAlts(e.currentTarget.checked)}
            />
            <span>只看备选</span>
          </label>
          <label class="review-filter-option">
            <input
              type="checkbox"
              checked={props.hOnly()}
              onChange={(e) => props.onHOnly(e.currentTarget.checked)}
            />
            <span>只看H场景</span>
          </label>
        </div>
      </Show>
    </div>
  );
}

/* ── ReviewPage 主组件 ── */
