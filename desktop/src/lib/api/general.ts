/**
 * General API — version, translators, jobs, plugins, settings, templates.
 */
import { apiRequest } from './client';
import { getPromptTemplateOverridesForJob } from './preferences';
import type {
  AppSettings,
  FetchOpenAIModelsPayload,
  FetchOpenAIModelsResponse,
  Job,
  JobsResponse,
  ModelCheckResult,
  PluginInfo,
  PluginsResponse,
  ProblemTypeInfo,
  ProblemTypesResponse,
  PromptTemplatesResponse,
  PromptTemplateInfo,
  SubmitJobPayload,
  TranslatorOption,
  TranslatorsResponse,
  VersionCheckResponse,
} from './types';

// ---- Version ----

export async function fetchVersion() {
  const response = await apiRequest<{ version: string }>('/api/version');
  return response.version;
}

export async function fetchVersionCheck() {
  return apiRequest<VersionCheckResponse>('/api/version/check');
}

// ---- Translators ----

export async function fetchTranslators() {
  const response = await apiRequest<TranslatorsResponse>('/api/translators');
  return response.translators;
}

// ---- Jobs ----

export async function fetchJobs() {
  const response = await apiRequest<JobsResponse>('/api/jobs');
  return response.jobs;
}

export async function fetchJob(jobId: string) {
  return apiRequest<Job>(`/api/jobs/${jobId}`);
}

export async function submitJob(payload: SubmitJobPayload) {
  const overrides = getPromptTemplateOverridesForJob(payload.translator);
  const payloadWithOverrides = Object.keys(overrides).length > 0
    ? { ...payload, prompt_template_overrides: overrides }
    : payload;
  return apiRequest<Job>('/api/jobs', {
    body: JSON.stringify(payloadWithOverrides),
    headers: { 'Content-Type': 'application/json' },
    method: 'POST',
  });
}

// ---- Plugins ----

export async function fetchPlugins() {
  const response = await apiRequest<PluginsResponse>('/api/plugins');
  return response.plugins;
}

// ---- Problem types ----

export async function fetchProblemTypes() {
  const response = await apiRequest<ProblemTypesResponse>('/api/problem-types');
  return response.problem_types;
}

// ---- Translation guidelines ----

export async function fetchTranslationGuidelines() {
  const response = await apiRequest<{ guidelines: string[] }>('/api/translation-guidelines');
  return response.guidelines;
}

// ---- App settings ----

export async function fetchAppSettings() {
  return apiRequest<AppSettings>('/api/app-settings');
}

export async function updateAppSettings(settings: AppSettings) {
  return apiRequest<AppSettings>('/api/app-settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  });
}

// ---- Project config template ----

export async function fetchDefaultProjectConfigTemplate() {
  const response = await apiRequest<{ content: string }>('/api/project-config-template');
  return response.content;
}

// ---- Prompt templates ----

export async function fetchPromptTemplates() {
  return apiRequest<PromptTemplatesResponse>('/api/prompt-templates');
}

// ---- OpenAI-Compatible model list ----

export async function fetchOpenAIModels(payload: FetchOpenAIModelsPayload) {
  return apiRequest<FetchOpenAIModelsResponse>('/api/openai-models', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

// ---- Model availability check ----

export async function checkModelAvailability(payload: {
  projectId: string;
  translator: string;
  configFileName?: string;
}): Promise<ModelCheckResult> {
  return apiRequest<ModelCheckResult>(
    `/api/projects/${payload.projectId}/check-model`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        translator: payload.translator,
        config_file_name: payload.configFileName ?? 'config.yaml',
      }),
    }
  );
}

// Re-export types for convenience
export type {
  AppSettings,
  FetchOpenAIModelsPayload,
  FetchOpenAIModelsResponse,
  Job,
  ModelCheckResult,
  PluginInfo,
  ProblemTypeInfo,
  PromptTemplateInfo,
  SubmitJobPayload,
  TranslatorOption,
  VersionCheckResponse,
};
