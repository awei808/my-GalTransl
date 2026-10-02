/**
 * 设置页 MCP 门禁区块：H 门禁开关与 MCP 工具开关的渲染与写回。
 *
 * 仅 mock API 层（general / project），验证乐观更新 + 串行保存把完整设置
 * 提交到 PUT /api/app-settings，以及工具清单按只读/写入分组渲染。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, cleanup, fireEvent, waitFor } from "@solidjs/testing-library";
import { SettingsPage } from "../pages/settings/SettingsPage";
import { fetchAppSettings, updateAppSettings, fetchMcpTools } from "../lib/api/general";
import type { AppSettings, McpToolInfo } from "../lib/api/types";

vi.mock("../lib/api/general", () => ({
  fetchVersion: vi.fn().mockResolvedValue({ version: "0.6.0", author: "awei808" }),
  fetchVersionCheck: vi.fn().mockResolvedValue({ version: "0.6.0", update_available: false }),
  fetchAppSettings: vi.fn(),
  updateAppSettings: vi.fn(),
  fetchMcpTools: vi.fn(),
}));
vi.mock("../lib/api/project", () => ({
  fetchProjectConfig: vi.fn().mockResolvedValue({ config: {} }),
  updateProjectConfig: vi.fn(),
}));

const mockedFetchSettings = vi.mocked(fetchAppSettings);
const mockedUpdateSettings = vi.mocked(updateAppSettings);
const mockedFetchTools = vi.mocked(fetchMcpTools);

const TOOLS: McpToolInfo[] = [
  { name: "galtransl_search_cache", description: "检索项目翻译缓存", kind: "read" },
  { name: "galtransl_submit_job", description: "提交翻译任务", kind: "write" },
];

function defaultSettings(): AppSettings {
  return {
    printTranslationLogInTerminal: true,
    mcpHGateEnabled: true,
    mcpDisabledTools: [],
  };
}

function gateField(container: HTMLElement) {
  return [...container.querySelectorAll(".settings-field")].find((el) =>
    el.textContent?.includes("MCP H 门禁"),
  );
}

function toolRowByName(container: HTMLElement, name: string) {
  return [...container.querySelectorAll(".mcp-tool-row")].find((el) =>
    el.textContent?.includes(name),
  );
}

describe("设置页 MCP 门禁区块", () => {
  beforeEach(() => {
    mockedFetchSettings.mockReset();
    mockedUpdateSettings.mockReset();
    mockedFetchTools.mockReset();
    mockedFetchSettings.mockResolvedValue(defaultSettings());
    mockedUpdateSettings.mockImplementation((s) => Promise.resolve(s));
    mockedFetchTools.mockResolvedValue(TOOLS);
  });

  afterEach(() => {
    cleanup();
  });

  it("工具清单按只读/写入分组渲染，开关状态反映禁用黑名单", async () => {
    mockedFetchSettings.mockResolvedValue({
      ...defaultSettings(),
      mcpHGateEnabled: false,
      mcpDisabledTools: ["galtransl_submit_job"],
    });
    const { container } = render(() => <SettingsPage />);
    await waitFor(() => {
      expect(container.querySelectorAll(".mcp-tool-row").length).toBe(2);
    });
    const groupTitles = [...container.querySelectorAll(".mcp-tool-group-title")].map((el) =>
      el.textContent,
    );
    expect(groupTitles).toEqual(["只读检索（1 个）", "写入（1 个）"]);

    // 禁用清单中的工具开关为关，未列入的为开
    const cacheInput = toolRowByName(container, "galtransl_search_cache")!.querySelector(
      "input[type=checkbox]",
    ) as HTMLInputElement;
    const submitInput = toolRowByName(container, "galtransl_submit_job")!.querySelector(
      "input[type=checkbox]",
    ) as HTMLInputElement;
    expect(cacheInput.checked).toBe(true);
    expect(submitInput.checked).toBe(false);

    // H 门禁开关反映 mcpHGateEnabled=false
    const gateInput = gateField(container)!.querySelector(
      "input[type=checkbox]",
    ) as HTMLInputElement;
    expect(gateInput.checked).toBe(false);
  });

  it("点击工具开关：乐观更新 UI，并把合并后的完整设置提交保存", async () => {
    mockedFetchSettings.mockResolvedValue({
      ...defaultSettings(),
      mcpHGateEnabled: false,
      mcpDisabledTools: ["galtransl_submit_job"],
    });
    const { container } = render(() => <SettingsPage />);
    await waitFor(() => {
      expect(container.querySelectorAll(".mcp-tool-row").length).toBe(2);
    });

    const submitInput = toolRowByName(container, "galtransl_submit_job")!.querySelector(
      "input[type=checkbox]",
    ) as HTMLInputElement;
    fireEvent.click(submitInput);

    await waitFor(() => {
      expect(mockedUpdateSettings).toHaveBeenCalled();
    });
    const payload = mockedUpdateSettings.mock.calls[0][0];
    // 重新启用 submit_job 后黑名单清空；其余字段来自服务端现状（read-modify-write）
    expect(payload.mcpDisabledTools).toEqual([]);
    expect(payload.mcpHGateEnabled).toBe(false);

    // 开关状态乐观更新，不等待保存完成
    await waitFor(() => {
      expect(submitInput.checked).toBe(true);
    });
  });

  it("切换 H 门禁开关并保存", async () => {
    const { container } = render(() => <SettingsPage />);
    await waitFor(() => {
      expect(container.querySelectorAll(".mcp-tool-row").length).toBe(2);
    });

    const gateInput = gateField(container)!.querySelector(
      "input[type=checkbox]",
    ) as HTMLInputElement;
    expect(gateInput.checked).toBe(true);
    fireEvent.click(gateInput);

    await waitFor(() => {
      expect(mockedUpdateSettings).toHaveBeenCalled();
    });
    const payload = mockedUpdateSettings.mock.calls[0][0];
    expect(payload.mcpHGateEnabled).toBe(false);
  });

  it("全部启用按钮：有禁用项时可点、点击后清空黑名单", async () => {
    mockedFetchSettings.mockResolvedValue({
      ...defaultSettings(),
      mcpDisabledTools: ["galtransl_search_cache", "galtransl_submit_job"],
    });
    const { container } = render(() => <SettingsPage />);
    await waitFor(() => {
      expect(container.querySelectorAll(".mcp-tool-row").length).toBe(2);
    });

    const button = [...container.querySelectorAll(".mcp-tool-toolbar button")].find(
      (el) => el.textContent === "全部启用",
    ) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    fireEvent.click(button);

    await waitFor(() => {
      expect(mockedUpdateSettings).toHaveBeenCalled();
    });
    const payload = mockedUpdateSettings.mock.calls[0][0];
    expect(payload.mcpDisabledTools).toEqual([]);
  });

  it("保存失败：展示错误并回读服务端纠偏本地状态", async () => {
    const { container } = render(() => <SettingsPage />);
    await waitFor(() => {
      expect(container.querySelectorAll(".mcp-tool-row").length).toBe(2);
    });

    mockedUpdateSettings.mockRejectedValueOnce(new Error("后端不可达"));
    const gateInput = gateField(container)!.querySelector(
      "input[type=checkbox]",
    ) as HTMLInputElement;
    fireEvent.click(gateInput);
    // 乐观更新先变为关
    await waitFor(() => {
      expect(gateInput.checked).toBe(false);
    });
    // 保存失败后回读服务端（fetchAppSettings 仍返回 gate=true），本地纠偏回开并提示错误
    await waitFor(() => {
      const hasError = [...container.querySelectorAll(".settings-error")].some((el) =>
        el.textContent?.includes("失败"),
      );
      expect(hasError).toBe(true);
    });
    await waitFor(() => {
      expect(gateInput.checked).toBe(true);
    });
  });
});
