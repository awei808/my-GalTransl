/**
 * 单条 CacheEntry 卡片（0.4.10 从 ReviewPage.tsx 抽出）。
 *
 * 译文条目的完整交互：主译文草稿（失焦提交）、展开字段编辑、
 * 跳过检查、删除、AI 建议面板、问题标记、角色名彩色标签。
 */
import { createSignal, createEffect, createMemo, Show } from "solid-js";
import type { CacheEntry } from "../../lib/api/types";
import { appState, markDirty } from "../../stores/appStore";
import { themeDark } from "../../lib/theme";
import { getNameColor, displaySpeakerName } from "./reviewColor";
import { toVisibleNewlines, ALL_FIELDS, EDITABLE_FIELD_KEYS } from "./reviewUtils";

/* ── 单条 CacheEntry 组件 ── */
export function EntryCard(props: {
  entry: CacheEntry;
  onSkip: () => void;
  onDelete: () => void;
  onFieldChange: (field: string, value: string) => void;
  onSwapAlt: () => void;
  // 展开状态受控（父级持有，条目翻页卸载重建后仍能恢复）
  expanded: boolean;
  onToggleExpanded: () => void;
  // 可选：主译文框聚焦状态上报（虚拟滚动"钉住"编辑项用；分页模式不传）
  onFocusChange?: (focused: boolean) => void;
  // 角色名替换表（仅显示层使用，不写入缓存）
  nameDict: Record<string, string>;
  // AI 建议面板（仅当前条目非 null）：父级发起请求并持有状态
  suggestPanel?: {
    loading: boolean;
    text: string;
    error: string;
    model: string;
  } | null;
  onRequestSuggest?: (currentDst: string) => void;
  onSuggestAccept?: () => void;
  onSuggestRegenerate?: (instruction: string) => void;
  onSuggestClose?: () => void;
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

  // 展开字段草稿（pre_dst / proofread_dst / alt_dst）：键入只更新本地草稿，失焦或收起时统一提交。
  // 与主译文框同语义：避免每次按键直写 entries 造成 undo 噪音与脏状态抖动。
  const [expandedDrafts, setExpandedDrafts] = createSignal<Record<string, string>>({});
  // 用户实际编辑过的展开字段集合：提交时只回写被编辑的字段，
  // 避免把展开时初始化的"旧草稿"覆盖主译文框等其他入口刚提交的新值
  const [touchedExpandedKeys, setTouchedExpandedKeys] = createSignal<ReadonlySet<string>>(new Set());
  const draftOf = (key: string): string => {
    const d = expandedDrafts()[key];
    return d !== undefined ? d : String(e()[key as keyof CacheEntry] ?? "");
  };
  // 条目内容变化（提交/refetch）时回填草稿；聚焦中的字段保留输入内容，避免打断编辑
  createEffect(() => {
    const ent = e();
    const active = document.activeElement as HTMLTextAreaElement | null;
    const focusedKey =
      active && active.classList.contains("field-value--editable") && active.dataset.index === String(ent.index)
        ? (active.dataset.fieldKey ?? null)
        : null;
    setExpandedDrafts((prev) => {
      let changed = false;
      const next: Record<string, string> = {};
      for (const key of EDITABLE_FIELD_KEYS) {
        const v =
          focusedKey === key && prev[key] !== undefined
            ? prev[key]
            : String(ent[key as keyof CacheEntry] ?? "");
        if (prev[key] !== v) changed = true;
        next[key] = v;
      }
      return changed ? next : prev;
    });
  });
  // 把展开字段草稿批量提交到 entries（幂等：值未变化的字段由父级 handleFieldChange 跳过）
  const commitExpandedDrafts = (): void => {
    for (const key of touchedExpandedKeys()) {
      props.onFieldChange(key, draftOf(key));
    }
  };

  // 操作按钮统一入口：mousedown 时先提交译文草稿再执行动作。
  // 背景：输入中的译文是本地草稿（draftDst），点击按钮会先触发 textarea 失焦提交草稿
  // （setEntries 更新条目）→ <For> 按对象引用 keyed 重建该条目 DOM → 原按钮被移除 →
  // mousedown/up 目标不一致导致 click 丢失（表现为"第一次点击只失焦，第二次才生效"）。
  // 鼠标路径在 mousedown（DOM 尚未重建）执行并置位去重标记；键盘路径（Enter/Space）
  // 只触发 click，此时 mouseHandled 为空，正常执行动作。
  let mouseHandled = false;
  function buttonHandlers(action: () => void) {
    return {
      onMouseDown: (ev: MouseEvent) => {
        ev.preventDefault(); // 阻止默认焦点转移，避免 textarea 二次 blur 提交
        mouseHandled = true;
        // 幂等提交草稿：值未变化时 handleFieldChange 跳过 setEntries，不触发重建
        props.onFieldChange("pre_dst", draftDst());
        commitExpandedDrafts(); // 展开字段草稿一并提交，避免操作时丢失未失焦的编辑
        action();
      },
      onClick: () => {
        if (mouseHandled) {
          mouseHandled = false;
          return;
        }
        action();
      },
    };
  }

  // AI 建议面板的补充要求草稿（重新生成时传给父级）
  const [suggestInstruction, setSuggestInstruction] = createSignal("");
  // 双击条目空白处发起 AI 建议：文本区/输入框/按钮/原文区不算空白（双击常用于选词）
  function handleCardDblClick(ev: MouseEvent) {
    const el = ev.target as HTMLElement;
    if (el.closest("textarea, input, button, select, a, .entry-src, .entry-problem-text")) return;
    props.onRequestSuggest?.(draftDst());
  }

  return (
    <div
      class={`entry-card ${hasProblem() ? "has-problem" : ""} ${e().skip_check ? "skip-check" : ""}`}
      onDblClick={handleCardDblClick}
    >
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
              {...buttonHandlers(props.onSwapAlt)}
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
            {...buttonHandlers(props.onToggleExpanded)}
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
            {...buttonHandlers(props.onSkip)}
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
          <button class="entry-btn entry-btn--danger" title="删除该条目" {...buttonHandlers(props.onDelete)}>
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

        {/* AI 建议面板：双击空白处发起，采纳后写入 alt_dst 走既有交换/撤销链路 */}
        <Show when={props.suggestPanel}>
          <div class="suggest-panel">
            <div class="suggest-panel-head">
              <span class="suggest-panel-title">AI 建议译文</span>
              <Show when={props.suggestPanel!.model}>
                <span class="suggest-panel-model" title="生成所用模型">{props.suggestPanel!.model}</span>
              </Show>
              <button
                type="button"
                class="suggest-panel-close"
                title="关闭建议面板"
                onClick={() => props.onSuggestClose?.()}
              >
                ✕
              </button>
            </div>
            <Show
              when={!props.suggestPanel!.loading}
              fallback={<div class="suggest-panel-body suggest-panel-body--loading">生成中…</div>}
            >
              <Show
                when={!props.suggestPanel!.error}
                fallback={<div class="suggest-panel-body suggest-panel-body--error">{props.suggestPanel!.error}</div>}
              >
                <div class="suggest-panel-body">{props.suggestPanel!.text || "（空建议）"}</div>
              </Show>
            </Show>
            <div class="suggest-panel-foot">
              <input
                class="suggest-panel-instruction"
                placeholder="补充要求（可选，重新生成时生效）"
                value={suggestInstruction()}
                onInput={(e) => setSuggestInstruction(e.currentTarget.value)}
              />
              <button
                type="button"
                class="entry-btn"
                disabled={props.suggestPanel!.loading}
                onClick={() => props.onSuggestRegenerate?.(suggestInstruction())}
              >
                <span class="entry-btn-text">重新生成</span>
              </button>
              <button
                type="button"
                class="entry-btn entry-btn--suggest-accept"
                disabled={props.suggestPanel!.loading || !props.suggestPanel!.text}
                onClick={() => props.onSuggestAccept?.()}
              >
                <span class="entry-btn-text">采纳为备选</span>
              </button>
            </div>
          </div>
        </Show>
      </div>

      {/* ── 展开全部字段 ── */}
      <Show when={props.expanded}>
        <div class="entry-expanded">
          {ALL_FIELDS.map((field) => {
            const val = e()[field.key];
            const isEditable = EDITABLE_FIELD_KEYS.has(field.key);
            // 只读且字段缺失（JSON 中不存在）时不显示；可读写字段无论空/非空/不存在一律显示
            if (!isEditable && val == null) return null;
            return (
              <div class="entry-field">
                <span class="field-label">{field.label}</span>
                <Show
                  when={isEditable}
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
                    data-index={e().index}
                    value={draftOf(field.key)}
                    onInput={(ev) => {
                      setExpandedDrafts((prev) => ({ ...prev, [field.key]: ev.currentTarget.value }));
                      setTouchedExpandedKeys((prev) => new Set(prev).add(field.key));
                    }}
                    onBlur={commitExpandedDrafts}
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
