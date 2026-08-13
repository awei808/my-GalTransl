/**
 * API Type Definitions
 *
 * All shared TypeScript types for API requests and responses.
 */

export type ConnectionPhase = "connecting" | "online" | "offline";

export type JobStatus = "pending" | "running" | "completed" | "failed" | "cancelled";

export type TranslatorOption = {
  description: string;
  name: string;
};

/** 模型可用性检测结果（POST /api/projects/:id/check-model 返回） */
export type ModelCheckResult = {
  /** 是否通过：applicable 时表示有可用 token；非 applicable 时恒为 true */
  ok: boolean;
  /** 该后端是否适用 token 检测（OpenAI-Compatible 类为 true，本地/特殊端点为 false） */
  applicable: boolean;
  /** 检测后可用 token 数 */
  available: number;
  /** 配置中 token 总数 */
  total: number;
  /** 后端名称（translator） */
  engine: string;
  /** 给用户看的状态说明 */
  message: string;
};

/** 批次划分预检结果（POST /api/projects/:id/check-batch-size 返回） */
export type CheckBatchSizeResult = {
  /** 最大可自然划分文件行数（0.9 * max_batch_size * max_batches） */
  max_natural_lines: number;
  /** 行数超限的待翻译文件列表 */
  oversize_files: Array<{ filename: string; lines: number }>;
  /** 是否适用本次预检（仅 ForGal-full-pipeline / ForBatchMetaData 为 true） */
  applicable: boolean;
};

export type Job = {
  config_file_name: string;
  created_at: string;
  error: string;
  finished_at: string;
  job_id: string;
  project_dir: string;
  started_at: string;
  status: JobStatus;
  success: boolean;
  translator: string;
  gendic_added_entries?: number;
  gendic_duplicated_entries?: number;
};

export type PromptTemplateOverride = {
  system_prompt?: string;
  user_prompt?: string;
};

export type SubmitJobPayload = {
  config_file_name: string;
  project_dir: string;
  translator: string;
  backend_profile?: string;
  backend_profile_data?: Record<string, unknown>;
  prompt_template_overrides?: Record<string, PromptTemplateOverride>;
};

export type TranslatorsResponse = {
  translators: TranslatorOption[];
};

export type JobsResponse = {
  jobs: Job[];
};

export type ErrorResponse = {
  error?: string;
};

export type ProjectConfigTemplateResponse = {
  content: string;
};

// ---- Project API types ----

export type ProjectConfigResponse = {
  config: Record<string, unknown>;
  project_dir: string;
  config_file_name: string;
};

export type ConfigSchemaResponse = {
  project_dir: string;
  parameters: Record<string, string>;
};

export type ProjectConfigUpdatePayload = {
  config: Record<string, unknown>;
  config_file_name: string;
  /** 向导保存时携带的流水线阶段开关（值为 false 的阶段不执行） */
  pipeline?: Record<string, boolean>;
  /** 向导保存时携带的「生成示例文件」阶段键集合（独立于禁用阶段） */
  sample_stages?: string[];
};

export type FileEntry = {
  name: string;
  is_file: boolean;
  size: number;
  modified: string;
  entry_count?: number;
  /** 是否为元数据文件（FileMetaData.json / BatchMetadata.json），与平铺译文缓存不在同目录 */
  is_metadata?: boolean;
};

/** 缓存目录树节点（递归）。path 为相对缓存根目录的路径，使用 '/' 分隔。 */
export type FileNode = {
  name: string;
  path: string;
  is_file: boolean;
  size: number;
  modified: string;
  is_metadata?: boolean;
  entry_count?: number;
  children?: FileNode[];
};

export type ProjectFilesResponse = {
  project_dir: string;
  input_dir: string;
  output_dir: string;
  cache_dir: string;
  input_files: FileEntry[];
  output_files: FileEntry[];
  cache_files: FileNode[];
};

export type CacheFileResponse = {
  project_dir: string;
  filename: string;
  entries: CacheEntry[];
};

/* H 剧情区间（来自 pass2 批次元数据，换算为缓存条目 index 口径） */
export type CacheHRange = { lo: number; hi: number };
export type CacheHrangesResponse = {
  batch_exists: boolean;
  has_h: boolean;
  h_ranges: CacheHRange[];
};

/* 元数据 — per-file 模式（filemeta/batchmeta/globalprompt/plotroute），每文件独立 JSON */
export type MetadataEntry = Record<string, unknown>;
export type MetadataType = "filemeta" | "batchmeta" | "globalprompt" | "plotroute";
export type PerFileMetadataResponse = {
  exists: boolean;
  type: MetadataType;
  filename?: string;
  entry: MetadataEntry | null;
  path?: string;
};

