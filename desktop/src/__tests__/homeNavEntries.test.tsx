/**
 * 首页「新建项目向导」入口与 TitleBar「回到首页」按钮验证
 *
 * 覆盖：
 * - 首页欢迎区 CTA 点击 → 切到 new-project 视图且侧栏收起
 * - TitleBar「帮助」后存在「回到首页」，点击 → 切到 home 视图
 * - 下拉菜单展开时点击「回到首页」→ 下拉关闭且完成跳转（防悬留）
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, fireEvent } from "@solidjs/testing-library";
import { HomePage } from "../pages/home/HomePage";
import { TitleBar } from "../components/TitleBar";
import { appState, setAppState } from "../stores/appStore";

vi.mock("../lib/api/general", () => ({
  fetchVersion: vi.fn().mockResolvedValue({ version: "0.4.2" }),
  fetchJobs: vi.fn().mockResolvedValue([]),
}));

vi.mock("../lib/api/client", () => ({
  ensureDesktopBackendReady: vi.fn().mockResolvedValue(undefined),
  encodeProjectDir: vi.fn((dir: string) => `enc:${dir}`),
  isBackendReachable: vi.fn().mockResolvedValue(true),
}));

vi.mock("../lib/api/project", () => ({
  fetchProjectFiles: vi.fn().mockResolvedValue([]),
}));

// toastStore → logStore 的日志上报依赖 client 的 apiRequest；直接 no-op sendLog
vi.mock("../lib/api/log", () => ({
  sendLog: vi.fn(),
}));

vi.mock("@tauri-apps/plugin-dialog", () => ({
  open: vi.fn(),
}));

vi.mock("@tauri-apps/api/webviewWindow", () => ({
  getCurrentWebviewWindow: vi.fn(() => ({ close: vi.fn() })),
}));

afterEach(() => {
  vi.restoreAllMocks();
});

describe("首页「新建项目向导」入口", () => {
  beforeEach(() => {
    setAppState({ activeView: "home", sidebarOpen: true });
  });

  it("欢迎区渲染 CTA 按钮，点击 → 切到 new-project 且侧栏收起", () => {
    render(() => <HomePage />);
    const cta = Array.from(document.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("新建项目向导"),
    );
    expect(cta, "首页应存在「新建项目向导」按钮").toBeTruthy();
    fireEvent.click(cta!);
    expect(appState.activeView).toBe("new-project");
    expect(appState.sidebarOpen).toBe(false);
  });
});

describe("TitleBar「回到首页」按钮", () => {
  beforeEach(() => {
    setAppState({ activeView: "translate", sidebarOpen: true });
  });

  function findHomeButton(): Element {
    const btn = Array.from(document.querySelectorAll(".titlebar-menu > .titlebar-menuitem")).find(
      (el) => el.textContent?.includes("回到首页"),
    );
    expect(btn, "TitleBar 应存在「回到首页」按钮").toBeTruthy();
    return btn!;
  }

  it("「回到首页」位于「帮助」之后，点击 → 切到 home", () => {
    render(() => <TitleBar />);
    const nav = document.querySelector(".titlebar-menu")!;
    const texts = Array.from(nav.children).map((el) => el.textContent!.trim());
    expect(texts[texts.length - 2]).toContain("帮助");
    expect(texts[texts.length - 1]).toBe("回到首页");
    fireEvent.click(findHomeButton());
    expect(appState.activeView).toBe("home");
  });

  it("下拉菜单展开时点击「回到首页」→ 下拉关闭且跳转", () => {
    render(() => <TitleBar />);
    const fileMenu = document.querySelector<HTMLElement>(".titlebar-menuitem")!;
    fireEvent.click(fileMenu);
    expect(document.querySelector(".titlebar-dropdown"), "点击后应展开文件菜单").toBeTruthy();
    fireEvent.click(findHomeButton());
    expect(appState.activeView).toBe("home");
    expect(document.querySelector(".titlebar-dropdown"), "跳转后下拉不应悬留").toBeNull();
  });
});
