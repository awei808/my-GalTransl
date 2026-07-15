/**
 * API Type Definitions
 *
 * All shared TypeScript types for API requests and responses.
 */

export type ConnectionPhase = 'connecting' | 'online' | 'offline';

export type JobStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';

export type TranslatorOption = {
  description: string;
  name: string;
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

export type ProjectConfigUpdatePayload = {
  config: Record<string, unknown>;
  config_file_name: string;
};

export type FileEntry = {
  name: string;
  is_file: boolean;
  size: number;
  modified: string;
  entry_count?: number;
};

export type ProjectFilesResponse = {
  project_dir: string;
  input_dir: string;
  output_dir: string;
  cache_dir: string;
  input_files: FileEntry[];
  output_files: FileEntry[];
  cache_files: FileEntry[];
};

export type CacheFileResponse = {
  project_dir: string;
  filename: string;
  entries: CacheEntry[];
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
  trans_conf?: number;
  doub_content?: string;
  unknown_proper_noun?: string;
  pre_jp?: string;
  post_jp?: string;
  pre_zh?: string;
  proofread_zh?: string;
  post_zh_preview?: string;
  post_dst_preview?: string;
};

export type CacheSearchField = 'all' | 'src' | 'dst' | 'problem';

export type CacheSearchResult = {
  filename: string;
  index: number;
  speaker: string | string[];
  post_src: string;
  pre_dst: string;
  match_src: boolean;
  match_dst: boolean;
  match_problem: boolean;
  problem: string;
  trans_by: string;
};

export type CacheSearchResponse = {
  results: CacheSearchResult[];
  total: number;
};

export type CacheReplaceField = 'src' | 'dst' | 'all';

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

export type ProjectRuntimeResponse = {
  project_dir: string;
  job: RuntimeJob | null;
  summary: ProjectRuntimeSummary;
  stage: string;
  current_file: string;
  recent_errors: ProjectRuntimeErrorEntry[];
  recent_successes: ProjectRuntimeSuccessEntry[];
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
  post_dict_files: string[];
  dict_contents: Record<string, DictFileContent>;
};

export type DictionaryCategory = 'pre' | 'gpt' | 'post';

export type ProjectDictionaryManagerResponse = {
  project_dir: string;
  config_file_name: string;
  pre_dict_files: string[];
  gpt_dict_files: string[];
  post_dict_files: string[];
  dict_contents: Record<string, DictFileContent>;
};

export type CommonDictionaryManagerResponse = {
  dict_dir: string;
  pre_dict_files: string[];
  gpt_dict_files: string[];
  post_dict_files: string[];
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
};

export type ThemeMode = 'light' | 'dark' | 'system';

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
