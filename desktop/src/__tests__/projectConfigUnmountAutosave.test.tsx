/**
 * ProjectConfigPage 卸载自动保存验证（渲染集成）
 *
 * 覆盖统一自动保存骨架接入后的关键不变式：
 *   1. 编辑后卸载 → 用挂载时项目/配置名快照落盘并 toast.info（短时长）
 *   2. 无未保存修改卸载 → 不落盘、不提示（完全静默）
 *   3. 卸载保存失败 → toast.error 且保留失败详情（lastSaveError 兜底，不降级）
 *   4. 手动保存飞行中卸载 → waitForReady 等待在途保存结束后按实际 dirty 接续落盘
 *      （飞行期间新编辑不丢失）
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
}));

import {
  fetchProjectConfig,
  fetchConfigSchema,
  updateProjectConfig,
} from "../lib/api/project";
import { fetchTranslationGuidelines, fetchPlugins, fetchProblemTypes } from "../lib/api/general";

const PID = "projA";

beforeEach(() => {
  vi.clearAllMocks();
  setAppState({
    activeProjectId: PID,
    activeConfigFileName: "config.yaml",
    configNameDetecting: false,
    activeFilePath: null,
    dirtyFiles: [],
  });
  vi.mocked(fetchProjectConfig).mockResolvedValue({
    config: { common: { language: "zh-cn" } },
  } as never);
  vi.mocked(fetchConfigSchema).mockResolvedValue({ parameters: {} });
  vi.mocked(fetchTranslationGuidelines).mockResolvedValue([] as never);
  vi.mocked(fetchPlugins).mockResolvedValue([] as never);
  vi.mocked(fetchProblemTypes).mockResolvedValue([] as never);
  vi.mocked(updateProjectConfig).mockResolvedValue({ success: true } as never);
});

afterEach(() => {
  vi.restoreAllMocks();
  setAppState("activeProjectId", null);
});

/** 渲染并等待配置加载完成（配置列表出现可编辑输入框） */
async function renderLoaded() {
  const result = render(() => <ProjectConfigPage />);
  await vi.waitFor(() => {
    expect(vi.mocked(fetchProjectConfig)).toHaveBeenCalledWith(PID, "config.yaml");
  });
  await vi.waitFor(() => {
    expect(document.querySelector(".pc-field-list input.pc-input")).not.toBeNull();
  });
  return result;
}

/** 编辑首个配置输入框（触发 setValue → dirty） */
function editFirstField(value: string) {
  const input = document.querySelector<HTMLInputElement>(".pc-field-list input.pc-input");
  expect(input, "找不到配置输入框").toBeTruthy();
  fireEvent.input(input!, { target: { value } });
}

/** 「保存配置」按钮 */
function saveButton(): HTMLButtonElement {
  const btn = Array.from(document.querySelectorAll("button")).find((b) =>
    b.textContent?.includes("保存配置"),
  );
  expect(btn, "找不到保存配置按钮").toBeTruthy();
  return btn!;
}

