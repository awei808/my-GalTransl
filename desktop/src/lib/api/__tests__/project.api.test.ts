import { describe, it, expect, beforeEach, vi } from 'vitest';

// Mock the api client
vi.mock('../client', () => ({
  apiRequest: vi.fn(),
}));

import { apiRequest } from '../client';
import { fetchProjectConfig, updateProjectConfig } from '../project';
import { submitJob, fetchJob, fetchPlugins, fetchTranslationGuidelines } from '../general';

const mockedApi = apiRequest as ReturnType<typeof vi.fn>;

describe('api/project.ts (P1)', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('fetchProjectConfig calls correct path', async () => {
    mockedApi.mockResolvedValueOnce({ config: { common: {} }, config_file_name: 'config.yaml' });
    await fetchProjectConfig('enc-id', 'config.yaml');
    expect(mockedApi).toHaveBeenCalledWith('/api/projects/enc-id/config?config=config.yaml');
  });

  it('updateProjectConfig calls PUT with body', async () => {
    mockedApi.mockResolvedValueOnce({ success: true });
    const body = { config: { common: { workersPerProject: 8 } }, config_file_name: 'config.yaml' };
    await updateProjectConfig('pid', body);
    expect(mockedApi).toHaveBeenCalledWith(
      '/api/projects/pid/config',
      expect.objectContaining({ method: 'PUT', body: JSON.stringify(body) }),
    );
  });

  it('fetchProjectConfig throws on network error', async () => {
    mockedApi.mockRejectedValueOnce(new Error('Network error'));
    await expect(fetchProjectConfig('id', 'yaml')).rejects.toThrow('Network error');
  });
});

describe('api/general.ts — jobs (P1)', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('submitJob calls correct endpoint', async () => {
    mockedApi.mockResolvedValueOnce({ job_id: 'job-1' });
    const payload = { project_dir: '/test', config_file_name: 'c.yaml', translator: 'gpt' };
    const result = await submitJob(payload);
    expect(result).toEqual({ job_id: 'job-1' });
    expect(mockedApi).toHaveBeenCalledWith('/api/jobs', expect.objectContaining({ method: 'POST' }));
  });

  it('fetchJob calls correct endpoint', async () => {
    mockedApi.mockResolvedValueOnce({ status: 'completed', success: true });
    const result = await fetchJob('job-1');
    expect(result.status).toBe('completed');
    expect(mockedApi).toHaveBeenCalledWith('/api/jobs/job-1');
  });

  it('fetchJob handles failed status', async () => {
    mockedApi.mockResolvedValueOnce({ status: 'failed', error: 'boom' });
    const result = await fetchJob('job-2');
    expect(result.status).toBe('failed');
  });
});

describe('api/general.ts — plugins & guidelines (P1)', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('fetchPlugins calls /api/plugins', async () => {
    mockedApi.mockResolvedValueOnce([]);
    await fetchPlugins();
    expect(mockedApi).toHaveBeenCalledWith('/api/plugins');
  });

  it('fetchTranslationGuidelines calls correct endpoint', async () => {
    mockedApi.mockResolvedValueOnce({ guidelines: ['日译中_增强'] });
    const result = await fetchTranslationGuidelines();
    expect(result).toEqual(['日译中_增强']);
    expect(mockedApi).toHaveBeenCalledWith('/api/translation-guidelines');
  });

  it('fetchTranslationGuidelines returns empty on empty response', async () => {
    mockedApi.mockResolvedValueOnce({ guidelines: [] });
    expect(await fetchTranslationGuidelines()).toEqual([]);
  });
});
