/**
 * 输入目录体积统计预检的请求口径测试。
 * 覆盖：fetchInputStats 使用 GET 且路径正确（后端据此返回 suggest_git，
 * 前端在启动翻译前弹「建议使用 git 管理」确认框）。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../lib/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api/client")>();
  return { ...actual, apiRequest: vi.fn() };
});

import { apiRequest } from "../lib/api/client";
import { fetchInputStats } from "../lib/api/general";

describe("fetchInputStats", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (apiRequest as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      input_dir: "D:/proj/gt_input",
      total_bytes: 2097152,
      file_count: 12,
      threshold_bytes: 1048576,
      suggest_git: true,
    });
  });

  it("使用 GET 请求正确的端点路径", async () => {
    await fetchInputStats("proj1");
    expect(apiRequest).toHaveBeenCalledWith("/api/projects/proj1/input-stats");
  });

  it("透传后端返回的统计与建议字段", async () => {
    const stats = await fetchInputStats("proj2");
    expect(stats.suggest_git).toBe(true);
    expect(stats.total_bytes).toBe(2097152);
    expect(stats.threshold_bytes).toBe(1048576);
  });
});
