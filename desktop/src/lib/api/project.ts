/**
 * Project API — all project-specific HTTP API calls.
 */
import { apiRequest, getBackendBaseUrl } from "./client";
import type {
  CacheEntry,
  CacheFileResponse,
  CacheReplaceField,
  CacheReplaceResponse,
  CacheSearchField,
  CacheSearchResponse,
  DictionaryCategory,
  FileEntry,
  NameDictResponse,
  NameEntry,
  NameTableGenerateResponse,
  NameTableResponse,
  NameTableSaveResponse,
  ProblemEntry,
  ProjectConfigResponse,
  ProjectConfigUpdatePayload,
  ConfigSchemaResponse,
  BuildOutputResponse,
  ProjectDictionaryManagerResponse,
  ProjectDictionaryResponse,
  ProjectFilesResponse,
  ProjectLogsResponse,
  ProjectProblemsResponse,
  CacheCheckResponse,
  ProjectProgressResponse,
  ProjectRuntimeResponse,
  MetadataEntry,
  MetadataType,
  PerFileMetadataResponse,
  StopProjectResponse,
  CommonDictionaryManagerResponse,
} from "./types";

// ---- Project config ----

export async function fetchProjectConfig(projectId: string, configFileName = "config.yaml") {
  return apiRequest<ProjectConfigResponse>(
    `/api/projects/${projectId}/config?config=${encodeURIComponent(configFileName)}`,
  );
}

/** 在服务端 workspace 根下按名称创建项目；已存在且 overwrite=false 时回 409。 */
export async function initProject(name: string, overwrite = false) {
  return apiRequest<{
    project_id: string;
    project_dir: string;
    created: string[];
    config_file_name: string;
  }>("/api/projects/init", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, overwrite }),
  });
}

/** 将用户选中的本地源路径交给后端读取并导入 gt_input（所有磁盘 IO 走后端）。 */
export async function importProjectFiles(projectId: string, sourcePaths: string[]) {
  return apiRequest<{ imported: string[]; skipped: string[] }>(
    `/api/projects/${projectId}/import`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_paths: sourcePaths }),
    },
  );
}

export async function updateProjectConfig(projectId: string, payload: ProjectConfigUpdatePayload) {
  return apiRequest<{ success: boolean; project_dir: string; config_file_name: string }>(
    `/api/projects/${projectId}/config`,
    {
      body: JSON.stringify(payload),
      headers: { "Content-Type": "application/json" },
      method: "PUT",
    },
  );
}

/** 获取项目配置的参数路径→注释描述映射（用于设置界面显示参数解释） */
export async function fetchConfigSchema(projectId: string) {
  return apiRequest<ConfigSchemaResponse>(`/api/projects/${projectId}/config-schema`);
}

/** 从缓存文件构建输出文件（全量构建）。POST 调用，校对审核完成后触发 */
export async function buildProjectOutput(projectId: string, filenames?: string[]) {
  return apiRequest<BuildOutputResponse>(`/api/projects/${projectId}/build-output`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(filenames ? { filenames } : {}),
  });
}

/** 从缓存文件构建单个输出文件 */
export async function buildSingleFileOutput(projectId: string, filename: string) {
  return apiRequest<BuildOutputResponse>(
    `/api/projects/${projectId}/build-output/${encodeURIComponent(filename)}`,
    {
      method: "POST",
    },
  );
}

// ---- Project files ----

export async function fetchProjectFiles(projectId: string) {
  return apiRequest<ProjectFilesResponse>(`/api/projects/${projectId}/files`);
}

/** 在系统文件管理器中定位（文件）/ 打开（文件夹）。后端按 is_metadata 解析实际绝对路径。 */
export async function revealInFileManager(
  projectId: string,
  path: string,
  isMetadata: boolean,
) {
  return apiRequest<{ success: boolean; path: string }>(
    `/api/projects/${projectId}/reveal`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, is_metadata: isMetadata }),
    },
  );
}

// ---- Project cache ----

export async function fetchProjectCache(projectId: string) {
  return apiRequest<{ project_dir: string; cache_dir: string; files: FileEntry[] }>(
    `/api/projects/${projectId}/cache`,
  );
}

export async function fetchCacheFile(projectId: string, filename: string) {
  return apiRequest<CacheFileResponse>(
    `/api/projects/${projectId}/cache/${encodeURIComponent(filename)}`,
  );
}

export async function saveCacheFile(
  projectId: string,
  filename: string,
  entries: CacheEntry[],
  configFileName?: string,
) {
  return apiRequest<{ success: boolean; filename: string; entries?: CacheEntry[] }>(
    `/api/projects/${projectId}/cache/save`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        filename,
        entries,
        config_file_name: configFileName || "config.yaml",
      }),
    },
  );
}

export async function deleteCacheEntry(projectId: string, filename: string, index: number) {
  return apiRequest<{ success: boolean; filename: string; deleted_index: number }>(
    `/api/projects/${projectId}/cache/delete-entry`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename, index }),
    },
  );
}

// ---- 项目元数据 — per-file 模式 ----

export async function fetchPerFileMetadata(
  projectId: string,
  type: MetadataType,
  filename: string,
) {
  return apiRequest<PerFileMetadataResponse>(
    `/api/projects/${projectId}/metadata/${type}/${encodeURIComponent(filename)}`,
  );
}

