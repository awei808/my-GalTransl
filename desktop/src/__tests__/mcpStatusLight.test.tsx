/**
 * MCP 连接指示灯：绿/灰状态渲染与轮询行为。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, cleanup } from "@solidjs/testing-library";
import { McpStatusLight } from "../components/McpStatusLight";
import { fetchMcpStatus, type McpStatus } from "../lib/api/mcp";

vi.mock("../lib/api/mcp", () => ({ fetchMcpStatus: vi.fn() }));

const mockedFetch = vi.mocked(fetchMcpStatus);

function status(overrides: Partial<McpStatus> = {}): McpStatus {
  return {
    available: true,
    path: "D:\\x\\mcp_status.json",
    pid: 1234,
    tools: 11,
    started_at: "2026-09-22T15:00:00+00:00",
    updated_at: "2026-09-22T15:00:00+00:00",
    age_seconds: 1.2,
    stale_after_seconds: 90,
    ...overrides,
  };
}

function lightElement(container: HTMLElement) {
  return container.querySelector(".mcp-status-light") as HTMLElement | null;
}

describe("MCP 连接指示灯", () => {
  beforeEach(() => {
    mockedFetch.mockReset();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("心跳新鲜时点亮绿灯并展示工具数", async () => {
    mockedFetch.mockResolvedValue(status());
    const { container } = render(() => <McpStatusLight />);
    await vi.waitFor(() => {
      expect(lightElement(container)?.classList.contains("online")).toBe(true);
    });
    const el = lightElement(container)!;
    expect(el.classList.contains("offline")).toBe(false);
    expect(el.getAttribute("aria-label")).toContain("已连接");
    expect(el.getAttribute("aria-label")).toContain("11");
    expect(el.getAttribute("role")).toBe("status");
  });

  it("心跳过期时保持灰灯", async () => {
    mockedFetch.mockResolvedValue(status({ available: false, age_seconds: 300 }));
    const { container } = render(() => <McpStatusLight />);
    await vi.waitFor(() => {
      expect(mockedFetch).toHaveBeenCalled();
    });
    const el = lightElement(container)!;
    expect(el.classList.contains("online")).toBe(false);
    expect(el.classList.contains("offline")).toBe(true);
    expect(el.getAttribute("aria-label")).toContain("未连接");
  });

  it("接口异常时按未连接处理且不抛错", async () => {
    mockedFetch.mockRejectedValue(new Error("后端不可达"));
    const { container } = render(() => <McpStatusLight />);
    await vi.waitFor(() => {
      expect(mockedFetch).toHaveBeenCalled();
    });
    const el = lightElement(container)!;
    expect(el.classList.contains("offline")).toBe(true);
  });

  it("挂载后立即拉取一次", () => {
    mockedFetch.mockResolvedValue(status());
    render(() => <McpStatusLight />);
    expect(mockedFetch).toHaveBeenCalledTimes(1);
  });

  it("按 10s 间隔持续轮询", async () => {
    vi.useFakeTimers();
    mockedFetch.mockResolvedValue(status());
    render(() => <McpStatusLight />);
    const afterMount = mockedFetch.mock.calls.length;
    await vi.advanceTimersByTimeAsync(10000);
    expect(mockedFetch.mock.calls.length).toBeGreaterThan(afterMount);
    await vi.advanceTimersByTimeAsync(10000);
    expect(mockedFetch.mock.calls.length).toBeGreaterThan(afterMount + 1);
    vi.useRealTimers();
  });

  it("卸载后停止轮询", async () => {
    vi.useFakeTimers();
    mockedFetch.mockResolvedValue(status());
    const { unmount } = render(() => <McpStatusLight />);
    unmount();
    const callsAtUnmount = mockedFetch.mock.calls.length;
    await vi.advanceTimersByTimeAsync(30000);
    expect(mockedFetch.mock.calls.length).toBe(callsAtUnmount);
    vi.useRealTimers();
  });
});
