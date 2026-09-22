/**
 * 应用整体布局：侧栏列只在校对审核视图渲染
 *
 * 回归背景：侧栏渲染口径曾只排除 translate，导致从校对审核切到首页 / 日志 /
 * 项目配置等整页视图时残留已展开的侧栏（文件浏览器 / 查找 / 问题面板）。
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, cleanup } from "@solidjs/testing-library";
import { App } from "../App";
import { setAppState } from "../stores/appStore";

vi.mock("../lib/api/general", () => ({
  fetchVersion: vi.fn().mockResolvedValue({ version: "0.5.0" }),
  fetchJobs: vi.fn().mockResolvedValue([]),
  fetchProblemTypes: vi.fn().mockResolvedValue([]),
}));

vi.mock("../lib/api/client", () => ({
  ensureDesktopBackendReady: vi.fn().mockResolvedValue(undefined),
  encodeProjectDir: vi.fn((dir: string) => `enc:${dir}`),
  isBackendReachable: vi.fn().mockResolvedValue(true),
  getBackendBaseUrl: vi.fn(() => "http://127.0.0.1:12333"),
  apiRequest: vi.fn().mockResolvedValue({}),
}));

vi.mock("../lib/api/project", () => ({
  fetchProjectFiles: vi.fn().mockResolvedValue({ cache_files: [] }),
  fetchProjectProblems: vi.fn().mockResolvedValue({ problems: [] }),
  fetchProjectAltTranslations: vi.fn().mockResolvedValue({ alts: [] }),
  fetchProjectConfigName: vi.fn().mockResolvedValue("config.yaml"),
  validateBuild: vi.fn().mockResolvedValue({}),
  buildProjectOutput: vi.fn().mockResolvedValue({ total_built: 0 }),
}));

vi.mock("../lib/api/log", () => ({ sendLog: vi.fn() }));

vi.mock("@tauri-apps/plugin-dialog", () => ({ open: vi.fn(), save: vi.fn() }));

vi.mock("@tauri-apps/plugin-shell", () => ({ open: vi.fn() }));

vi.mock("@tauri-apps/api/webviewWindow", () => ({
  getCurrentWebviewWindow: vi.fn(() => ({ close: vi.fn() })),
}));

/** 在指定视图下渲染 App，返回是否存在侧栏列 */
function rendersSidebarColumn(activeView: "home" | "review" | "logs") {
  setAppState({ activeView });
  render(() => <App />);
  const hasColumn = document.querySelector(".sidebar-column") !== null;
  cleanup();
  return hasColumn;
}

describe("侧栏列渲染口径", () => {
  beforeEach(() => {
    // 前置条件：侧栏处于展开状态（模拟用户在校对页打开了文件浏览器）
    setAppState({ sidebarOpen: true, sidebarTab: "explorer", activeProjectId: null });
  });

  afterEach(() => {
    cleanup();
  });

  it("home / logs 不渲染侧栏列（即使 sidebarOpen 为 true）", () => {
    expect(rendersSidebarColumn("home"), "首页不应有侧栏列").toBe(false);
    expect(rendersSidebarColumn("logs"), "日志页不应有侧栏列").toBe(false);
  });

  it("review 渲染侧栏列", () => {
    expect(rendersSidebarColumn("review"), "校对审核应有侧栏列").toBe(true);
  });
});
