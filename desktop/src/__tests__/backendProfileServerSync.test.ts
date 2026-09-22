/**
 * 全局后端配置的「服务端同步」测试（0.5.1）。
 *
 * 历史缺口：前端只写 localStorage，后端的 backend_profiles.yaml 从无生产者，
 * 于是 common.stageBackends 按名引用一律报「配置不存在（当前可用：无）」。
 * 本测试锁定：保存/删除会同步到服务端；首次补齐只写服务端缺失的项。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../lib/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api/client")>();
  return { ...actual, apiRequest: vi.fn() };
});
vi.mock("../lib/api/preferences", () => ({
  fetchBackendProfiles: vi.fn(),
}));

import { apiRequest } from "../lib/api/client";
import { fetchBackendProfiles } from "../lib/api/preferences";
import {
  deleteBackendProfileOnServer,
  putBackendProfileToServer,
  syncLocalBackendProfilesToServer,
} from "../lib/api/backendProfiles";

const apiRequestMock = apiRequest as unknown as ReturnType<typeof vi.fn>;
const fetchLocalMock = fetchBackendProfiles as unknown as ReturnType<typeof vi.fn>;

describe("后端配置的服务端同步", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiRequestMock.mockResolvedValue({});
  });

  it("保存时 PUT 到 /api/backend-profiles/:name（名字需 URL 编码）", async () => {
    const profile = { "OpenAI-Compatible": { tokens: [] } };
    await putBackendProfileToServer("后端 A", profile);
    expect(apiRequest).toHaveBeenCalledWith(
      `/api/backend-profiles/${encodeURIComponent("后端 A")}`,
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ profile }),
      }),
    );
  });

  it("删除时 DELETE 同名端点", async () => {
    await deleteBackendProfileOnServer("后端A");
    expect(apiRequest).toHaveBeenCalledWith(
      `/api/backend-profiles/${encodeURIComponent("后端A")}`,
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("首次补齐只写服务端缺失的配置", async () => {
    fetchLocalMock.mockResolvedValue({
      profiles: { A: { "OpenAI-Compatible": {} }, B: { "OpenAI-Compatible": {} } },
    });
    apiRequestMock.mockImplementation(async (path: string, init?: { method?: string }) => {
      if (path === "/api/backend-profiles" && !init?.method) {
        return { profiles: { A: { "OpenAI-Compatible": {} } } };
      }
      return {};
    });

    const result = await syncLocalBackendProfilesToServer();

    expect(result).toEqual({ synced: ["B"], failed: [] });
    const putCalls = apiRequestMock.mock.calls.filter(
      ([, init]) => (init as { method?: string } | undefined)?.method === "PUT",
    );
    expect(putCalls).toHaveLength(1);
    expect(putCalls[0][0]).toBe(`/api/backend-profiles/${encodeURIComponent("B")}`);
  });

  it("后端不可达时跳过补齐，不抛错也不发 PUT", async () => {
    fetchLocalMock.mockResolvedValue({ profiles: { A: {} } });
    apiRequestMock.mockRejectedValue(new Error("无法连接到后端"));

    const result = await syncLocalBackendProfilesToServer();

    expect(result).toEqual({ synced: [], failed: [] });
    expect(
      apiRequestMock.mock.calls.filter(
        ([, init]) => (init as { method?: string } | undefined)?.method === "PUT",
      ),
    ).toHaveLength(0);
  });

  it("单项失败记入 failed，不影响其余项", async () => {
    fetchLocalMock.mockResolvedValue({ profiles: { A: {}, B: {} } });
    apiRequestMock.mockImplementation(async (path: string, init?: { method?: string }) => {
      if (path === "/api/backend-profiles" && !init?.method) return { profiles: {} };
      if (path.includes(encodeURIComponent("A"))) throw new Error("500");
      return {};
    });

    const result = await syncLocalBackendProfilesToServer();

    expect(result).toEqual({ synced: ["B"], failed: ["A"] });
  });
});
