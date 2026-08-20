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