describe("ProjectConfigPage 卸载自动保存", () => {
  it("编辑后卸载 → 用挂载时配置名快照落盘并 toast.info（短时长）", async () => {
    const infoSpy = vi.spyOn(toast, "info").mockImplementation(() => "t");
    const { unmount } = await renderLoaded();
    editFirstField("zh-tw");
    unmount();
    await vi.waitFor(() => {
      expect(vi.mocked(updateProjectConfig)).toHaveBeenCalled();
    });
    expect(vi.mocked(updateProjectConfig)).toHaveBeenCalledWith(
      PID,
      expect.objectContaining({
        config_file_name: "config.yaml",
        config: expect.objectContaining({
          common: expect.objectContaining({ language: "zh-tw" }),
        }),
      }),
    );
    // 成功提示：统一 info 短时长（runPageAutosave 延迟到宏任务）
    await vi.waitFor(() => {
      expect(infoSpy).toHaveBeenCalledWith("已自动保存配置", 3000);
    });
  });

  it("无未保存修改卸载 → 不落盘、不提示", async () => {
    const infoSpy = vi.spyOn(toast, "info").mockImplementation(() => "t");
    const errorSpy = vi.spyOn(toast, "error").mockImplementation(() => "t");
    const { unmount } = await renderLoaded();
    unmount();
    await new Promise((r) => setTimeout(r, 20)); // 留出异步保存窗口
    expect(vi.mocked(updateProjectConfig)).not.toHaveBeenCalled();
    expect(infoSpy).not.toHaveBeenCalled();
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it("卸载保存失败 → toast.error 且保留失败详情", async () => {
    const errorSpy = vi.spyOn(toast, "error").mockImplementation(() => "t");
    vi.mocked(updateProjectConfig).mockRejectedValue(new Error("网络中断"));
    const { unmount } = await renderLoaded();
    editFirstField("zh-tw");
    unmount();
    // doSave 捕获后返回 false（不抛异常），failMessage 动态读取 lastSaveError 补详情
    await vi.waitFor(() => {
      expect(errorSpy).toHaveBeenCalledWith("自动保存配置失败：网络中断");
    });
  });

  it("手动保存飞行中卸载 → 等待在途保存结束后按实际 dirty 接续落盘（飞行期间编辑不丢失）", async () => {
    let resolveSave!: (v: never) => void;
    vi.mocked(updateProjectConfig).mockImplementation(
      () => new Promise((r) => (resolveSave = r)) as never,
    );
    const { unmount } = await renderLoaded();
    editFirstField("zh-tw"); // 手动保存前已有编辑
    fireEvent.click(saveButton()); // 手动保存 → 在途 pending
    await vi.waitFor(() => {
      expect(vi.mocked(updateProjectConfig)).toHaveBeenCalledTimes(1);
    });
    // 手动保存飞行中继续编辑：editVersion 变化，保存完成时不得清 dirty
    editFirstField("ja");
    // 卸载：runPageAutosave 的 waitForReady 等待手动保存结束后再判定 dirty
    unmount();
    resolveSave({ success: true } as never);
    await vi.waitFor(() => {
      expect(vi.mocked(updateProjectConfig).mock.calls.length).toBeGreaterThanOrEqual(2);
    });
    // 卸载自动保存接续落盘的是「挂载快照」目标 + 最新编辑
    const lastCall = vi.mocked(updateProjectConfig).mock.calls.at(-1);
    expect(lastCall![0]).toBe(PID);
    expect(lastCall![1]).toEqual(
      expect.objectContaining({
        config_file_name: "config.yaml",
        config: expect.objectContaining({
          common: expect.objectContaining({ language: "ja" }),
        }),
      }),
    );
  });

  it("手动保存卡死超过 waitForReady 超时后卸载 → 自动保存不被守卫拦截，脏数据仍落盘", async () => {
    let resolveSave!: (v: never) => void;
    vi.mocked(updateProjectConfig).mockImplementation(
      () => new Promise((r) => (resolveSave = r)) as never,
    );
    const { unmount } = await renderLoaded();
    editFirstField("zh-tw");
    fireEvent.click(saveButton()); // 手动保存 → 卡死 pending（saving=true，dirty 未清）
    await vi.waitFor(() => {
      expect(vi.mocked(updateProjectConfig)).toHaveBeenCalledTimes(1);
    });
    // 卸载后 waitForReady（waitForSavingDone）等待 3s 超时兜底：此时 saving 仍 true，
    // 但页面未注册 isBusy 守卫 → 自动保存继续执行，避免脏数据被静默丢弃
    vi.useFakeTimers();
    unmount();
    await vi.advanceTimersByTimeAsync(3100); // 推进直到 waitForSavingDone 超时退出
    expect(vi.mocked(updateProjectConfig).mock.calls.length).toBeGreaterThanOrEqual(2);
    resolveSave({ success: true } as never);
    await vi.advanceTimersByTimeAsync(0);
    vi.useRealTimers();
  });

  it("上一次手动保存失败后卸载重试成功 → 静默（不弹成功 toast，避免与失败提示矛盾）", async () => {
    const errorSpy = vi.spyOn(toast, "error").mockImplementation(() => "t");
    const infoSpy = vi.spyOn(toast, "info").mockImplementation(() => "t");
    vi.mocked(updateProjectConfig)
      .mockRejectedValueOnce(new Error("网络中断")) // 手动保存失败
      .mockResolvedValueOnce({ success: true } as never); // 卸载自动保存成功
    const { unmount } = await renderLoaded();
    editFirstField("zh-tw");
    fireEvent.click(saveButton()); // 手动保存失败 → lastSaveError 有值、dirty 保留
    await vi.waitFor(() => {
      expect(vi.mocked(updateProjectConfig)).toHaveBeenCalledTimes(1);
      expect(errorSpy).toHaveBeenCalledWith("保存失败：网络中断");
    });
    unmount(); // 卸载：saveFailedAtUnmount 固化 → 重试成功时应静默
    await vi.waitFor(() => {
      expect(vi.mocked(updateProjectConfig)).toHaveBeenCalledTimes(2);
    });
    await new Promise((r) => setTimeout(r, 20)); // 留出宏任务 toast 窗口
    expect(infoSpy).not.toHaveBeenCalled();
  });
});