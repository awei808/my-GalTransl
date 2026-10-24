/**
 * 翻译控制台 TTFT 状态灯文字（badge 正文与 tab 圆点 hover title 共用 ttftText 口径）：
 *
 * - COMPLETED（请求成功完成）徽标显示「已完成 · 首字 Xms」；ttft_ms 缺失（非流式）只显示「已完成」；
 * - COMPLETED 圆点落 ttftColor 的 default 分支复用 --ttft-idle 色，深浅色由 tokens.css 变量承担；
 * - tab 圆点 hover title 覆盖各状态：COMPLETED / WAITING / 无条目（空闲）。
 *
 * 回归背景：成功分支原复位 IDLE，黑灯没有任何文字说明（TODO：需显示「已完成 + 耗时」）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, cleanup, fireEvent } from "@solidjs/testing-library";
import { TranslateConsole } from "../pages/translate/TranslateConsole";
import { setAppState } from "../stores/appStore";
import { fetchProjectRuntime } from "../lib/api/project";
import type { ProjectRuntimeResponse } from "../lib/api/types";

vi.mock("../lib/api/project", () => ({
  fetchProjectRuntime: vi.fn(),
  clearRuntimeNotices: vi.fn().mockResolvedValue(undefined),
  stopProjectTranslation: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("../lib/api/general", () => ({
  fetchTranslators: vi.fn().mockResolvedValue([]),
  submitJob: vi.fn().mockResolvedValue({}),
  checkModelAvailability: vi.fn().mockResolvedValue({ available: true }),
  checkBatchSize: vi.fn().mockResolvedValue({}),
  fetchInputStats: vi.fn().mockResolvedValue({}),
}));

vi.mock("../lib/api/client", () => ({
  decodeProjectDir: vi.fn((dir: string) => dir),
}));

vi.mock("../lib/api/preferences", () => ({
  resolveSelectedBackendProfile: vi.fn().mockResolvedValue(null),
  getSelectedBackendProfileJobPayload: vi.fn(() => ({})),
  getGitSuggestAcknowledged: vi.fn(() => true),
  setGitSuggestAcknowledged: vi.fn(),
}));

const runtimeFixture = {
  project_dir: "test",
  job: null,
  summary: {
    total: 10,
    translated: 5,
    percent: 50,
    failed: 0,
    problems: 0,
    translation_speed_lpm: 0,
    eta_seconds: null,
  },
  stage: "翻译执行",
  stage_index: 6,
  stage_total: 9,
  current_file: "a.json",
  current_batch: 1,
  batch_total: 2,
  latest_prompt_preview: "提示词预览",
  translation_previews: {},
  prompt_previews: {
    "0": { worker_id: "0", preview: "p0", filename: "a.json", batch: "1", updated_at: "" },
    "1": { worker_id: "1", preview: "p1", filename: "b.json", batch: "", updated_at: "" },
    "2": { worker_id: "2", preview: "p2", filename: "c.json", batch: "", updated_at: "" },
    "3": { worker_id: "3", preview: "p3", filename: "d.json", batch: "", updated_at: "" },
  },
  // worker 3 故意无条目：tab 圆点回退 IDLE（title「空闲」），badge 不渲染
  ttft_states: {
    "0": { worker_id: "0", status: "COMPLETED", ttft_ms: 1234, model: "m", updated_at: "" },
    "1": { worker_id: "1", status: "WAITING", ttft_ms: null, model: "m", updated_at: "" },
    "2": { worker_id: "2", status: "COMPLETED", ttft_ms: null, model: "", updated_at: "" },
  },
  recent_errors: [],
  recent_successes: [],
  notices: [],
} as unknown as ProjectRuntimeResponse;

/** 提示词面板 worker tab 的圆点（结果预览 tab 无圆点，据此唯一确定） */
function promptTabDots(): HTMLElement[] {
  return [...document.querySelectorAll(".panel-tabs--prompt .ttft-dot")] as HTMLElement[];
}

/** 点击第 N 个提示词 worker tab（经圆点反查按钮，避开同名的结果预览 tab） */
function clickPromptTab(index: number): void {
  const button = promptTabDots()[index].closest("button");
  expect(button).not.toBeNull();
  fireEvent.click(button!);
}

describe("TTFT 状态灯文字", () => {
  beforeEach(() => {
    vi.mocked(fetchProjectRuntime).mockResolvedValue(runtimeFixture);
    setAppState({ activeProjectId: "enc:test", prevJobStatus: "" });
    render(() => <TranslateConsole />);
  });

  afterEach(cleanup);

  it("COMPLETED 徽标显示已完成与首字耗时，圆点复用 idle 色", async () => {
    await vi.waitFor(() => {
      const badge = document.querySelector(".ttft-badge");
      expect(badge).not.toBeNull();
      expect(badge!.textContent).toContain("已完成 · 首字 1234ms");
    });
    const badgeDot = document.querySelector(".ttft-badge .ttft-dot") as HTMLElement;
    expect(badgeDot.style.background).toContain("--ttft-idle");
  });

  it("tab 圆点 hover title 覆盖各状态（含无条目的空闲）", async () => {
    await vi.waitFor(() => {
      expect(promptTabDots().length).toBe(4);
    });
    expect(promptTabDots().map((el) => el.getAttribute("title"))).toEqual([
      "已完成 · 首字 1234ms", // worker 0：COMPLETED + ttft_ms
      "等待首字…", // worker 1：WAITING
      "已完成", // worker 2：COMPLETED 无 ttft_ms（非流式）
      "空闲", // worker 3：无条目，回退 IDLE
    ]);
  });

  it("COMPLETED 无 ttft_ms 的徽标只显示已完成", async () => {
    await vi.waitFor(() => {
      expect(promptTabDots().length).toBe(4);
    });
    clickPromptTab(2);
    await vi.waitFor(() => {
      const badge = document.querySelector(".ttft-badge");
      expect(badge).not.toBeNull();
      expect(badge!.textContent!.trim()).toBe("已完成");
    });
  });

  it("无条目 worker 不渲染徽标", async () => {
    await vi.waitFor(() => {
      expect(promptTabDots().length).toBe(4);
    });
    clickPromptTab(3);
    await vi.waitFor(() => {
      // 已切到 worker 3 的快照，但无 TTFT 条目时徽标整体不渲染
      expect(document.querySelector(".prompt-worker-meta")?.textContent).toContain("d.json");
      expect(document.querySelector(".ttft-badge")).toBeNull();
    });
  });
});
