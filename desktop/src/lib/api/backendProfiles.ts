/**
 * 全局后端配置的**服务端**侧读写（`<程序目录>/backend_profiles.yaml`）。
 *
 * 「后端配置」页的编辑数据存在 localStorage（本地偏好），但后端解析
 * `common.stageBackends` 里的配置名时只认这个文件 —— 必须同步写过去，否则按名
 * 引用阶段后端一律报「引用的后端配置 X 不存在（当前可用：无）」。
 * 历史缺口：前端只写 localStorage，该文件从无生产者（0.5.1 修复）。
 */
import { apiRequest } from "./client";
import { fetchBackendProfiles } from "./preferences";
import type { BackendProfilesMap } from "./types";

export async function fetchServerBackendProfiles(): Promise<BackendProfilesMap> {
  const data = await apiRequest<{ profiles?: BackendProfilesMap }>("/api/backend-profiles");
  return data.profiles ?? {};
}

export async function putBackendProfileToServer(
  name: string,
  profile: Record<string, unknown>,
): Promise<void> {
  await apiRequest(`/api/backend-profiles/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profile }),
  });
}

export async function deleteBackendProfileOnServer(name: string): Promise<void> {
  await apiRequest(`/api/backend-profiles/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
}

/**
 * 首次补齐：把 localStorage 中存在、服务端尚未记录的配置逐个写入（幂等）。
 * 单项失败不阻断其余；后端不可达时直接跳过（页面其它请求会给出可见错误）。
 */
export async function syncLocalBackendProfilesToServer(): Promise<{
  synced: string[];
  failed: string[];
}> {
  const local = await fetchBackendProfiles();
  const localProfiles = (local.profiles || {}) as BackendProfilesMap;

  let remote: BackendProfilesMap;
  try {
    remote = await fetchServerBackendProfiles();
  } catch {
    return { synced: [], failed: [] };
  }

  const synced: string[] = [];
  const failed: string[] = [];
  for (const [name, profile] of Object.entries(localProfiles)) {
    if (remote[name]) continue;
    try {
      await putBackendProfileToServer(name, profile as Record<string, unknown>);
      synced.push(name);
    } catch {
      failed.push(name);
    }
  }
  return { synced, failed };
}
