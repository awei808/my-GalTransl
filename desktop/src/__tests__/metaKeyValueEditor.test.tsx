/**
 * MetaKeyValueEditor 键值编辑器行为验证：
 * - 行渲染排除只读 id；
 * - 值宽松解析：散文按字符串、JSON 结构原样保留、类型往返不漂移；
 * - 键为空/重复时标红并阻断对外提交；
 * - 增删行、行内失焦不触发保存、离开编辑器才触发。
 */

import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@solidjs/testing-library";
import { MetaKeyValueEditor, parseMetaValueText, rowsFromEntry } from "../pages/review/MetaKeyValueEditor";

function renderEditor(entry: Record<string, unknown>) {
  const onContentChange = vi.fn();
  const onBlur = vi.fn();
  render(() => (
    <MetaKeyValueEditor entry={entry} onContentChange={onContentChange} onBlur={onBlur} />
  ));
  return { onContentChange, onBlur };
}

function keyInputs(): HTMLInputElement[] {
  return Array.from(document.querySelectorAll(".meta-kv-key"));
}

function valueOfRow(row: HTMLElement): HTMLTextAreaElement {
  return row.querySelector(".meta-kv-value") as HTMLTextAreaElement;
}

function rowByKey(key: string): HTMLElement {
  const row = Array.from(document.querySelectorAll(".meta-kv-row")).find(
    (r) => (r.querySelector(".meta-kv-key") as HTMLInputElement)?.value === key,
  );
  if (!row) throw new Error(`找不到字段行: ${key}`);
  return row as HTMLElement;
}

function editValue(key: string, value: string) {
  const ta = valueOfRow(rowByKey(key));
  fireEvent.input(ta, { target: { value } });
}

describe("parseMetaValueText 宽松解析", () => {
  it("散文 → 字符串；JSON 结构 → 原样保留；空文本 → 空字符串", () => {
    expect(parseMetaValueText("你好\n世界")).toBe("你好\n世界");
    expect(parseMetaValueText("[1,2]")).toEqual([1, 2]);
    expect(parseMetaValueText("42")).toBe(42);
    expect(parseMetaValueText("true")).toBe(true);
    expect(parseMetaValueText("")).toBe("");
  });
});

describe("rowsFromEntry 回程安全", () => {
  it("字符串值「123」保留 JSON 引号源，防止往返变成数字", () => {
    const rows = rowsFromEntry({ code: "123", text: "剧情概要", tags: ["a", "b"] });
    expect(rows.find((r) => r.key === "code")!.valueText).toBe('"123"');
    expect(rows.find((r) => r.key === "text")!.valueText).toBe("剧情概要");
    expect(rows.find((r) => r.key === "tags")!.valueText).toBe(JSON.stringify(["a", "b"], null, 2));
  });
});

describe("MetaKeyValueEditor 交互", () => {
  it("渲染字段行且排除只读 id", () => {
    renderEditor({ id: "filemeta", name: "t01.txt", summary: "概要" });
    expect(keyInputs().map((i) => i.value)).toEqual(["name", "summary"]);
  });

  it("编辑值为散文 → 对外发合法 JSON 字符串值", () => {
    const { onContentChange } = renderEditor({ id: "m", name: "旧名" });
    editValue("name", "新名");
    expect(onContentChange).toHaveBeenLastCalledWith(JSON.stringify({ name: "新名" }, null, 2));
  });

  it("编辑值为 JSON 数组 → 数组结构保留", () => {
    const { onContentChange } = renderEditor({ id: "m", tags: "a" });
    editValue("tags", "[1, 2]");
    expect(onContentChange).toHaveBeenLastCalledWith(JSON.stringify({ tags: [1, 2] }, null, 2));
  });

  it("空键 → 标红且不对外提交；补上键名后恢复提交", () => {
    const { onContentChange } = renderEditor({ name: "旧名" });
    fireEvent.click(document.querySelector(".meta-kv-add") as HTMLButtonElement);
    expect(onContentChange).not.toHaveBeenCalled();
    expect(document.querySelector(".meta-kv-row--error")).not.toBeNull();
    const inputs = keyInputs();
    fireEvent.input(inputs[1], { target: { value: "extra" } });
    expect(onContentChange).toHaveBeenLastCalledWith(
      JSON.stringify({ name: "旧名", extra: "" }, null, 2),
    );
  });

  it("重复键 → 标红且不对外提交", () => {
    const { onContentChange } = renderEditor({ name: "旧名" });
    // 新增行：空键阻断 → 补上与第一行相同的键名：重复阻断，全程无对外提交
    fireEvent.click(document.querySelector(".meta-kv-add") as HTMLButtonElement);
    const inputs = keyInputs();
    fireEvent.input(inputs[1], { target: { value: "name" } });
    expect(onContentChange).not.toHaveBeenCalled();
    expect(document.querySelectorAll(".meta-kv-row--error").length).toBeGreaterThan(0);
  });

  it("删除字段行 → 对外发的 JSON 不含该键", () => {
    const { onContentChange } = renderEditor({ name: "旧名", extra: "x" });
    fireEvent.click(rowByKey("extra").querySelector(".meta-kv-delete") as HTMLButtonElement);
    expect(onContentChange).toHaveBeenLastCalledWith(JSON.stringify({ name: "旧名" }, null, 2));
  });

  it("编辑器内部切换焦点不触发保存，焦点离开编辑器才触发", () => {
    const { onBlur } = renderEditor({ name: "旧名" });
    const keyInput = rowByKey("name").querySelector(".meta-kv-key") as HTMLInputElement;
    const valueTa = valueOfRow(rowByKey("name"));
    // 真实焦点切换：key → value，原生 focusout 的 relatedTarget 在编辑器内 → 不算离开
    keyInput.focus();
    valueTa.focus();
    expect(onBlur).not.toHaveBeenCalled();
    // 焦点移出（relatedTarget 不在编辑器内）→ 触发保存
    fireEvent.focusOut(valueTa, { relatedTarget: document.body });
    expect(onBlur).toHaveBeenCalledTimes(1);
  });

  it("同一元素连续输入多字符时 DOM 复用（Index 就地更新，焦点不丢）", () => {
    renderEditor({ name: "旧名" });
    const ta = valueOfRow(rowByKey("name"));
    ta.focus();
    const before = ta;
    fireEvent.input(ta, { target: { value: "新名" } });
    expect(document.activeElement).toBe(before);
  });
});