export async function savePerFileMetadata(
  projectId: string,
  type: MetadataType,
  filename: string,
  entry: MetadataEntry,
) {
  return apiRequest<{ success: boolean; type: string; filename: string; path: string }>(
    `/api/projects/${projectId}/metadata/${type}/${encodeURIComponent(filename)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entry }),
    },
  );
}

export async function deleteCacheFiles(projectId: string, filenames: string[]) {
  return apiRequest<{ success: boolean; deleted_files: string[]; not_found_files: string[] }>(
    `/api/projects/${projectId}/cache/delete-file`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filenames }),
    },
  );
}

export async function searchCache(
  projectId: string,
  query: string,
  field: CacheSearchField = "all",
  maxResults = 500,
) {
  return apiRequest<CacheSearchResponse>(`/api/projects/${projectId}/cache/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, field, max_results: maxResults }),
  });
}

export async function replaceCache(
  projectId: string,
  query: string,
  replacement: string,
  field: CacheReplaceField = "dst",
  dryRun = false,
) {
  return apiRequest<CacheReplaceResponse>(`/api/projects/${projectId}/cache/replace`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, replacement, field, dry_run: dryRun }),
  });
}

// ---- Project progress & runtime ----

export async function fetchProjectProgress(projectId: string) {
  return apiRequest<ProjectProgressResponse>(`/api/projects/${projectId}/progress`);
}

export async function fetchProjectRuntime(projectId: string) {
  return apiRequest<ProjectRuntimeResponse>(`/api/projects/${projectId}/runtime`);
}

export async function stopProjectTranslation(projectId: string) {
  return apiRequest<StopProjectResponse>(`/api/projects/${projectId}/stop`, {
    method: "POST",
  });
}

// ---- Project dictionary ----

export async function fetchProjectDictionary(projectId: string, configFileName = "config.yaml") {
  return apiRequest<ProjectDictionaryResponse>(
    `/api/projects/${projectId}/dictionary?config=${encodeURIComponent(configFileName)}`,
  );
}

export async function fetchProjectDictionaryManager(
  projectId: string,
  configFileName = "config.yaml",
) {
  return apiRequest<ProjectDictionaryManagerResponse>(
    `/api/projects/${projectId}/dictionary/project?config=${encodeURIComponent(configFileName)}`,
  );
}

export async function createProjectDictionaryFile(
  projectId: string,
  payload: { config_file_name: string; category: DictionaryCategory; filename: string },
) {
  return apiRequest<{ success: boolean; file_key: string; path: string }>(
    `/api/projects/${projectId}/dictionary/project/create`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}

export async function saveProjectDictionaryFile(
  projectId: string,
  payload: { config_file_name: string; file_key: string; content: string },
) {
  return apiRequest<{ success: boolean; file_key: string }>(
    `/api/projects/${projectId}/dictionary/project/save`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}

export async function deleteProjectDictionaryFile(
  projectId: string,
  payload: { config_file_name: string; file_key: string; delete_file?: boolean },
) {
  return apiRequest<{ success: boolean; file_key: string; deleted_file: boolean }>(
    `/api/projects/${projectId}/dictionary/project/delete`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}

// ---- Common dictionary ----

export async function fetchCommonDictionaryManager() {
  return apiRequest<CommonDictionaryManagerResponse>("/api/dictionaries/common");
}

export async function createCommonDictionaryFile(payload: {
  category: DictionaryCategory;
  filename: string;
}) {
  return apiRequest<{ success: boolean; filename: string; path: string }>(
    "/api/dictionaries/common/create",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}

export async function saveCommonDictionaryFile(payload: { filename: string; content: string }) {
  return apiRequest<{ success: boolean; filename: string }>("/api/dictionaries/common/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function deleteCommonDictionaryFile(payload: { filename: string }) {
  return apiRequest<{ success: boolean; filename: string }>("/api/dictionaries/common/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

// ---- Project problems ----

export async function fetchProjectProblems(projectId: string, file?: string) {
  const q = file ? `?file=${encodeURIComponent(file)}` : "";
  return apiRequest<ProjectProblemsResponse>(`/api/projects/${projectId}/problems${q}`);
}

/** 对给定缓存条目重新运行问题检测（不落盘），用于"刷新"时强制更新检测结果 */
export async function checkCacheProblems(
  projectId: string,
  filename: string,
  entries: CacheEntry[],
  configFileName?: string,
) {
  return apiRequest<CacheCheckResponse>(`/api/projects/${projectId}/cache/check`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      filename,
      entries,
      config_file_name: configFileName || "config.yaml",
    }),
  });
}

// ---- Name table ----

export async function fetchNameTable(projectId: string) {
  return apiRequest<NameTableResponse>(`/api/projects/${projectId}/name-table`);
}

export async function generateNameTable(projectId: string) {
  return apiRequest<NameTableGenerateResponse>(`/api/projects/${projectId}/name-table/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
}

export async function saveNameTable(projectId: string, names: NameEntry[]) {
  return apiRequest<NameTableSaveResponse>(`/api/projects/${projectId}/name-table/save`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ names }),
  });
}

export function getAiTranslateUrl(projectId: string) {
  const baseUrl = getBackendBaseUrl();
  return `${baseUrl}/api/projects/${projectId}/name-table/ai-translate`;
}

export async function fetchNameDict(projectId: string) {
  return apiRequest<NameDictResponse>(`/api/projects/${projectId}/name-dict`);
}

export async function fetchProjectLogs(projectId: string, tail = 2000) {
  return apiRequest<ProjectLogsResponse>(`/api/projects/${projectId}/logs?tail=${tail}`);
}

// Re-export ProblemEntry type for convenience
export type { ProblemEntry };
