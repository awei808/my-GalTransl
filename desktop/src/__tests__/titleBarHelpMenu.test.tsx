/**
 * 标题栏帮助菜单测试：三个菜单项的行为（指南跳转 / 外链 / 关于）。
 *
 * mock 全部 Tauri 与 API 依赖，仅验证菜单交互本身。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, cleanup, fireEvent } from "@solidjs/testing-library";
import { TitleBar } from "../components/TitleBar";
import { openExternal } from "../lib/openExternal";
import { appState } from "../stores/appStore";

vi.mock("@tauri-apps/plugin-dialog", () => ({ open: vi.fn() }));
vi.mock("@tauri-apps/api/webviewWindow", () => ({
  getCurrentWebviewWindow: vi.fn(() => ({ close: vi.fn() })),
}));
vi.mock("../lib/api/client", () => ({
  ensureDesktopBackendReady: vi.fn(),
  encodeProjectDir: vi.fn((dir: string) => btoa(encodeURIComponent(dir))),
  isBackendReachable: vi.fn(async () => true),
}));
vi.mock("../lib/api/project", () => ({ fetchProjectFiles: vi.fn(async () => []) }));
vi.mock("../lib/globalSave", () => ({ invokeGlobalSave: vi.fn() }));
vi.mock("../lib/openExternal", () => ({ openExternal: vi.fn() }));
vi.mock("../components/McpStatusLight", () => ({ McpStatusLight: () => <i /> }));

const mockedOpenExternal = vi.mocked(openExternal);

function menuButtonByLabel(container: HTMLElement, label: string) {
  return [...container.querySelectorAll(".titlebar-menuitem")].find(
    (el) => el.textContent === label,
  ) as HTMLElement | undefined;
}

function dropdownItem(container: HTMLElement, label: string) {
  return [...container.querySelectorAll(".titlebar-dropdown-item")].find(
    (el) => el.textContent?.trim() === label,
  ) as HTMLElement | undefined;
}

describe("标题栏帮助菜单", () => {
  beforeEach(() => {
    mockedOpenExternal.mockClear();
  });

  afterEach(() => {
    cleanup();
  });

  it("「翻译指南」打开指南页", () => {
    const { container } = render(() => <TitleBar />);
    fireEvent.click(menuButtonByLabel(container, "帮助")!);
    fireEvent.click(dropdownItem(container, "翻译指南")!);
    expect(appState.activeView).toBe("guide");
  });

  it("「关于 GalTransl」打开指南页的关于篇目", () => {
    const { container } = render(() => <TitleBar />);
    fireEvent.click(menuButtonByLabel(container, "帮助")!);
    fireEvent.click(dropdownItem(container, "关于 GalTransl")!);
    expect(appState.activeView).toBe("guide");
    expect(appState.guideTarget).toBe("00-about.md");
  });

  it("「项目地址」走系统浏览器外链", () => {
    const { container } = render(() => <TitleBar />);
    fireEvent.click(menuButtonByLabel(container, "帮助")!);
    fireEvent.click(dropdownItem(container, "项目地址")!);
    expect(mockedOpenExternal).toHaveBeenCalledWith("https://github.com/awei808/my-GalTransl");
  });
});
