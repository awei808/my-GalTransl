/**
 * 使用指南页（GuidePage）测试：篇目列表、默认选中、guideTarget 消费与错误态。
 *
 * 仅 mock API 层（general），验证渲染与交互；guideTarget 沿用"读后即清"口径。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, cleanup, fireEvent, waitFor } from "@solidjs/testing-library";
import { GuidePage } from "../pages/guide/GuidePage";
import { fetchGuideContent, fetchGuides, fetchVersion } from "../lib/api/general";
import { appState, setAppState } from "../stores/appStore";

vi.mock("../lib/api/general", () => ({
  fetchGuides: vi.fn(),
  fetchGuideContent: vi.fn(),
  fetchVersion: vi.fn(),
}));

const mockedFetchGuides = vi.mocked(fetchGuides);
const mockedFetchContent = vi.mocked(fetchGuideContent);
const mockedFetchVersion = vi.mocked(fetchVersion);

const GUIDES = ["00-about.md", "04-review.md", "05-dictionary.md"];

function content(name: string) {
  return { name, content: `# ${name} 标题\n\n正文内容。` };
}

describe("使用指南页", () => {
  beforeEach(() => {
    mockedFetchGuides.mockReset();
    mockedFetchContent.mockReset();
    mockedFetchVersion.mockReset();
    mockedFetchGuides.mockResolvedValue(GUIDES);
    mockedFetchContent.mockImplementation((name) => Promise.resolve(content(name)));
    mockedFetchVersion.mockResolvedValue({ version: "0.6.0" });
    setAppState("guideTarget", null);
  });

  afterEach(() => {
    cleanup();
    setAppState("guideTarget", null);
  });

  it("渲染篇目列表并默认选中第一篇", async () => {
    const { container } = render(() => <GuidePage />);
    await waitFor(() => {
      expect(container.querySelectorAll(".guide-menu-item").length).toBe(3);
    });
    expect(container.querySelector(".guide-menu-item.active")?.textContent).toContain("00-about.md");
    await waitFor(() => {
      expect(mockedFetchContent).toHaveBeenCalledWith("00-about.md");
    });
  });

  it("内容请求返回后渲染消毒 HTML 并显示版本号", async () => {
    const { container } = render(() => <GuidePage />);
    await waitFor(() => {
      expect(container.querySelector(".guide-article")?.innerHTML).toContain("<h1");
    });
    expect(container.querySelector(".guide-version")?.textContent).toBe("0.6.0");
  });

  it("目录标题后台预取：未打开过的篇目也显示标题而非文件名", async () => {
    const { container } = render(() => <GuidePage />);
    await waitFor(() => {
      expect(container.querySelectorAll(".guide-menu-item").length).toBe(3);
    });
    // 预取完成后，全部条目标题来自各篇一级标题（mock 内容为 "# <name> 标题"）
    await waitFor(() => {
      const texts = [...container.querySelectorAll(".guide-menu-item")].map((el) => el.textContent);
      expect(texts.some((t) => t?.includes("04-review.md 标题"))).toBe(true);
    });
  });

  it("点击目录项加载对应篇目", async () => {
    const { container } = render(() => <GuidePage />);
    await waitFor(() => {
      expect(container.querySelectorAll(".guide-menu-item").length).toBe(3);
    });
    const items = [...container.querySelectorAll(".guide-menu-item")];
    fireEvent.click(items[2]);
    await waitFor(() => {
      expect(mockedFetchContent).toHaveBeenCalledWith("05-dictionary.md");
    });
  });

  it("guideTarget 指定篇目时直接打开并读后即清", async () => {
    setAppState("guideTarget", "04-review.md");
    const { container } = render(() => <GuidePage />);
    await waitFor(() => {
      expect(mockedFetchContent).toHaveBeenCalledWith("04-review.md");
    });
    expect(appState.guideTarget).toBeNull();
    expect(container.querySelector(".guide-menu-item.active")?.textContent).toContain("04-review.md");
  });

  it("目录为空时显示空态", async () => {
    mockedFetchGuides.mockResolvedValue([]);
    const { container } = render(() => <GuidePage />);
    await waitFor(() => {
      expect(container.textContent).toContain("暂无指南内容");
    });
  });

  it("列表加载失败显示重试入口", async () => {
    mockedFetchGuides.mockRejectedValue(new Error("backend offline"));
    const { container } = render(() => <GuidePage />);
    await waitFor(() => {
      expect(container.textContent).toContain("指南列表加载失败");
    });
    expect(container.querySelector(".guide-retry-btn")).toBeTruthy();
  });

  it("内容加载失败显示错误信息", async () => {
    mockedFetchContent.mockRejectedValue(new Error("404"));
    const { container } = render(() => <GuidePage />);
    await waitFor(() => {
      expect(container.textContent).toContain("指南内容加载失败");
    });
  });
});
