/**
 * generateNameTable config query 拼接测试（M26）
 * 覆盖：config.inc.yaml 项目提交 dump-name 任务时显式携带真实配置名，
 * 避免后端默认 config.yaml 读错配置。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../lib/api/client", () => ({ apiRequest: vi.fn(), getBackendBaseUrl: vi.fn() }));

import { apiRequest } from "../lib/api/client";
import { generateNameTable } from "../lib/api/project";

describe("generateNameTable", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (apiRequest as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ success: true, job_id: "abc" });
  });

  it("无 configFileName 时不拼 query（兼容默认 config.yaml 项目）", async () => {
    await generateNameTable("pid123");
    expect(apiRequest).toHaveBeenCalledWith(
      "/api/projects/pid123/name-table/generate",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("带 configFileName 时拼接 ?config= query", async () => {
    await generateNameTable("pid123", "config.inc.yaml");
    expect(apiRequest).toHaveBeenCalledWith(
      "/api/projects/pid123/name-table/generate?config=config.inc.yaml",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("configFileName 含特殊字符时 URL 编码", async () => {
    await generateNameTable("pid123", "my config & v1.yaml");
    expect(apiRequest).toHaveBeenCalledWith(
      "/api/projects/pid123/name-table/generate?config=my%20config%20%26%20v1.yaml",
      expect.anything(),
    );
  });
});
