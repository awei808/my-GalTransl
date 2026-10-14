/**
 * 校对页顶部工具栏契约测试（两行分组）。
 *
 * 背景：工具栏曾是一条不换行的 flex 行，塞了文件名 + 3 个筛选 + 跳转 + 条数 +
 * H 区间 + 未保存 + 保存按钮 + AI 提示 + 指南 共 9~10 个元素，最小窗宽（1100px，
 * 主内容区约 736px）下固定宽度就已溢出、互相挤压。重排后固化为两行：
 * - 第 1 行 .review-toolbar-main：文件身份 + 状态 + 动作（翻译/元数据两种模式共用）
 * - 第 2 行 .review-toolbar-filters：筛选 + 跳转（仅翻译校对模式）
 *
 * 本测试锁定该结构与几处"合并口径"，避免后续改动把筛选控件挪回第 1 行、
 * 给元数据模式渲染空筛选行、或把合并掉的重复计数再加回来。
 * 行高/控件高度属纯视觉，由 CSS（part03.css .review-toolbar 一组变量）承载，
 * jsdom 无布局引擎，此处不覆盖，需目视验证。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent } from "@solidjs/testing-library";
import { ReviewPage } from "../pages/review/ReviewPage";
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

import {
  fetchCacheFile,
  fetchCacheHranges,
  fetchNameDict,
  fetchPerFileMetadata,
} from "../lib/api/project";
import { fetchProblemTypes } from "../lib/api/general";

const PID = "projA";
const FILE = "pass3_cache/t01.txt.json";
const META_FILE = "pass1_cache/t01.txt.json.meta.json";

function entry(over: Partial<CacheEntry>): CacheEntry {
  return { index: 1, name: "", pre_src: "", post_src: "", pre_dst: "", ...over };
}

/** 挂载翻译校对模式并等待条目渲染完成 */
async function mountTranslate(
  entries: CacheEntry[],
  hranges: {
    h_ranges: { lo: number; hi: number; h?: number }[];
    batch_exists: boolean;
    has_h: boolean;
  } = {
    h_ranges: [],
    batch_exists: false,
    has_h: false,
  },
) {
  vi.mocked(fetchCacheFile).mockResolvedValue({
    project_dir: PID,
    filename: FILE,
    entries,
  } as never);
  vi.mocked(fetchCacheHranges).mockResolvedValue(hranges as never);

  const view = render(() => <ReviewPage />);
  await vi.waitFor(() => {
    expect(document.querySelector(".entry-card")).not.toBeNull();
  });
  return view;
}

function textOf(root: Element, selector: string): string {
  return root.querySelector(selector)?.textContent?.replace(/\s+/g, " ").trim() ?? "";
}

beforeEach(() => {
  vi.clearAllMocks();
  // 每个用例都自带全部 API 的默认实现：clearAllMocks 只清调用记录、不重置实现，
  // 但用例必须能单独运行（-t / .only），不得依赖前一个用例泄漏下来的 mock 实现
  // （fetchNameDict 在 ReviewPage 里是无模式保护的 effect，缺实现会直接抛 TypeError）。
  vi.mocked(fetchProblemTypes).mockResolvedValue([]);
  vi.mocked(fetchCacheFile).mockResolvedValue({
    project_dir: PID,
    filename: FILE,
    entries: [],
  } as never);
  vi.mocked(fetchCacheHranges).mockResolvedValue({
    h_ranges: [],
    batch_exists: false,
    has_h: false,
  } as never);
  vi.mocked(fetchNameDict).mockResolvedValue({ project_dir: PID, name_dict: {} } as never);
  vi.mocked(fetchPerFileMetadata).mockResolvedValue({
    exists: false,
    type: "filemeta",
    entry: null,
  } as never);
  setAppState({
    activeProjectId: PID,
    activeConfigFileName: "config.yaml",
    activeFilePath: FILE,
    dirtyFiles: [],
    activeView: "review",
    pendingView: null,
  });
});

