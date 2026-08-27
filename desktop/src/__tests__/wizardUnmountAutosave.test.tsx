/**
 * NewProjectWizard 卸载自动保存验证（渲染集成）
 *
 * 覆盖方案 A 骨架接入后的关键不变式：
 *   1. 进入过设置步骤（第 4/5 步）且有未保存修改 → 卸载时自动保存并 toast.info
 *   2. 进入过设置步骤但无修改 → 卸载完全不落盘、不提示
 *   3. 从未进入设置步骤 → 卸载不保存（skip）
 *   4. 卸载自动保存失败 → toast.error（文案与探索式保存一致，无详情降级）
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, fireEvent } from "@solidjs/testing-library";
import { NewProjectWizard } from "../pages/wizard/NewProjectWizard";
import { toast } from "../stores/toastStore";

vi.mock("../lib/api/project", () => ({
  fetchProjectConfig: vi.fn(),
  updateProjectConfig: vi.fn(),
  initProject: vi.fn(),
  importProjectFiles: vi.fn(),
  fetchWorkspaceRoot: vi.fn(),
}));

vi.mock("../lib/api/general", () => ({
  fetchPlugins: vi.fn(),
  fetchTranslationGuidelines: vi.fn(),
  submitJob: vi.fn(),
  fetchJob: vi.fn(),
}));

vi.mock("../lib/api/client", () => ({
  ensureDesktopBackendReady: vi.fn(),
  ApiError: class ApiError extends Error {},
}));

// toastStore → logStore 的日志上报依赖 client 的 apiRequest；直接 no-op sendLog
vi.mock("../lib/api/log", () => ({
  sendLog: vi.fn(),
}));

vi.mock("../lib/api/preferences", () => ({
  setSelectedBackendProfile: vi.fn(),
  getBackendProfileNames: () => [],
  getDefaultBackendProfile: () => "",
}));

vi.mock("../stores/confirmStore", () => ({
  confirm: { show: vi.fn() },
}));

vi.mock("@tauri-apps/plugin-dialog", () => ({
  open: vi.fn(),
}));

import {
  fetchProjectConfig,
  updateProjectConfig,
  initProject,
  fetchWorkspaceRoot,
} from "../lib/api/project";
import { fetchPlugins, fetchTranslationGuidelines } from "../lib/api/general";
import { ensureDesktopBackendReady } from "../lib/api/client";

const PID = "projW";

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchWorkspaceRoot).mockResolvedValue({ workspace_root: "D:/workspace" });
  vi.mocked(initProject).mockResolvedValue({
    project_id: PID,
    project_dir: "D:/workspace/TestProj",
  });
  vi.mocked(ensureDesktopBackendReady).mockResolvedValue(undefined as never);
  vi.mocked(fetchPlugins).mockResolvedValue([
    { name: "file_galtransl_json", type: "file", display_name: "GALTRANSL JSON" },
    { name: "text_common_normalfix", type: "text", display_name: "通用修复" },
  ] as never);
  // 返回空列表：避免进入设置步骤时异步默认翻译规范的填充让基线 snapshot 波动
  //（真实场景该波动会把"未手动修改"误判为 dirty，属既有边界；测试用空列表隔离，显式聚焦编辑判定）
  vi.mocked(fetchTranslationGuidelines).mockResolvedValue([]);
  vi.mocked(fetchProjectConfig).mockResolvedValue({
    config: {
      common: {},
      gpt: undefined,
      plugin: undefined,
      externals: undefined,
      internals: undefined,
    },
  });
  vi.mocked(updateProjectConfig).mockResolvedValue({ success: true } as never);
});

afterEach(() => {
  vi.restoreAllMocks();
});

/** 点击文本匹配的导航按钮 */
function clickNavButton(text: string): void {
  const btn = Array.from(document.querySelectorAll(".wizard-nav button")).find((b) =>
    b.textContent?.includes(text),
  );
  expect(btn, `找不到导航按钮 ${text}`).toBeTruthy();
  fireEvent.click(btn!);
}

/** 创建项目并连续「下一步」进入第 4 步（常用设置），返回渲染结果以便卸载 */
async function renderAtSettingsStep() {
  const result = render(() => <NewProjectWizard />);
  const nameInput = document.querySelector<HTMLInputElement>(
    ".wizard-panel input.field__input",
  );
  fireEvent.input(nameInput!, { target: { value: "TestProj" } });
  const createBtn = Array.from(document.querySelectorAll(".wizard-actions button")).find((b) =>
    b.textContent?.includes("创建项目"),
  );
  fireEvent.click(createBtn!);
  await vi.waitFor(() => {
    expect(vi.mocked(initProject)).toHaveBeenCalledWith("TestProj", false);
  });
  for (let i = 0; i < 3; i++) {
    clickNavButton("下一步");
    await new Promise((r) => setTimeout(r, 0));
  }
  await vi.waitFor(() => {
    expect(document.querySelector(".wizard-panel-title")?.textContent).toContain("常用设置");
  });
  return result;
}

/** 在常用设置步骤修改「目标语言」下拉（第 2 个 select），触发编辑基线变化 */
function changeLanguage(value: string) {
  const select = document.querySelectorAll<HTMLSelectElement>(
    ".wizard-settings-grid select.field__input",
  )[1];
  expect(select, "找不到目标语言下拉").toBeTruthy();
  fireEvent.change(select!, { target: { value } });
}

describe("NewProjectWizard 卸载自动保存", () => {
  it("进入设置步骤并修改后卸载 → 自动保存并 toast.info", async () => {
    const infoSpy = vi.spyOn(toast, "info").mockImplementation(() => "t");
    const { unmount } = await renderAtSettingsStep();
    changeLanguage("zh-tw");
    unmount();
    await vi.waitFor(() => {
      expect(vi.mocked(updateProjectConfig)).toHaveBeenCalled();
    });
    expect(vi.mocked(updateProjectConfig)).toHaveBeenCalledWith(
      PID,
      expect.objectContaining({ config_file_name: "config.yaml" }),
    );
    await vi.waitFor(() => {
      expect(infoSpy).toHaveBeenCalledWith("已自动保存向导设置", 3000);
    });
  });

  it("进入设置步骤但无修改卸载 → 不落盘、不提示", async () => {
    const infoSpy = vi.spyOn(toast, "info").mockImplementation(() => "t");
    const errorSpy = vi.spyOn(toast, "error").mockImplementation(() => "t");
    const { unmount } = await renderAtSettingsStep();
    unmount();
    await new Promise((r) => setTimeout(r, 20)); // 留出异步保存窗口
    expect(vi.mocked(updateProjectConfig)).not.toHaveBeenCalled();
    expect(infoSpy).not.toHaveBeenCalled();
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it("从未进入设置步骤卸载 → 不保存", async () => {
    const { unmount } = render(() => <NewProjectWizard />);
    unmount();
    await new Promise((r) => setTimeout(r, 20));
    expect(vi.mocked(updateProjectConfig)).not.toHaveBeenCalled();
  });

  it("卸载自动保存失败 → toast.error 且保留失败详情", async () => {
    const errorSpy = vi.spyOn(toast, "error").mockImplementation(() => "t");
    vi.mocked(updateProjectConfig).mockRejectedValue(new Error("网络中断"));
    const { unmount } = await renderAtSettingsStep();
    changeLanguage("zh-tw");
    unmount();
    // handleSaveSettings 内部 catch 后返回 false（不抛异常），failMessage 动态读取 lastSaveError 补详情
    await vi.waitFor(() => {
      expect(errorSpy).toHaveBeenCalledWith("自动保存向导设置失败：网络中断");
    });
  });
});