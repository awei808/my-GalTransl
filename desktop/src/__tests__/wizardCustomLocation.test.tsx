/**
 * NewProjectWizard 自定义创建位置验证
 *
 * 覆盖：第 1 步「创建位置」切换（应用程序目录 / 自定义位置）、
 * 自定义父目录为空时创建按钮禁用、initProject 透传 parent_dir、
 * 创建成功后切换位置使已创建结果失效（预览回落实时 previewDir）、
 * Web 模式（无 Tauri）不显示浏览按钮。
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, fireEvent } from "@solidjs/testing-library";
import { NewProjectWizard } from "../pages/wizard/NewProjectWizard";

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
  getThemeModePreference: () => "light-flat" as const,
  setThemeModePreference: vi.fn((m: string) => m),
  THEME_MODE_CHANGE_EVENT: "galtransl:theme-mode-change",
}));

vi.mock("../stores/confirmStore", () => ({
  confirm: { show: vi.fn() },
}));

vi.mock("@tauri-apps/plugin-dialog", () => ({
  open: vi.fn(),
}));

import { initProject, fetchWorkspaceRoot } from "../lib/api/project";
import { ensureDesktopBackendReady } from "../lib/api/client";

const PID = "projLoc";

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetchWorkspaceRoot).mockResolvedValue({ workspace_root: "D:/workspace" });
  vi.mocked(initProject).mockResolvedValue({
    project_id: PID,
    project_dir: "D:/workspace/TestProj",
    created: [],
    config_file_name: "config.yaml",
  });
  vi.mocked(ensureDesktopBackendReady).mockResolvedValue(undefined as never);
});

afterEach(() => {
  vi.restoreAllMocks();
});

/** 渲染向导并填入项目名称，返回当前渲染环境 */
async function renderWithName(name = "TestProj") {
  render(() => <NewProjectWizard />);
  const nameInput = document.querySelector<HTMLInputElement>(
    ".wizard-panel input.field__input",
  );
  fireEvent.input(nameInput!, { target: { value: name } });
  // 等待 workspaceRoot 拉取完成（预览依赖）
  await vi.waitFor(() => {
    expect(document.querySelector(".wizard-path-preview__path")?.textContent).toContain(name);
  });
}

/** 位置 radio：0 = 应用程序目录，1 = 自定义位置 */
function locationRadio(index: number): HTMLInputElement {
  const radios = document.querySelectorAll<HTMLInputElement>(
    'input[name="wizard-project-location"]',
  );
  expect(radios.length, "应有 2 个位置 radio").toBe(2);
  return radios[index];
}

function findCreateButton(): HTMLButtonElement {
  const btn = Array.from(
    document.querySelectorAll<HTMLButtonElement>(".wizard-actions button"),
  ).find((b) => b.textContent?.includes("创建项目"));
  expect(btn, "找不到创建项目按钮").toBeTruthy();
  return btn!;
}

describe("NewProjectWizard 自定义创建位置", () => {
  it("默认位置创建不透传 parent_dir（第三参 undefined）", async () => {
    await renderWithName();
    fireEvent.click(findCreateButton());
    await vi.waitFor(() => {
      expect(vi.mocked(initProject)).toHaveBeenCalledWith("TestProj", false, undefined);
    });
  });

  it("自定义位置父目录为空时创建按钮禁用，不发起请求", async () => {
    await renderWithName();
    fireEvent.click(locationRadio(1));
    const btn = findCreateButton();
    expect(btn.disabled).toBe(true);
    fireEvent.click(btn);
    expect(vi.mocked(initProject)).not.toHaveBeenCalled();
  });

  it("输入父目录后创建 → initProject 透传 parent_dir，预览按自定义位置拼接", async () => {
    await renderWithName();
    fireEvent.click(locationRadio(1));
    const dirInput = document.querySelector<HTMLInputElement>(
      ".wizard-parent-dir-row input.field__input",
    );
    expect(dirInput, "自定义位置应显示父目录输入框").toBeTruthy();
    fireEvent.input(dirInput!, { target: { value: "D:/custom" } });
    await vi.waitFor(() => {
      expect(document.querySelector(".wizard-path-preview__path")?.textContent).toBe(
        "D:/custom/TestProj",
      );
    });
    const btn = findCreateButton();
    expect(btn.disabled).toBe(false);
    fireEvent.click(btn);
    await vi.waitFor(() => {
      expect(vi.mocked(initProject)).toHaveBeenCalledWith("TestProj", false, {
        parent_dir: "D:/custom",
      });
    });
  });

  it("创建成功后切换位置 → 已创建结果失效，预览回落实时 previewDir，可重新创建", async () => {
    await renderWithName();
    fireEvent.click(locationRadio(1));
    const dirInput = document.querySelector<HTMLInputElement>(
      ".wizard-parent-dir-row input.field__input",
    )!;
    fireEvent.input(dirInput, { target: { value: "D:/custom" } });
    vi.mocked(initProject).mockResolvedValue({
      project_id: PID,
      project_dir: "D:/custom/TestProj",
      created: [],
      config_file_name: "config.yaml",
    });
    fireEvent.click(findCreateButton());
    await vi.waitFor(() => {
      expect(document.querySelector(".wizard-actions button")?.textContent).toContain("已创建");
    });
    // 预览显示实际创建位置
    expect(document.querySelector(".wizard-path-preview__path")?.textContent).toBe(
      "D:/custom/TestProj",
    );
    // 切回默认位置：旧落点被清空，预览回落 workspace 预览
    fireEvent.click(locationRadio(0));
    expect(document.querySelector(".wizard-path-preview__path")?.textContent).toBe(
      "D:/workspace/TestProj",
    );
    const btn = findCreateButton();
    expect(btn.disabled).toBe(false);
    fireEvent.click(btn);
    await vi.waitFor(() => {
      expect(vi.mocked(initProject)).toHaveBeenLastCalledWith("TestProj", false, undefined);
    });
  });

  it("Web 模式（无 Tauri）自定义位置不显示浏览按钮，仅手输路径", async () => {
    await renderWithName();
    fireEvent.click(locationRadio(1));
    const browseBtn = Array.from(document.querySelectorAll(".wizard-parent-dir-row button")).find(
      (b) => b.textContent?.includes("浏览"),
    );
    expect(browseBtn, "jsdom 环境不应显示浏览按钮").toBeUndefined();
  });
});
