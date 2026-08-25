/**
 * NewProjectWizard 显式保存按钮验证
 *
 * 覆盖：进入设置步骤（第 4/5 步）后导航区出现「保存设置」按钮，
 * 点击后经 handleSaveSettings 落盘（updateProjectConfig）并反馈「设置已保存」。
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
  vi.mocked(fetchTranslationGuidelines).mockResolvedValue(["日译中_增强", "通用"]);
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

/** 创建项目并连续「下一步」进入第 4 步（常用设置） */
async function goToSettingsStep() {
  render(() => <NewProjectWizard />);
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
}

describe("NewProjectWizard 显式保存按钮", () => {
  it("进入设置步骤后出现「保存设置」按钮，点击 → 落盘并反馈「设置已保存」", async () => {
    await goToSettingsStep();
    const saveBtn = document.querySelector<HTMLButtonElement>(
      'button[title="保存当前设置步骤的修改"]',
    );
    expect(saveBtn, "第 4 步应显示保存设置按钮").toBeTruthy();
    expect(saveBtn!.textContent).toContain("保存设置");
    fireEvent.click(saveBtn!);
    await vi.waitFor(() => {
      expect(vi.mocked(updateProjectConfig)).toHaveBeenCalled();
    });
    expect(vi.mocked(updateProjectConfig)).toHaveBeenCalledWith(
      PID,
      expect.objectContaining({ config_file_name: "config.yaml" }),
    );
    await vi.waitFor(() => {
      expect(document.querySelector(".wizard-feedback")?.textContent).toContain("设置已保存");
    });
  });

  it("创建项目前的第 1 步不显示保存设置按钮", async () => {
    render(() => <NewProjectWizard />);
    const saveBtn = document.querySelector<HTMLButtonElement>(
      'button[title="保存当前设置步骤的修改"]',
    );
    expect(saveBtn).toBeNull();
  });

  it("阶段全量启用时保存 → 覆盖磁盘旧 enableXxx=false 残留", async () => {
    // 磁盘残留：enableTranslate=false（历史禁用写入），用户当前全量勾选（默认全开）
    vi.mocked(fetchProjectConfig).mockResolvedValue({
      config: {
        common: {},
        gpt: undefined,
        plugin: undefined,
        externals: undefined,
        internals: { pipeline: { enableTranslate: false } },
      },
    });
    await goToSettingsStep();
    fireEvent.click(
      document.querySelector<HTMLButtonElement>('button[title="保存当前设置步骤的修改"]')!,
    );
    await vi.waitFor(() => {
      expect(vi.mocked(updateProjectConfig)).toHaveBeenCalled();
    });
    const [, payload] = vi.mocked(updateProjectConfig).mock.calls[0];
    const pipeline = (
      payload.config.internals as Record<string, unknown>
    ).pipeline as Record<string, boolean>;
    expect(pipeline.enableTranslate).toBe(true);
  });

  it("第 4 步离开时保存失败 → 阻断前进，停留当前步骤", async () => {
    await goToSettingsStep();
    vi.mocked(updateProjectConfig).mockRejectedValue(new Error("网络中断"));
    clickNavButton("下一步");
    await vi.waitFor(() => {
      expect(document.querySelector(".wizard-feedback")?.textContent).toContain("保存失败");
    });
    expect(document.querySelector(".wizard-panel-title")?.textContent).toContain("常用设置");
    expect(document.querySelector(".wizard-panel-title")?.textContent).not.toContain("流水线");
  });
});
