/**
 * ProjectConfigPage 搜索过滤与分区导航验证（渲染集成）
 *
 * 覆盖重设计后的关键行为：
 *   1. 搜索按 标签/键名/说明 过滤：未命中分组隐藏，命中分组标注「匹配 N 项」
 *   2. 清空搜索恢复全部分组；无命中显示空态文案
 *   3. 固定卡片参与搜索（静态关键词命中）
 *   4. 搜索态强制展开分组，折叠记录不受影响；清空后恢复折叠
 *   5. 左侧分区导航：点击展开折叠分组并高亮
 *   6. 编辑字段出现「未保存」徽标，保存成功后消失
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, fireEvent } from "@solidjs/testing-library";
import { ProjectConfigPage } from "../pages/project-config/ProjectConfigPage";
import { setAppState } from "../stores/appStore";
import { toast } from "../stores/toastStore";

vi.mock("../lib/api/project", () => ({
  fetchProjectConfig: vi.fn(),
  fetchConfigSchema: vi.fn(),
  updateProjectConfig: vi.fn(),
}));

vi.mock("../lib/api/general", () => ({
  fetchTranslationGuidelines: vi.fn(),
  fetchPlugins: vi.fn(),
  fetchProblemTypes: vi.fn(),
  fetchPipelineStages: vi.fn(),
}));

import {
  fetchProjectConfig,
  fetchConfigSchema,
  updateProjectConfig,
} from "../lib/api/project";
import {
  fetchTranslationGuidelines,
  fetchPlugins,
  fetchProblemTypes,
  fetchPipelineStages,
} from "../lib/api/general";

const PID = "projA";

// jsdom 未实现 scrollIntoView（导航跳转/快捷锚点用）。
// 文件级一次性替换：jumpToSection 的 180ms 延时滚动可能在 afterEach 之后触发，
// 若按用例还原会偶发 "scrollIntoView is not a function"（vitest 按文件隔离环境，无跨文件污染）。
Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  setAppState({
    activeProjectId: PID,
    activeConfigFileName: "config.yaml",
    configNameDetecting: false,
    activeFilePath: null,
    dirtyFiles: [],
  });
  vi.mocked(fetchProjectConfig).mockResolvedValue({
    config: {
      common: { language: "zh-cn", workersPerProject: 5 },
      dictionary: { sortDict: true },
    },
  } as never);
  vi.mocked(fetchConfigSchema).mockResolvedValue({
    project_dir: "D:/workspace/TestProj",
    parameters: {},
  });
  vi.mocked(fetchTranslationGuidelines).mockResolvedValue([] as never);
  vi.mocked(fetchPlugins).mockResolvedValue([] as never);
  vi.mocked(fetchProblemTypes).mockResolvedValue([] as never);
  vi.mocked(fetchPipelineStages).mockResolvedValue({ stages: [] } as never);
  vi.mocked(updateProjectConfig).mockResolvedValue({ success: true } as never);
});

afterEach(() => {
  vi.restoreAllMocks();
  setAppState("activeProjectId", null);
});

/** 渲染并等待配置加载完成（出现可编辑输入框） */
async function renderLoaded() {
  const result = render(() => <ProjectConfigPage />);
  await vi.waitFor(() => {
    expect(document.querySelector(".pc-field-list input.pc-input")).not.toBeNull();
  });
  return result;
}

function searchInput(): HTMLInputElement {
  const el = document.querySelector<HTMLInputElement>(".pc-search__input");
  expect(el, "找不到搜索框").toBeTruthy();
  return el!;
}

const nav = () => document.querySelector('nav[aria-label="设置分区导航"]');
const groups = () => Array.from(document.querySelectorAll<HTMLElement>(".pc-group"));
const groupByTitle = (title: string) => groups().find((g) => g.dataset.title === title);

