/**
 * Home page utility functions — history, job memory, helpers.
 */
import {
  getHomeHistoryRetentionLimit,
  getHomeJobRetentionLimit,
  type Job,
} from '../../lib/api';

const HISTORY_KEY = 'galtransl-project-history';
const JOB_MEMORY_KEY = 'galtransl-home-jobs-memory';
const JOB_CLEARED_KEY = 'galtransl-home-jobs-cleared';

export type ProjectHistoryEntry = {
  projectDir: string;
  configFileName: string;
  lastOpened: string;
};

export function loadHistory(limit = getHomeHistoryRetentionLimit()): ProjectHistoryEntry[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    const parsed = raw ? (JSON.parse(raw) as ProjectHistoryEntry[]) : [];
    return parsed.slice(0, limit);
  } catch {
    return [];
  }
}

function saveHistory(entries: ProjectHistoryEntry[]) {
  localStorage.setItem(HISTORY_KEY, JSON.stringify(entries));
}

export function addProjectToHistory(projectDir: string, configFileName: string) {
  const entries = loadHistory();
  const withoutDuplicate = entries.filter((e) => e.projectDir !== projectDir);
  withoutDuplicate.unshift({
    projectDir,
    configFileName,
    lastOpened: new Date().toISOString(),
  });
  saveHistory(withoutDuplicate.slice(0, getHomeHistoryRetentionLimit()));
}

export function removeProjectFromHistory(projectDir: string) {
  const entries = loadHistory().filter((e) => e.projectDir !== projectDir);
  saveHistory(entries);
}

// ---- Job memory ----

function getJobSortTimestamp(job: Job): number {
  const timestamp = Date.parse(job.finished_at || job.started_at || job.created_at || '');
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function isActiveJobStatus(status: Job['status']): boolean {
  return status === 'pending' || status === 'running';
}

function isActiveJob(job: Job): boolean {
  return isActiveJobStatus(job.status);
}

function normalizeRememberedJob(value: unknown): Job | null {
  if (!value || typeof value !== 'object') return null;

  const raw = value as Partial<Record<keyof Job, unknown>>;
  const status = raw.status;
  if (status !== 'pending' && status !== 'running' && status !== 'completed' && status !== 'failed' && status !== 'cancelled') {
    return null;
  }

  if (typeof raw.job_id !== 'string' || typeof raw.project_dir !== 'string') return null;

  return {
    config_file_name: typeof raw.config_file_name === 'string' ? raw.config_file_name : '',
    created_at: typeof raw.created_at === 'string' ? raw.created_at : '',
    error: typeof raw.error === 'string' ? raw.error : '',
    finished_at: typeof raw.finished_at === 'string' ? raw.finished_at : '',
    job_id: raw.job_id,
    project_dir: raw.project_dir,
    started_at: typeof raw.started_at === 'string' ? raw.started_at : '',
    status,
    success: typeof raw.success === 'boolean' ? raw.success : false,
    translator: typeof raw.translator === 'string' ? raw.translator : '',
  };
}

export function sortAndLimitJobs(jobs: Job[], limit: number): Job[] {
  return jobs
    .sort((a, b) => getJobSortTimestamp(b) - getJobSortTimestamp(a))
    .slice(0, limit);
}

export function loadRememberedJobs(limit = getHomeJobRetentionLimit()): Job[] {
  try {
    const raw = localStorage.getItem(JOB_MEMORY_KEY);
    if (!raw) return [];

    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];

    return sortAndLimitJobs(
      parsed
        .map(normalizeRememberedJob)
        .filter((job): job is Job => job !== null)
        .filter((job) => !isActiveJob(job)),
      limit,
    );
  } catch {
    return [];
  }
}

export function saveRememberedJobs(jobs: Job[], limit: number) {
  try {
    localStorage.setItem(JOB_MEMORY_KEY, JSON.stringify(sortAndLimitJobs([...jobs], limit)));
  } catch {
    // ignore storage errors
  }
}

export function loadClearedJobIds(): Set<string> {
  try {
    const raw = localStorage.getItem(JOB_CLEARED_KEY);
    if (!raw) return new Set();
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.filter((id): id is string => typeof id === 'string'));
  } catch {
    return new Set();
  }
}

export function saveClearedJobIds(ids: Set<string>) {
  try {
    localStorage.setItem(JOB_CLEARED_KEY, JSON.stringify(Array.from(ids)));
  } catch {
    // ignore storage errors
  }
}

export function mergeJobsWithMemory(existing: Job[], incoming: Job[], limit: number, cleared: Set<string>): Job[] {
  const merged = new Map<string, Job>();

  incoming.forEach((job) => {
    if (cleared.has(job.job_id)) return;
    merged.set(job.job_id, job);
  });

  existing.forEach((job) => {
    if (cleared.has(job.job_id)) return;
    if (!merged.has(job.job_id) && !isActiveJob(job)) {
      merged.set(job.job_id, job);
    }
  });

  return sortAndLimitJobs(Array.from(merged.values()), limit);
}

// ---- Display helpers ----

export function projectName(projectDir: string): string {
  return projectDir.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || projectDir;
}

export function formatDate(isoString: string): string {
  try {
    const date = new Date(isoString);
    return date.toLocaleDateString('zh-CN', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return isoString;
  }
}