describe("校对页工具栏两行结构", () => {
  it("翻译模式：身份/状态/动作在第 1 行，筛选/跳转在第 2 行", async () => {
    await mountTranslate([
      entry({ index: 1, name: "ベルナ", problem: "残留日文" }),
      entry({ index: 2, name: "リズベル" }),
    ]);

    const toolbar = document.querySelector(".review-toolbar")!;
    const main = toolbar.querySelector(".review-toolbar-main");
    const filters = toolbar.querySelector(".review-toolbar-filters");
    expect(main).not.toBeNull();
    expect(filters).not.toBeNull();

    // 第 1 行：文件名（内层 span 承载省略号）/ 条数 / 保存并重检 / AI 提示 / 指南
    expect(textOf(main!, ".review-filename-text")).toBe("t01.txt.json");
    expect(textOf(main!, ".review-count")).toBe("2 条");
    expect(textOf(main!, ".review-actions")).toContain("保存并重检问题");
    expect(textOf(main!, ".review-ai-hint")).toContain("AI 精修");
    expect(main!.querySelector(".guide-link")).not.toBeNull();

    // 第 2 行：两个自定义下拉（视图/类型）+ 说话人 select + 跳转组
    expect(filters!.querySelectorAll(".review-filter-dropdown").length).toBe(2);
    expect(filters!.querySelector(".review-filter-select")).not.toBeNull();
    expect(filters!.querySelector(".review-jump-group")).not.toBeNull();

    // 筛选与跳转不得留在第 1 行（否则第 1 行又被撑满）
    expect(main!.querySelector(".review-filter-select")).toBeNull();
    expect(main!.querySelector(".review-filter-dropdown")).toBeNull();
    expect(main!.querySelector(".review-jump-group")).toBeNull();
  });

  it("元数据模式：不渲染筛选行（不留空行占高）", async () => {
    vi.mocked(fetchPerFileMetadata).mockResolvedValue({
      exists: true,
      type: "filemeta",
      entry: { id: "t01.txt.json", 场景: "序章" },
    } as never);
    setAppState({ activeFilePath: META_FILE });

    render(() => <ReviewPage />);
    // 等元数据真正加载完成（工具栏骨架一渲染就在，等它等于没等）
    await vi.waitFor(() => {
      expect(textOf(document.body, ".review-count")).toBe("1 条");
    });

    const main = document.querySelector(".review-toolbar-main")!;
    expect(document.querySelector(".review-toolbar-filters")).toBeNull();
    expect(textOf(main, ".review-filename-text")).toBe("t01.txt.json");
    // 元数据模式：条数与保存按钮同样在第 1 行
    expect(textOf(main, ".review-actions")).toContain("保存");
    // 筛选行不渲染，则元数据模式也不应出现 AI 精修提示（提示仅翻译模式）
    expect(main.querySelector(".review-ai-hint")).toBeNull();
  });
});

describe("校对页工具栏状态口径", () => {
  it("H 区间只报段数，完整区间进 title", async () => {
    await mountTranslate([entry({ index: 12 }), entry({ index: 30 })], {
      h_ranges: [
        { lo: 12, hi: 30, h: 0.5 },
        { lo: 44, hi: 60, h: 0.8 },
      ],
      batch_exists: true,
      has_h: true,
    });

    const badge = document.querySelector(".review-h-ranges")!;
    expect(badge.textContent).toBe("H 区间 2 段");
    const title = badge.getAttribute("title") ?? "";
    expect(title).toContain("#12~#30 H0.5");
    expect(title).toContain("#44~#60 H0.8");
  });

  it("无 H 批次元数据时不渲染 H 区间徽标", async () => {
    await mountTranslate([entry({ index: 1 })]);
    expect(document.querySelector(".review-h-ranges")).toBeNull();
  });

  it("筛选生效后工具栏只有一个计数元素，形如「M/N 条 · 问题 K」，清除筛选在筛选行", async () => {
    await mountTranslate([
      entry({ index: 1, name: "ベルナ", problem: "残留日文" }),
      entry({ index: 2, name: "リズベル" }),
    ]);

    const select = document.querySelector(".review-filter-select") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "ベルナ" } });

    await vi.waitFor(() => {
      expect(textOf(document.body, ".review-count")).toBe("1/2 条 · 问题 1");
    });

    const toolbar = document.querySelector(".review-toolbar")!;
    // 原先的「工具栏条数」与「筛选栏 筛选后/总 条」两个元素合并为一个
    expect(toolbar.querySelectorAll(".review-count").length).toBe(1);
    // 筛选态用二级文字色（合并前筛选栏计数即此色，属"看到的是子集"的提示）
    expect(
      toolbar.querySelector(".review-count")!.classList.contains("review-count--filtered"),
    ).toBe(true);

    const clear = toolbar.querySelector(".review-filter-clear");
    expect(clear).not.toBeNull();
    expect(clear!.parentElement!.classList.contains("review-filter-bar")).toBe(true);
    expect(document.querySelector(".review-toolbar-filters")!.contains(clear!)).toBe(true);
  });

  it("未保存指示在第 1 行且与保存按钮同簇", async () => {
    await mountTranslate([entry({ index: 1 })]);
    const toolbar = document.querySelector(".review-toolbar")!;
    expect(toolbar.querySelector(".review-dirty-dot")).toBeNull();

    setAppState("dirtyFiles", [FILE]);
    await vi.waitFor(() => {
      expect(toolbar.querySelector(".review-dirty-dot")).not.toBeNull();
    });
    const dot = toolbar.querySelector(".review-dirty-dot")!;
    expect(dot.parentElement!.classList.contains("review-status")).toBe(true);
    expect(dot.parentElement!.nextElementSibling!.classList.contains("review-actions")).toBe(true);
  });
});
