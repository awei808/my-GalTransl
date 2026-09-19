/**
 * 校对页 AI 建议（双击空白处 → 面板 → 采纳为备选译文）行为验证。
 *
 * - EntryCard 级：双击空白处触发请求（携带当前译文草稿）、双击文本区不触发、
 *   面板渲染建议、采纳/重新生成/关闭回调与参数；
 * - ReviewPage 级：双击发起请求 → 建议渲染 → 采纳后写入 alt_dst（出现"备选译文"
 *   交换按钮即证明）且面板关闭。
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent } from "@solidjs/testing-library";
import { EntryCard, ReviewPage } from "../pages/review/ReviewPage";
import { setAppState } from "../stores/appStore";
import type { CacheEntry } from "../lib/api/types";

vi.mock("../lib/api/project", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    fetchCacheFile: vi.fn(),
    fetchCacheHranges: vi.fn(),
    saveCacheFile: vi.fn(),
    fetchPerFileMetadata: vi.fn(),
    savePerFileMetadata: vi.fn(),
    checkCacheProblems: vi.fn(),
    fetchNameDict: vi.fn(),
    requestAiSuggest: vi.fn(),
  };
});

vi.mock("../lib/api/general", () => ({ fetchProblemTypes: vi.fn() }));

import { requestAiSuggest } from "../lib/api/project";
import { fetchProblemTypes } from "../lib/api/general";

const baseEntry: CacheEntry = {
  index: 7,
  name: "創",
  pre_src: "原文",
  post_src: "原文",
  pre_dst: "旧译文",
};

beforeEach(() => {
  vi.clearAllMocks();
  setAppState("activeFilePath", "pass3_cache/t01.txt.json");
  setAppState("dirtyFiles", []);
});

function renderCard(overrides: Partial<Parameters<typeof EntryCard>[0]> = {}) {
  const onRequestSuggest = vi.fn();
  const onSuggestAccept = vi.fn();
  const onSuggestRegenerate = vi.fn();
  const onSuggestClose = vi.fn();
  render(() => (
    <EntryCard
      entry={baseEntry}
      nameDict={{}}
      expanded={false}
      onSkip={vi.fn()}
      onDelete={vi.fn()}
      onSwapAlt={vi.fn()}
      onToggleExpanded={vi.fn()}
      onFieldChange={vi.fn()}
      onRequestSuggest={onRequestSuggest}
      onSuggestAccept={onSuggestAccept}
      onSuggestRegenerate={onSuggestRegenerate}
      onSuggestClose={onSuggestClose}
      {...overrides}
    />
  ));
  return { onRequestSuggest, onSuggestAccept, onSuggestRegenerate, onSuggestClose };
}

describe("EntryCard 双击发起 AI 建议", () => {
  it("双击条目空白处 → 触发请求并携带当前译文草稿", () => {
    const { onRequestSuggest } = renderCard();
    const card = document.querySelector(".entry-card") as HTMLElement;
    fireEvent.doubleClick(card);
    expect(onRequestSuggest).toHaveBeenCalledWith("旧译文");
  });

  it("双击译文输入框 → 不触发（非空白处）", () => {
    const { onRequestSuggest } = renderCard();
    const ta = document.querySelector(".entry-dst-input") as HTMLTextAreaElement;
    fireEvent.doubleClick(ta);
    expect(onRequestSuggest).not.toHaveBeenCalled();
  });

  it("双击原文区 → 不触发（常用于选词）", () => {
    const { onRequestSuggest } = renderCard();
    const src = document.querySelector(".entry-src") as HTMLElement;
    fireEvent.doubleClick(src);
    expect(onRequestSuggest).not.toHaveBeenCalled();
  });
});

describe("EntryCard AI 建议面板", () => {
  it("渲染建议与模型名，采纳走 onSuggestAccept", () => {
    const { onSuggestAccept } = renderCard({
      suggestPanel: { loading: false, text: "AI 译文", error: "", model: "m1" },
    });
    expect(document.querySelector(".suggest-panel-body")!.textContent).toContain("AI 译文");
    expect(document.querySelector(".suggest-panel-model")!.textContent).toContain("m1");
    fireEvent.click(
      document.querySelector(".entry-btn--suggest-accept") as HTMLButtonElement,
    );
    expect(onSuggestAccept).toHaveBeenCalledTimes(1);
  });

  it("生成中：建议与采纳按钮禁用（无文本）", () => {
    renderCard({ suggestPanel: { loading: true, text: "", error: "", model: "" } });
    expect(document.querySelector(".suggest-panel-body--loading")).not.toBeNull();
    const accept = document.querySelector(".entry-btn--suggest-accept") as HTMLButtonElement;
    expect(accept.disabled).toBe(true);
  });

  it("重新生成把补充要求传给父级", () => {
    const { onSuggestRegenerate } = renderCard({
      suggestPanel: { loading: false, text: "AI 译文", error: "", model: "" },
    });
    const input = document.querySelector(".suggest-panel-instruction") as HTMLInputElement;
    fireEvent.input(input, { target: { value: "更口语化" } });
    fireEvent.click(
      (Array.from(document.querySelectorAll(".suggest-panel-foot .entry-btn")).find(
        (b) => b.textContent === "重新生成",
      ) ?? null) as HTMLButtonElement,
    );
    expect(onSuggestRegenerate).toHaveBeenCalledWith("更口语化");
  });

  it("关闭走 onSuggestClose", () => {
    const { onSuggestClose } = renderCard({
      suggestPanel: { loading: false, text: "AI 译文", error: "", model: "" },
    });
    fireEvent.click(document.querySelector(".suggest-panel-close") as HTMLButtonElement);
    expect(onSuggestClose).toHaveBeenCalledTimes(1);
  });
});

describe("ReviewPage 双击 → AI 建议 → 采纳链路", () => {
  const PID = "projA";
  const FILE = "pass3_cache/t01.txt.json";

  beforeEach(() => {
    vi.mocked(fetchProblemTypes).mockResolvedValue([]);
    setAppState({
      activeProjectId: PID,
      activeConfigFileName: "config.yaml",
      activeFilePath: FILE,
      dirtyFiles: [],
      activeView: "review",
      pendingView: null,
    });
  });

  it("双击条目 → 请求发出 → 建议渲染 → 采纳后 alt_dst 写入且面板关闭", async () => {
    vi.mocked(requestAiSuggest).mockResolvedValue({ suggestion: "AI 建议译文", model: "m1" });
    const { fetchCacheFile, fetchCacheHranges, fetchNameDict } = await import("../lib/api/project");
    vi.mocked(fetchCacheFile).mockResolvedValue({
      project_dir: PID,
      filename: FILE,
      entries: [{ ...baseEntry }],
    } as never);
    vi.mocked(fetchCacheHranges).mockResolvedValue({
      h_ranges: [],
      batch_exists: false,
      has_h: false,
    } as never);
    vi.mocked(fetchNameDict).mockResolvedValue({ project_dir: PID, name_dict: {} } as never);

    render(() => <ReviewPage />);
    await vi.waitFor(() => {
      expect(document.querySelector(".entry-card")).not.toBeNull();
    });

    fireEvent.doubleClick(document.querySelector(".entry-card") as HTMLElement);
    await vi.waitFor(() => {
      expect(vi.mocked(requestAiSuggest)).toHaveBeenCalledWith(PID, {
        file: FILE,
        index: 7,
        draft: "旧译文",
        instruction: undefined,
      });
    });
    await vi.waitFor(() => {
      expect(document.querySelector(".suggest-panel-body")!.textContent).toContain("AI 建议译文");
    });

    // 无 alt_dst 时交换按钮不存在；采纳后出现即证明 alt_dst 已写入
    expect(document.querySelector(".entry-btn--swap")).toBeNull();
    fireEvent.click(document.querySelector(".entry-btn--suggest-accept") as HTMLButtonElement);
    await vi.waitFor(() => {
      expect(document.querySelector(".suggest-panel")).toBeNull();
    });
    expect(document.querySelector(".entry-btn--swap")).not.toBeNull();
  });
});
