/**
 * 模型可用性检测的超时口径测试。
 * 覆盖：探测接口必须带放宽后的 timeoutMs（慢模型下单次探活可达数十秒，
 * 沿用 apiRequest 默认 30s 会先被前端 abort，报「请求超时」假故障）；
 * 以及超时判定工具 isApiTimeoutError 的边界（供「超时不自动重试」使用）。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../lib/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api/client")>();
  return { ...actual, apiRequest: vi.fn() };
});

import { ApiError, apiRequest } from "../lib/api/client";
import { checkModelAvailability } from "../lib/api/general";
import { isApiTimeoutError } from "../lib/errors";

describe("checkModelAvailability", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (apiRequest as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
  });

  it("携带放宽后的 timeoutMs，避免慢模型假超时", async () => {
    await checkModelAvailability({ projectId: "p1", translator: "ForGal-full-pipeline" });
    expect(apiRequest).toHaveBeenCalledWith(
      "/api/projects/p1/check-model",
      expect.objectContaining({ timeoutMs: 90000 }),
    );
  });

  it("保持既有请求体字段口径（translator / config_file_name / profile）", async () => {
    await checkModelAvailability({
      projectId: "p2",
      translator: "ForGal-json-translate",
      configFileName: "config.inc.yaml",
      backendProfile: "后端A",
      backendProfileData: { "OpenAI-Compatible": {} },
    });
    const body = JSON.parse(
      (apiRequest as unknown as ReturnType<typeof vi.fn>).mock.calls[0][1].body as string,
    );
    expect(body).toEqual({
      translator: "ForGal-json-translate",
      config_file_name: "config.inc.yaml",
      backend_profile: "后端A",
      backend_profile_data: { "OpenAI-Compatible": {} },
    });
  });
});

describe("isApiTimeoutError", () => {
  it("408 判为超时", () => {
    expect(isApiTimeoutError(new ApiError("请求超时：http://x/check-model", 408))).toBe(true);
  });

  it("其它状态码与普通错误不算超时", () => {
    expect(isApiTimeoutError(new ApiError("请求失败：500", 500))).toBe(false);
    expect(isApiTimeoutError(new ApiError("无法连接到后端", 0))).toBe(false);
    expect(isApiTimeoutError(new Error("boom"))).toBe(false);
    expect(isApiTimeoutError(null)).toBe(false);
  });
});