export type CacheEntry = {
  index: number;
  name: string | string[];
  pre_src: string;
  post_src: string;
  pre_dst: string;
  proofread_dst?: string;
  trans_by?: string;
  proofread_by?: string;
  problem?: string;
  skip_check?: boolean;
  trans_conf?: number;
  doub_content?: string;
  unknown_proper_noun?: string;
  pre_jp?: string;
  post_jp?: string;
  pre_zh?: string;
  proofread_zh?: string;
  post_zh_preview?: string;
  post_dst_preview?: string;
  alt_dst?: string;
};

export type CacheSearchField = "all" | "src" | "dst" | "problem";

export type CacheSearchResult = {
  filename: string;
  index: number;
  speaker: string | string[];
  post_src: string;
  pre_dst: string;
  match_src: boolean;
  match_dst: boolean;
  match_speaker: boolean;
  match_problem: boolean;
  problem: string;
  trans_by: string;
};

export type CacheSearchResponse = {
  results: CacheSearchResult[];
  total: number;
};

export type CacheReplaceField = "src" | "dst" | "all";

export type CacheReplaceFileDetail = {
  filename: string;
  matches: number;
  entries?: CacheEntry[];
};

export type CacheReplaceResponse = {
  success: boolean;
  total_matches: number;
  total_files: number;
  dry_run: boolean;
  file_details: CacheReplaceFileDetail[];
};

export type FileProgress = {
  filename: string;
  total: number;
  translated: number;
  problems: number;
  failed: number;
};

export type ProjectProgressResponse = {
  project_dir: string;
  total: number;
  translated: number;
  problems: number;
  failed: number;
  files: FileProgress[];
};

export type RuntimeJob = {
  job_id: string;
  status: JobStatus;
  translator: string;
  created_at: string;
  started_at: string;
  finished_at: string;
  error?: string;
  gendic_added_entries?: number;
  gendic_duplicated_entries?: number;
};

export type ProjectRuntimeSummary = {
  total: number;
  translated: number;
  problems: number;
  failed: number;
  percent: number;
  workers_active: number;
  workers_configured: number;
  translation_speed_lpm: number;
  eta_seconds: number | null;
  updated_at: string;
};

export type ProjectRuntimeErrorEntry = {
  id: string;
  ts: string;
  kind: string;
  level: string;
  message: string;
  filename: string;
  index_range: string;
  retry_count: number | null;
  model: string;
  sleep_seconds: number | null;
};

export type ProjectRuntimeSuccessEntry = {
  id: string;
  ts: string;
  filename: string;
  index: number;
  speaker: string | string[] | null;
  source_preview: string;
  translation_preview: string;
  trans_by: string;
};

export type ProjectRetranslStatEntry = {
  key: string;
  count: number;
};

export type WorkerPromptPreview = {
  worker_id: string;
  preview: string;
  filename: string;
  batch: string;
  updated_at: string;
};

export type ProjectRuntimeResponse = {
  project_dir: string;
  job: RuntimeJob | null;
  summary: ProjectRuntimeSummary;
  stage: string;
  stage_index: number;
  stage_total: number;
  current_file: string;
  /** 当前文件被切分的 chunk/批次序号（翻译阶段有效，第 N 批） */
  current_batch: number;
  /** 当前文件的总批次数 */
  batch_total: number;
  latest_prompt_preview: string;
  latest_assembled_preview: string;
  /** 多 worker 并发时按 worker_id 隔离的提示词快照（key 为 worker 标识） */
  prompt_previews: Record<string, WorkerPromptPreview>;
  recent_errors: ProjectRuntimeErrorEntry[];
  recent_successes: ProjectRuntimeSuccessEntry[];
  /** 一次性用户提示（后端流水线阶段告知），前端 toast 后调用 clearRuntimeNotices 清除 */
  notices: string[];
  retransl_stats: ProjectRetranslStatEntry[];
  files: FileProgress[];
};

export type StopProjectResponse = {
  success: boolean;
  project_dir: string;
  job_id: string;
  status: JobStatus;
  message: string;
};

export type BuildOutputResponse = {
  success: boolean;
  project_dir: string;
  built_files: string[];
  total_built: number;
  errors?: string[];
};

/** 构建前校验：单条内容异常 */
export type BuildValidationIssue = {
  file: string;
  issue: string;
};

/** POST /api/projects/:id/build/validate 响应（仅提示，不阻断构建） */
export type BuildValidationResponse = {
  ok: boolean;
  input_total: number;
  cache_total: number;
  missing_files: string[];
  content_issues: BuildValidationIssue[];
};

export type DictFileContent = {
  path: string;
  lines: string[];
  count: number;
  mtime?: number | null;
  error?: string;
};

export type ProjectDictionaryResponse = {
  project_dir: string;
  default_dict_folder: string;
  pre_dict_files: string[];
  gpt_dict_files: string[];
  gpt_dict_files_h: string[];
  gpt_dict_files_nh: string[];
  post_dict_files: string[];
  h_dict_files: string[];
  dict_contents: Record<string, DictFileContent>;
};

export type DictionaryCategory = "pre" | "gpt" | "gpth" | "gptnh" | "post" | "h" | "forbiddenh" | "forbiddennh" | "forbidden";

