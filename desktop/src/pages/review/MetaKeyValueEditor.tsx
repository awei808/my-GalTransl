/**
 * 元数据键值编辑器：把整块 JSON 文本改成「每字段一行」的键值对应式编辑。
 *
 * 行为口径：
 * - 键为单行输入框（可改可增删，id 由卡片头只读展示，不进编辑器）；
 * - 值为 JSON 源文本编辑：先按 JSON 解析（数组/对象/数字/布尔/null 原样保留），
 *   解析失败则整体按普通字符串处理——元数据值多为散文，宽松规则免去手写引号。
 *   回程安全：若字符串值本身会被解析成非字符串 JSON（如 "123"），序列化时保留引号，
 *   防止未编辑的字段在保存往返中静默改变类型；
 * - 键为空或重复的行标红并阻断对外提交（不触发 onContentChange）；
 * - 每次合法变更对外发 JSON.stringify(obj, null, 2)，复用父级 handleMetaContentChange
 *   的解析与 dirty 标记链路，父级契约不变；
 * - 撤销沿用「方案 A」：聚焦中的行走浏览器原生逐字符撤销；外部 entry 变更仅在
 *   编辑器未聚焦时同步进来（聚焦时重置会清掉原生撤销栈）。
 */

import { createEffect, createSignal, Index, Show } from "solid-js";

export interface MetaKvRow {
  key: string;
  valueText: string;
}

/** 值解析：合法 JSON 原样保留，否则按普通字符串（空文本按空字符串） */
export function parseMetaValueText(text: string): unknown {
  const trimmed = text.trim();
  if (trimmed === "") return "";
  try {
    return JSON.parse(trimmed);
  } catch {
    return text;
  }
}

/** 值的类型徽标文案（提示解析成了什么，防「字符串被当成数字」的意外） */
export function metaValueTypeLabel(value: unknown): string {
  if (value === null) return "null";
  if (Array.isArray(value)) return "数组";
  const t = typeof value;
  if (t === "string") return "字符串";
  if (t === "number") return "数字";
  if (t === "boolean") return "布尔";
  if (t === "object") return "对象";
  return t;
}

export function rowsFromEntry(entry: Record<string, unknown>): MetaKvRow[] {
  return Object.entries(entry)
    .filter(([k]) => k !== "id")
    .map(([key, value]) => {
      let valueText: string;
      if (typeof value === "string") {
        valueText = value;
        // 字符串值若会被宽松解析成非字符串（数字/布尔/合法 JSON 结构），保留 JSON 引号源
        try {
          if (typeof JSON.parse(value) !== "string") valueText = JSON.stringify(value);
        } catch {
          /* 普通散文，原文展示 */
        }
      } else {
        try {
          valueText = JSON.stringify(value, null, 2);
        } catch {
          valueText = String(value);
        }
      }
      return { key, valueText };
    });
}

export function rowsToObject(rows: MetaKvRow[]): Record<string, unknown> | null {
  const obj: Record<string, unknown> = {};
  for (const row of rows) {
    if (row.key === "" || row.key in obj) return null; // 空/重复键：整体无效
    obj[row.key] = parseMetaValueText(row.valueText);
  }
  return obj;
}

export function serializeRows(rows: MetaKvRow[]): string | null {
  const obj = rowsToObject(rows);
  if (obj === null) return null;
  try {
    return JSON.stringify(obj, null, 2);
  } catch {
    return null;
  }
}

export function MetaKeyValueEditor(props: {
  entry: Record<string, unknown>;
  onContentChange: (text: string) => void;
  onBlur: () => void;
}) {
  let rootRef: HTMLDivElement | undefined;
  const [rows, setRows] = createSignal<MetaKvRow[]>(rowsFromEntry(props.entry));

  // 外部 entry 变更（保存后 store 更新等）且编辑器未聚焦时，重置行数据。
  // 聚焦中绝不重置：程序化重建行会销毁聚焦中的原生撤销栈（方案 A 约定）。
  createEffect(() => {
    void props.entry;
    const active = document.activeElement;
    if (rootRef && active instanceof Node && rootRef.contains(active)) return;
    setRows(rowsFromEntry(props.entry));
  });

  const keyErrors = () => {
    const errors: Record<number, string> = {};
    const seen = new Set<string>();
    rows().forEach((row, i) => {
      if (row.key === "") errors[i] = "键名不能为空";
      else if (seen.has(row.key)) errors[i] = "键名重复";
      else seen.add(row.key);
    });
    return errors;
  };

  const emit = (next: MetaKvRow[]) => {
    setRows(next);
    const text = serializeRows(next);
    if (text !== null) props.onContentChange(text);
  };

  // focusout 会冒泡：编辑器内部切换焦点（键 → 值）不触发保存，真正离开才触发
  const handleFocusOut = (e: FocusEvent) => {
    const next = e.relatedTarget;
    if (next instanceof Node && rootRef && rootRef.contains(next)) return;
    props.onBlur();
  };

  const valueLineCount = (text: string) => Math.min(12, Math.max(1, text.split("\n").length));

  return (
    <div class="meta-kv-editor" ref={rootRef} onFocusOut={handleFocusOut}>
      {/* 用 Index 而非 For：每次键入都会替换行对象，For 按引用键控会销毁重建该行 DOM
          导致焦点丢失；Index 按位置就地更新，输入元素全程复用 */}
      <Index each={rows()}>
        {(row, i) => {
          // Index 回调：row 为访问器（row()），i 为普通数字
          const err = () => keyErrors()[i];
          const parsed = () => parseMetaValueText(row().valueText);
          return (
            <div class={`meta-kv-row ${err() ? "meta-kv-row--error" : ""}`}>
              <input
                class="meta-kv-key"
                value={row().key}
                placeholder="字段名"
                spellcheck={false}
                onInput={(e) => {
                  const next = [...rows()];
                  next[i] = { ...next[i], key: e.currentTarget.value };
                  emit(next);
                }}
              />
              <div class="meta-kv-value-wrap">
                <textarea
                  class="meta-kv-value"
                  rows={valueLineCount(row().valueText)}
                  value={row().valueText}
                  spellcheck={false}
                  onInput={(e) => {
                    const next = [...rows()];
                    next[i] = { ...next[i], valueText: e.currentTarget.value };
                    emit(next);
                  }}
                />
                <span class="meta-kv-type-hint">{metaValueTypeLabel(parsed())}</span>
                <Show when={err()}>
                  <span class="meta-kv-error-text">{err()}</span>
                </Show>
              </div>
              <button
                type="button"
                class="meta-kv-delete"
                title="删除该字段"
                onClick={() => emit(rows().filter((_, j) => j !== i))}
              >
                ✕
              </button>
            </div>
          );
        }}
      </Index>
      <button
        type="button"
        class="meta-kv-add"
        onClick={() => emit([...rows(), { key: "", valueText: "" }])}
      >
        + 新增字段
      </button>
    </div>
  );
}
