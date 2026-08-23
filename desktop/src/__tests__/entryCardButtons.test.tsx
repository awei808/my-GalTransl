/**
 * 回归测试：译文输入框有未提交草稿时，点击条目操作按钮必须一次生效。
 *
 * 根因：输入中的译文是 EntryCard 本地草稿（draftDst），点击操作按钮会先触发 textarea
 * 失焦提交草稿（setEntries 更新条目）→ <For> 按对象引用 keyed 重建该条目 DOM → 原按钮
 * 被移除 → mousedown/up 目标不一致导致 click 丢失（表现为"第一次点击只失焦，第二次才
 * 生效"）。
 * 修复：按钮统一走 buttonHandlers——mousedown 时先幂等提交草稿再执行动作（DOM 尚未重建），
 * click 兜底去重（键盘 Enter/Space 只触发 click，正常执行动作）。
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent } from "@solidjs/testing-library";
import { EntryCard } from "../pages/review/ReviewPage";
import { setAppState } from "../stores/appStore";
import type { CacheEntry } from "../lib/api/types";

const baseEntry: CacheEntry = {
  index: 1,
  name: "",
  pre_src: "原文",
  post_src: "原文",
  pre_dst: "旧译文",
};

beforeEach(() => {
  setAppState("activeFilePath", "game/t01.txt.json");
  setAppState("dirtyFiles", []);
});

function renderCard() {
  const onSkip = vi.fn();
  const onDelete = vi.fn();
  const onSwapAlt = vi.fn();
  const onToggleExpanded = vi.fn();
  const onFieldChange = vi.fn();
  render(() => (
    <EntryCard
      entry={baseEntry}
      nameDict={{}}
      expanded={false}
      onSkip={onSkip}
      onDelete={onDelete}
      onSwapAlt={onSwapAlt}
      onToggleExpanded={onToggleExpanded}
      onFieldChange={onFieldChange}
    />
  ));
  return { onSkip, onDelete, onSwapAlt, onToggleExpanded, onFieldChange };
}

function skipButton(): HTMLButtonElement {
  const btn = document.querySelector('button[title*="跳过该条目"]');
  if (!(btn instanceof HTMLButtonElement)) throw new Error("跳过检查按钮未找到");
  return btn;
}

describe("EntryCard 操作按钮：草稿未提交时点击必须一次生效", () => {
  it("输入草稿后鼠标点击「跳过检查」：动作一次执行且草稿已提交", () => {
    const { onSkip, onFieldChange } = renderCard();
    const ta = document.querySelector(".entry-dst-input") as HTMLTextAreaElement;
    ta.focus();
    fireEvent.input(ta, { target: { value: "新译文" } });
    const btn = skipButton();
    // 模拟真实鼠标序列：mousedown（提交草稿+执行动作）→ click（去重跳过）
    fireEvent.mouseDown(btn);
    fireEvent.click(btn);
    expect(onSkip).toHaveBeenCalledTimes(1);
    expect(onFieldChange).toHaveBeenCalledWith("pre_dst", "新译文");
  });

  it("无草稿时鼠标点击：动作仅执行一次（click 去重不重复）", () => {
    const { onSkip } = renderCard();
    const btn = skipButton();
    fireEvent.mouseDown(btn);
    fireEvent.click(btn);
    expect(onSkip).toHaveBeenCalledTimes(1);
  });

  it("键盘激活（仅 click，无 mousedown）：动作正常执行", () => {
    const { onSkip } = renderCard();
    fireEvent.click(skipButton());
    expect(onSkip).toHaveBeenCalledTimes(1);
  });

  it("输入草稿后鼠标点击「删除」：动作一次执行且草稿已提交", () => {
    const { onDelete, onFieldChange } = renderCard();
    const ta = document.querySelector(".entry-dst-input") as HTMLTextAreaElement;
    ta.focus();
    fireEvent.input(ta, { target: { value: "待删除" } });
    const btn = document.querySelector('button[title="删除该条目"]');
    if (!(btn instanceof HTMLButtonElement)) throw new Error("删除按钮未找到");
    fireEvent.mouseDown(btn);
    fireEvent.click(btn);
    expect(onDelete).toHaveBeenCalledTimes(1);
    expect(onFieldChange).toHaveBeenCalledWith("pre_dst", "待删除");
  });

  it("输入草稿后鼠标点击「展开」：动作一次执行", () => {
    const { onToggleExpanded } = renderCard();
    const ta = document.querySelector(".entry-dst-input") as HTMLTextAreaElement;
    ta.focus();
    fireEvent.input(ta, { target: { value: "展开前草稿" } });
    const btn = document.querySelector('button[title="展开/收起全部字段"]');
    if (!(btn instanceof HTMLButtonElement)) throw new Error("展开按钮未找到");
    fireEvent.mouseDown(btn);
    fireEvent.click(btn);
    expect(onToggleExpanded).toHaveBeenCalledTimes(1);
  });
});

describe("EntryCard 展开字段：草稿提交语义（备选译文可编辑）", () => {
  function renderExpanded(entry?: Partial<CacheEntry>) {
    const onSkip = vi.fn();
    const onDelete = vi.fn();
    const onSwapAlt = vi.fn();
    const onToggleExpanded = vi.fn();
    const onFieldChange = vi.fn();
    render(() => (
      <EntryCard
        entry={{ ...baseEntry, alt_dst: "旧备选", ...entry }}
        nameDict={{}}
        expanded={true}
        onSkip={onSkip}
        onDelete={onDelete}
        onSwapAlt={onSwapAlt}
        onToggleExpanded={onToggleExpanded}
        onFieldChange={onFieldChange}
      />
    ));
    return { onSkip, onDelete, onSwapAlt, onToggleExpanded, onFieldChange };
  }

  function altTextarea(): HTMLTextAreaElement {
    const ta = document.querySelector('textarea[data-field-key="alt_dst"]');
    if (!(ta instanceof HTMLTextAreaElement)) throw new Error("备选译文输入框未找到");
    return ta;
  }

  it("备选译文渲染为可编辑输入框，初始值为条目 alt_dst", () => {
    renderExpanded();
    expect(altTextarea().value).toBe("旧备选");
  });

  it("键入只更新本地草稿，不立即触发 onFieldChange", () => {
    const { onFieldChange } = renderExpanded();
    const ta = altTextarea();
    ta.focus();
    fireEvent.input(ta, { target: { value: "新备选" } });
    expect(onFieldChange).not.toHaveBeenCalled();
  });

  it("失焦时只提交被编辑过的字段草稿", () => {
    const { onFieldChange } = renderExpanded();
    const ta = altTextarea();
    ta.focus();
    fireEvent.input(ta, { target: { value: "新备选" } });
    fireEvent.blur(ta);
    // 只提交 alt_dst（pre_dst/proofread_dst 未编辑，不产生多余提交）
    expect(onFieldChange).toHaveBeenCalledTimes(1);
    expect(onFieldChange).toHaveBeenCalledWith("alt_dst", "新备选");
  });

  it("未编辑字段失焦：不触发任何提交", () => {
    const { onFieldChange } = renderExpanded();
    const ta = altTextarea();
    ta.focus();
    fireEvent.blur(ta);
    expect(onFieldChange).not.toHaveBeenCalled();
  });

  it("收起（点击展开/收起按钮）时先提交草稿再触发 onToggleExpanded", () => {
    const { onToggleExpanded, onFieldChange } = renderExpanded();
    const ta = altTextarea();
    ta.focus();
    fireEvent.input(ta, { target: { value: "收起前草稿" } });
    const btn = document.querySelector('button[title="展开/收起全部字段"]');
    if (!(btn instanceof HTMLButtonElement)) throw new Error("展开按钮未找到");
    fireEvent.mouseDown(btn);
    fireEvent.click(btn);
    expect(onFieldChange).toHaveBeenCalledWith("alt_dst", "收起前草稿");
    expect(onToggleExpanded).toHaveBeenCalledTimes(1);
  });

  it("主译文框有草稿时收起：展开字段旧草稿不覆盖主框新值", () => {
    const { onToggleExpanded, onFieldChange } = renderExpanded();
    const dst = document.querySelector(".entry-dst-input") as HTMLTextAreaElement;
    dst.focus();
    fireEvent.input(dst, { target: { value: "主框新译文" } });
    const btn = document.querySelector('button[title="展开/收起全部字段"]');
    if (!(btn instanceof HTMLButtonElement)) throw new Error("展开按钮未找到");
    fireEvent.mouseDown(btn);
    fireEvent.click(btn);
    expect(onFieldChange).toHaveBeenCalledWith("pre_dst", "主框新译文");
    expect(onFieldChange).not.toHaveBeenCalledWith("pre_dst", "旧译文");
    expect(onToggleExpanded).toHaveBeenCalledTimes(1);
  });

  it("空字符串备选（空备选）：仍渲染可编辑输入框且值为空", () => {
    renderExpanded({ alt_dst: "" });
    expect(altTextarea().value).toBe("");
  });

  it("只读且字段缺失：展开不显示该字段", () => {
    renderExpanded(); // baseEntry 缺 trans_by/problem 等只读字段
    const labels = [...document.querySelectorAll(".entry-expanded .field-label")].map((e) => e.textContent.trim());
    expect(labels).not.toContain("翻译引擎"); // trans_by 缺失
    expect(labels).not.toContain("问题"); // problem 缺失
    expect(labels).not.toContain("校对者"); // proofread_by 缺失
    expect(labels).toContain("译前原文"); // pre_src 存在
    expect(labels).toContain("索引"); // index 存在
  });

  it("只读且字段存在：展开正常显示", () => {
    renderExpanded({ trans_by: "test-model" });
    const labels = [...document.querySelectorAll(".entry-expanded .field-label")].map((e) => e.textContent.trim());
    expect(labels).toContain("翻译引擎");
  });

  it("可读写字段缺失/为空：一律显示可编辑输入框", () => {
    renderExpanded({ alt_dst: undefined, proofread_dst: undefined });
    const alt = altTextarea();
    expect(alt).toBeTruthy();
    expect(alt.value).toBe("");
    const proof = document.querySelector('textarea[data-field-key="proofread_dst"]') as HTMLTextAreaElement | null;
    expect(proof).toBeTruthy();
    expect(proof?.value ?? "").toBe("");
  });
});