describe("ProjectConfigPage 搜索过滤", () => {
  it("搜索命中标签 → 仅显示命中分组并标注匹配数", async () => {
    await renderLoaded();
    const initialCount = groups().length;
    expect(initialCount).toBeGreaterThan(3); // 固定卡片分区（翻译规范文件/问题检测等）也在列
    fireEvent.input(searchInput(), { target: { value: "并行" } });
    await vi.waitFor(() => expect(groups().length).toBe(1));
    expect(groups()[0].querySelector(".pc-group-title-text")?.textContent).toContain(
      "翻译后端总设置",
    );
    expect(groups()[0].querySelector(".pc-group-meta")?.textContent).toContain("匹配 1 项");
    // 清空恢复全部分组
    fireEvent.input(searchInput(), { target: { value: "" } });
    await vi.waitFor(() => expect(groups().length).toBe(initialCount));
  });

  it("搜索无命中 → 显示空态文案", async () => {
    await renderLoaded();
    fireEvent.input(searchInput(), { target: { value: "绝不存在的词qqq" } });
    await vi.waitFor(() => {
      expect(document.querySelector(".pc-status--center")?.textContent).toContain("没有匹配");
    });
  });

  it("固定卡片静态关键词命中（游戏外部信息）", async () => {
    await renderLoaded();
    fireEvent.input(searchInput(), { target: { value: "外部信息" } });
    await vi.waitFor(() => expect(groups().length).toBe(1));
    expect(groups()[0].querySelector(".pc-group-title-text")?.textContent).toContain("全局提示词");
  });

  it("搜索态强制展开分组，清空后恢复折叠记录", async () => {
    await renderLoaded();
    const dictGroup = groupByTitle("字典");
    expect(dictGroup, "配置夹具应产生「字典」分组").toBeTruthy();
    fireEvent.click(dictGroup!.querySelector(".pc-group-title--toggle")!);
    await vi.waitFor(() => expect(dictGroup!.className).toContain("pc-group--collapsed"));

    fireEvent.input(searchInput(), { target: { value: "词长" } });
    await vi.waitFor(() => {
      const g = groupByTitle("字典");
      expect(g).toBeTruthy();
      expect(g!.className).not.toContain("pc-group--collapsed");
    });

    fireEvent.input(searchInput(), { target: { value: "" } });
    await vi.waitFor(() => {
      expect(groupByTitle("字典")!.className).toContain("pc-group--collapsed");
    });
  });

  it("搜索态键盘 Enter 与鼠标同口径守卫：不改写折叠记录", async () => {
    await renderLoaded();
    fireEvent.input(searchInput(), { target: { value: "并行" } });
    await vi.waitFor(() => expect(groups().length).toBe(1));
    const title = groups()[0].querySelector(".pc-group-title--toggle");
    expect(title).toBeTruthy();
    fireEvent.keyDown(title!, { key: "Enter" });
    // 折叠记录未被键盘操作写入
    const stored = JSON.parse(
      localStorage.getItem("galtransl.project-config.collapse.v1") ?? "[]",
    );
    expect(stored).toEqual([]);
    // 清空搜索后分组仍为展开
    fireEvent.input(searchInput(), { target: { value: "" } });
    await vi.waitFor(() => expect(groups().length).toBeGreaterThan(3));
    const g = groupByTitle("翻译后端总设置")!;
    expect(g.querySelector(".pc-group-title--toggle")!.getAttribute("aria-expanded")).toBe("true");
  });

  it("分组计数：固定卡片按内部条目统计（问题检测 = 类型数 + 阈值行数）", async () => {
    await renderLoaded();
    const problemGroup = groupByTitle("问题检测");
    expect(problemGroup, "配置夹具应产生「问题检测」分组").toBeTruthy();
    // 夹具未返回问题类型 → 仅 4 个阈值行计入
    expect(problemGroup!.querySelector(".pc-group-meta")?.textContent).toContain("4 项");
  });
});

describe("ProjectConfigPage 分区导航", () => {
  it("导航渲染全部分区；点击展开折叠分组并高亮", async () => {
    await renderLoaded();
    const initialCount = groups().length;
    expect(nav()).not.toBeNull();
    expect(nav()!.querySelectorAll("button").length).toBe(initialCount);

    const dictGroup = groupByTitle("字典");
    fireEvent.click(dictGroup!.querySelector(".pc-group-title--toggle")!);
    await vi.waitFor(() => expect(dictGroup!.className).toContain("pc-group--collapsed"));

    const navBtn = Array.from(nav()!.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("字典"),
    );
    expect(navBtn, "导航应包含「字典」项").toBeTruthy();
    fireEvent.click(navBtn!);
    await vi.waitFor(() => {
      expect(groupByTitle("字典")!.className).not.toContain("pc-group--collapsed");
      expect(navBtn!.className).toContain("pc-nav__item--active");
    });
  });
});

describe("ProjectConfigPage 未保存徽标", () => {
  it("编辑字段出现「未保存」，保存成功后消失", async () => {
    const infoSpy = vi.spyOn(toast, "success").mockImplementation(() => "t");
    await renderLoaded();
    const input = document.querySelector<HTMLInputElement>(".pc-field-list input.pc-input");
    expect(input, "找不到配置输入框").toBeTruthy();
    fireEvent.input(input!, { target: { value: "zh-tw" } });
    expect(document.querySelector(".pc-dirty-pill"), "编辑后应出现未保存徽标").not.toBeNull();
    const saveBtn = Array.from(document.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("保存配置"),
    );
    expect(saveBtn).toBeTruthy();
    fireEvent.click(saveBtn!);
    await vi.waitFor(() => {
      expect(updateProjectConfig).toHaveBeenCalled();
      expect(document.querySelector(".pc-dirty-pill")).toBeNull();
    });
    expect(infoSpy).toHaveBeenCalled();
  });
});
