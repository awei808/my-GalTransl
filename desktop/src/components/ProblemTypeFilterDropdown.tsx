import { createEffect, createSignal, For, onCleanup, Show } from "solid-js";
import type { ProblemTypeInfo } from "../lib/api/types";

/**
 * 问题类型多选下拉：可同时勾选多个类型（AND 过滤语义由调用方实现，本组件只维护选中集合）。
 * 校对审核页与问题检测侧栏复用同一套交互与样式（review-filter-*）。
 */
export function ProblemTypeFilterDropdown(props: {
  value: () => string[];
  types: () => ProblemTypeInfo[];
  onChange: (v: string[]) => void;
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
    const selected = props.value();
    if (selected.length === 0) return "全部类型";
    const names = selected
      .map((n) => props.types().find((t) => t.name === n)?.name ?? n)
      .join("+");
    return names.length > 18 ? names.slice(0, 18) + "…" : names;
  };

  const anyActive = () => props.value().length > 0;

  function toggleType(name: string, checked: boolean) {
    const cur = props.value();
    if (checked) {
      if (!cur.includes(name)) props.onChange([...cur, name]);
    } else {
      props.onChange(cur.filter((n) => n !== name));
    }
  }

  return (
    <div ref={rootRef} class="review-filter-dropdown">
      <button
        class={`review-filter-dropdown-trigger ${anyActive() ? "review-filter-dropdown-trigger--active" : ""}`}
        onClick={(e) => {
          e.stopPropagation();
          setOpen(!open());
        }}
      >
        类型: {summary()}
        <span class="review-filter-dropdown-caret">▾</span>
      </button>
      <Show when={open()}>
        <div class="review-filter-dropdown-panel" onClick={(e) => e.stopPropagation()}>
          <For each={props.types()}>
            {(t) => (
              <label class="review-filter-option">
                <input
                  type="checkbox"
                  checked={props.value().includes(t.name)}
                  onChange={(e) => toggleType(t.name, e.currentTarget.checked)}
                />
                <span title={t.description}>{t.name}</span>
              </label>
            )}
          </For>
        </div>
      </Show>
    </div>
  );
}