export type ProjectDictionaryManagerResponse = {
  project_dir: string;
  config_file_name: string;
  pre_dict_files: string[];
  gpt_dict_files: string[];
  gpt_dict_files_h: string[];
  gpt_dict_files_nh: string[];
  post_dict_files: string[];
  h_dict_files: string[];
  forbidden_dict_files_h: string[];
  forbidden_dict_files_nh: string[];
  dict_contents: Record<string, DictFileContent>;
};

export type CommonDictionaryManagerResponse = {
  dict_dir: string;
  pre_dict_files: string[];
  gpt_dict_files: string[];
  gpt_dict_files_h: string[];
  gpt_dict_files_nh: string[];
  post_dict_files: string[];
  h_dict_files: string[];
  forbidden_dict_files_h: string[];
  forbidden_dict_files_nh: string[];
  dict_contents: Record<string, DictFileContent>;
};

export type ProblemEntry = {
  filename: string;
  index: number;
  speaker: string | string[];
  post_src: string;
  pre_dst: string;
  problem: string;
  trans_by: string;
  post_jp?: string;
  pre_zh?: string;
};

export type ProjectProblemsResponse = {
  project_dir: string;
  problems: ProblemEntry[];
  total: number;
};

export type AltTransEntry = {
  filename: string;
  index: number;
  speaker: string | string[];
  post_src: string;
  pre_dst: string;
  alt_dst: string;
  trans_by: string;
  post_jp?: string;
  pre_zh?: string;
};

export type ProjectAltTransResponse = {
  project_dir: string;
  alts: AltTransEntry[];
  total: number;
};

/** POST /api/projects/:id/cache/check 单条目重检结果 */
export type CacheCheckResult = {
  index: number;
  problem: string;
  post_dst_preview: string | null;
  skip_check: boolean;
};

/** POST /api/projects/:id/cache/check 响应（只检测不落盘） */
export type CacheCheckResponse = {
  success: boolean;
  filename?: string;
  results: CacheCheckResult[];
};

/** POST /api/projects/:id/cache/recheck-all 响应（全缓存重检并写回） */
export type CacheRecheckAllResponse = {
  success: boolean;
  rechecked?: number;
  error?: string;
};

// ---- Name Table API types ----

export type NameEntry = {
  src_name: string;
  dst_name: string;
  count: number;
};

export type NameTableResponse = {
  project_dir: string;
  source_file: string | null;
  names: NameEntry[];
};

export type NameTableGenerateResponse = {
  success: boolean;
  source_file: string;
  names: NameEntry[];
  total: number;
  job_id?: string;
};

export type NameTableSaveResponse = {
  success: boolean;
  source_file: string;
  total: number;
};

export type NameDictResponse = {
  project_dir: string;
  name_dict: Record<string, string>;
};

export type ProjectLogsResponse = {
  project_dir: string;
  exists: boolean;
  total_lines?: number;
  lines: string[];
};

export type PluginInfo = {
  name: string;
  display_name: string;
  version: string;
  author: string;
  description: string;
  type: string;
  module: string;
  settings: Record<string, unknown>;
};

export type AppSettings = {
  printTranslationLogInTerminal: boolean;
  maxConcurrentJobs?: number;
  writeApiCallLog?: boolean;
};

export type ThemeMode = "light" | "dark" | "system";

export type CustomBackgroundPreference = {
  imageDataUrl: string;
  imageName: string;
  opacity: number;
  surfaceOpacity: number;
};

export type PluginsResponse = {
  plugins: PluginInfo[];
};

export type ProblemTypeInfo = {
  name: string;
  description: string;
};

export type ProblemTypesResponse = {
  problem_types: ProblemTypeInfo[];
};

export type PromptTemplateInfo = {
  name: string;
  description: string;
  default_system_prompt: string;
  system_prompt: string;
  system_overridden: boolean;
  default_user_prompt: string;
  user_prompt: string;
  user_overridden: boolean;
  overridden: boolean;
};

export type PromptTemplatesResponse = {
  templates: PromptTemplateInfo[];
};

export type VersionCheckResponse = {
  version: string;
  latest_version: string | null;
  update_available: boolean;
  author?: string;
};

/** GET /api/version 返回：版本号 + 作者（以后端 AUTHOR 为准） */
export type VersionResponse = {
  version: string;
  author?: string;
};

// ---- Backend Profiles API types ----

export type BackendProfilesResponse = {
  profiles: Record<string, Record<string, unknown>>;
};

export type BackendProfileResponse = {
  name: string;
  profile: Record<string, unknown>;
};

export type BackendProfilesMap = Record<string, Record<string, unknown>>;

// ---- OpenAI-Compatible model list types ----

export interface FetchOpenAIModelsPayload {
  endpoint: string;
  token: string;
  proxy?: { http?: string; https?: string } | string | null;
  timeout?: number;
}

export interface FetchOpenAIModelsResponse {
  models: string[];
  url: string;
}
