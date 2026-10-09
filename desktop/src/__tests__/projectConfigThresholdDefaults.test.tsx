/**
 * problemAnalyze 阈值默认值来源验证（渲染集成）
 *
 * 前端不再硬编码 17/24/10/12，而是取后端 /api/problem-types 下发的
 * problem_analyze_defaults（与检测侧 getter 同源）；项目配置里已写的值优先。
 *   1. 配置缺该项 → 输入框显示后端默认值（而非前端写死值）
 *   2. 配置已写该项 → 显示配置值
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "@solidjs/testing-library";
import { ProjectConfigPage } from "../pages/project-config/ProjectConfigPage";
import { setAppState } from "../stores/appStore";

vi.mock("../lib/api/project", () => ({
  fetchProjectConfig: vi.fn(),
  fetchConfigSchema: vi.fn(),
  updateProjectConfig: vi.fn(),
}));

vi.mock("../lib/api/general", () => ({
  fetchTranslationGuidelines: vi.fn(),
  fetchPlugins: vi.fn(),
  fetchProblemTypes: vi.fn(),
  fetchProblemTypeCatalog: vi.fn(),
  fetchPipelineStages: vi.fn(),
}));

import { fetchProjectConfig, fetchConfigSchema } from "../lib/api/project";
import {
  fetchTranslationGuidelines,
  fetchPlugins,
  fetchProblemTypes,
  fetchProblemTypeCatalog,
  fetchPipelineStages,
} from "../lib/api/general";

const PID = "projThresholds";

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
  vi.mocked(fetchConfigSchema).mockResolvedValue({
    project_dir: "D:/workspace/TestProj",
    parameters: {},
  });
  vi.mocked(fetchTranslationGuidelines).mockResolvedValue([] as never);
  vi.mocked(fetchPlugins).mockResolvedValue([] as never);
  vi.mocked(fetchProblemTypes).mockResolvedValue([] as never);
  vi.mocked(fetchPipelineStages).mockResolvedValue({ stages: [] } as never);
});

afterEach(() => {
  vi.restoreAllMocks();
  setAppState("activeProjectId", null);
});

/** 渲染并等待阈值输入框出现，返回按 id 取输入框 value 的函数 */
async function renderThresholds() {
  render(() => <ProjectConfigPage />);
  await vi.waitFor(() => {
    expect(
      document.querySelector("#avg-sentence-length-threshold") as HTMLInputElement | null,
    ).not.toBeNull();
  });
  return (id: string) => (document.querySelector(id) as HTMLInputElement | null)?.value;
}

describe("ProjectConfigPage problemAnalyze 阈值默认值", () => {
  it("配置缺阈值时显示后端下发的默认值，不前端硬编码", async () => {
    vi.mocked(fetchProjectConfig).mockResolvedValue({
      config: { common: { language: "zh-cn" }, problemAnalyze: {} },
    } as never);
    vi.mocked(fetchProblemTypeCatalog).mockResolvedValue({
      types: [],
      defaults: {
        avgSentenceLengthThreshold: 33,
        avgSentenceLengthThresholdH: 44,
        attributiveMaxLength: 15,
        adverbialMaxLength: 16,
      },
    });

    const valueOf = await renderThresholds();

    expect(valueOf("#avg-sentence-length-threshold")).toBe("33");
    expect(valueOf("#avg-sentence-length-threshold-h")).toBe("44");
    expect(valueOf("#attributive-max-length")).toBe("15");
    expect(valueOf("#adverbial-max-length")).toBe("16");
  });

  it("后端未下发默认值且配置缺该项时不编造数值（显示为空）", async () => {
    vi.mocked(fetchProjectConfig).mockResolvedValue({
      config: { common: { language: "zh-cn" }, problemAnalyze: {} },
    } as never);
    vi.mocked(fetchProblemTypeCatalog).mockResolvedValue({ types: [], defaults: {} });

    const valueOf = await renderThresholds();

    expect(valueOf("#avg-sentence-length-threshold")).toBe("");
    expect(valueOf("#attributive-max-length")).toBe("");
  });

  it("配置已写阈值时优先显示配置值", async () => {
    vi.mocked(fetchProjectConfig).mockResolvedValue({
      config: {
        common: { language: "zh-cn" },
        problemAnalyze: {
          avgSentenceLengthThreshold: 19,
          avgSentenceLengthThresholdH: 28,
          attributiveMaxLength: 11,
          adverbialMaxLength: 13,
        },
      },
    } as never);
    vi.mocked(fetchProblemTypeCatalog).mockResolvedValue({
      types: [],
      defaults: {
        avgSentenceLengthThreshold: 33,
        avgSentenceLengthThresholdH: 44,
        attributiveMaxLength: 15,
        adverbialMaxLength: 16,
      },
    });

    const valueOf = await renderThresholds();

    expect(valueOf("#avg-sentence-length-threshold")).toBe("19");
    expect(valueOf("#avg-sentence-length-threshold-h")).toBe("28");
    expect(valueOf("#attributive-max-length")).toBe("11");
    expect(valueOf("#adverbial-max-length")).toBe("13");
  });
});
