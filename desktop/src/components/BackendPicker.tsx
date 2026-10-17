/**
 * BackendPicker — 翻译控制台的后端选择器（单选）。
 *
 * 交互：点击/回车展开 → 搜索框即时过滤 → 点击或 ↑↓ + 回车选中 → Esc 或点击外部收起。
 * 展示元数据（中文短名 / 短说明 / 分组 / 组内顺序）与过滤逻辑在 lib/engineDisplay，
 * 本组件只负责交互；选项值始终是引擎名（后端 `/api/translators` 的 name），
 * 不受中文短名影响，保证提交任务与模型检测的口径不变。
 *
 * 样式：styles/part02.css 的 .backend-* 与 .backend-group（自定义背景主题的实底
 * 覆盖在 styles/custom-background.css）。
 */
import { createEffect, createMemo, createSignal, For, onCleanup, Show } from "solid-js";
import type { TranslatorOption } from "../lib/api/types";
import {
  engineHint,
  engineShortName,
  engineTitle,
  filterEngines,
  flattenGroups,
  groupEngines,
} from "../lib/engineDisplay";
import { Icon } from "./icons";

export function BackendPicker(props: {
  translators: TranslatorOption[];
  /** 清单拉取失败（区别于「尚未加载完」）：仅在确认失败时展示禁用态 */
  loadFailed?: boolean;
  value: string;
  onSelect: (name: string) => void;
}) {
  const [open, setOpen] = createSignal(false);
  const [query, setQuery] = createSignal("");
  const [highlight, setHighlight] = createSignal(0);
  let rootRef: HTMLDivElement | undefined;
  let listRef: HTMLDivElement | undefined;
  let triggerRef: HTMLDivElement | undefined;
  let searchRef: HTMLInputElement | undefined;

  const groups = createMemo(() => groupEngines(filterEngines(props.translators, query())));
  const flat = createMemo(() => flattenGroups(groups()));
  // 高亮下标对列表长度收敛：列表变短（过滤/清单刷新）时不会越界到空选中
  const hl = () => Math.min(highlight(), Math.max(0, flat().length - 1));
  // 渲染用分组：把摊平下标挂到条目上，键盘高亮与鼠标悬停共用同一套下标
  const renderGroups = createMemo(() =>
    groups().map((g) => ({
      label: g.label,
      items: g.items.map((t) => ({ t, flatIndex: flat().indexOf(t) })),
    }))
  );

  /** 清单拉取失败（后端未运行或 /api/translators 失败）：触发框给出可见状态而非静默无反应 */
  const listMissing = () => !!props.loadFailed && props.translators.length === 0;
  /** 尚未拿到清单（首帧/切换项目时）：短暂状态，不按失败态渲染，避免闪一下禁用样式 */
  const listLoading = () => !props.loadFailed && props.translators.length === 0;

  const currentHint = () =>
    props.value
      ? engineTitle(
          props.value,
          props.translators.find((t) => t.name === props.value)?.description ?? ""
        )
      : "";

  const triggerTitle = () =>
    listMissing()
      ? "后端清单未加载（后端未运行或接口调用失败），暂无法选择"
      : listLoading()
        ? "正在加载后端清单…"
        : currentHint();

  const activeDescendant = () => {
    const name = flat()[hl()]?.name;
    return name ? `backend-option-${name}` : undefined;
  };

  function openPanel() {
    if (props.translators.length === 0) return;
    setQuery("");
    // 打开时定位到当前选中项（-1 表示列表里没有，退回首项）
    setHighlight(Math.max(0, flat().findIndex((t) => t.name === props.value)));
    setOpen(true);
    queueMicrotask(() => searchRef?.focus());
  }

  function closePanel() {
    setOpen(false);
  }

  function select(name: string) {
    props.onSelect(name);
    closePanel();
    triggerRef?.focus();
  }

  function onSearchKeyDown(e: KeyboardEvent) {
    const len = flat().length;
    // 以收敛后的 hl() 为基准加减，避免列表变短后从陈旧下标跳位
    const cur = hl();
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (len > 0) setHighlight((cur + 1) % len);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (len > 0) setHighlight((cur - 1 + len) % len);
    } else if (e.key === "Enter") {
      e.preventDefault();
      const target = flat()[hl()];
      if (target) select(target.name);
    } else if (e.key === "Escape") {
      e.preventDefault();
      closePanel();
      triggerRef?.focus();
    }
  }

  // 展开期间：点击外部或按 Esc 收起；Esc 把焦点交回触发框
  createEffect(() => {
    if (!open()) return;
    const onDocClick = (e: MouseEvent) => {
      if (rootRef && !rootRef.contains(e.target as Node)) closePanel();
    };
    const onDocKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      closePanel();
      triggerRef?.focus();
    };
    document.addEventListener("click", onDocClick);
    document.addEventListener("keydown", onDocKey);
    onCleanup(() => {
      document.removeEventListener("click", onDocClick);
      document.removeEventListener("keydown", onDocKey);
    });
  });

  // 键盘移动或过滤后把高亮项滚进视野（不含 Home/End：Home/End 归搜索框的光标语义）
  createEffect(() => {
    const idx = hl();
    if (!open()) return;
    const el = listRef?.querySelectorAll(".backend-option")[idx];
    el?.scrollIntoView({ block: "nearest" });
  });

  return (
    <div class="backend-select-wrapper" ref={rootRef}>
      <div
        ref={triggerRef}
        class={`backend-select-trigger ${listMissing() ? "backend-select-trigger--disabled" : ""}`}
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open()}
        aria-controls="backend-select-list"
        aria-disabled={listMissing() ? "true" : undefined}
        aria-label="选择翻译后端"
        tabindex="0"
        title={triggerTitle()}
        onClick={() => (open() ? closePanel() : openPanel())}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " " || e.key === "ArrowDown") {
            e.preventDefault();
            openPanel();
          } else if (e.key === "Escape") {
            closePanel();
          }
        }}
      >
        <span class="backend-select-trigger__label">
          {listMissing()
            ? "后端清单未加载"
            : listLoading()
              ? "加载中…"
              : props.value
                ? engineShortName(props.value)
                : "选择后端"}
        </span>
        {/* 清单不可用时不显示引擎名，避免「未加载」与旧选中值并存的误读 */}
        <Show when={props.value && !listMissing() && !listLoading()}>
          <span class="backend-select-trigger__engine">{props.value}</span>
        </Show>
        <Icon name="chevron-down" size={12} strokeWidth={2} />
      </div>
      <Show when={open()}>
        <div class="backend-dropdown">
          <div class="backend-search-row">
            <input
              ref={searchRef}
              class="backend-search"
              type="text"
              placeholder="搜索后端名或用途"
              aria-label="搜索翻译后端"
              aria-activedescendant={activeDescendant()}
              value={query()}
              onInput={(e) => {
                setQuery(e.currentTarget.value);
                setHighlight(0);
              }}
              onKeyDown={onSearchKeyDown}
            />
          </div>
          <div
            class="backend-list"
            id="backend-select-list"
            role="listbox"
            aria-label="翻译后端"
            ref={listRef}
          >
            <For each={renderGroups()}>
              {(g) => (
                <>
                  {/* 分组头只作视觉分隔，不进 listbox 的可选项集合 */}
                  <div class="backend-group" role="presentation">
                    {g.label}
                  </div>
                  <For each={g.items}>
                    {(it) => (
                      <div
                        id={`backend-option-${it.t.name}`}
                        class={`backend-option ${
                          it.t.name === props.value ? "active" : ""
                        } ${it.flatIndex === hl() ? "backend-option--hl" : ""}`}
                        role="option"
                        aria-selected={it.t.name === props.value}
                        title={engineTitle(it.t.name, it.t.description)}
                        onMouseEnter={() => setHighlight(it.flatIndex)}
                        onClick={() => select(it.t.name)}
                      >
                        <div class="backend-option__head">
                          <span class="backend-option__name">{engineShortName(it.t.name)}</span>
                          <span class="backend-option__engine">{it.t.name}</span>
                          <Show when={it.t.name === props.value}>
                            <span class="backend-option__check" aria-label="当前选中">
                              ✓
                            </span>
                          </Show>
                        </div>
                        <div class="backend-option__hint">
                          {engineHint(it.t.name, it.t.description)}
                        </div>
                      </div>
                    )}
                  </For>
                </>
              )}
            </For>
            <Show when={flat().length === 0}>
              <div class="backend-empty">没有匹配的后端</div>
            </Show>
          </div>
        </div>
      </Show>
    </div>
  );
}
