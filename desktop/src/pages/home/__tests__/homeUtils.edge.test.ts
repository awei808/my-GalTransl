import { describe, it, expect, beforeEach, vi } from 'vitest';
import type { Job } from '../../../lib/api';

const mockStorage: Record<string, string> = {};
const localStorageMock = {
  getItem: vi.fn((key: string) => mockStorage[key] ?? null),
  setItem: vi.fn((key: string, value: string) => { mockStorage[key] = value; }),
  removeItem: vi.fn((key: string) => { delete mockStorage[key]; }),
  clear: vi.fn(() => { for (const k of Object.keys(mockStorage)) delete mockStorage[k]; }),
};
vi.stubGlobal('localStorage', localStorageMock);

// Import after mock is set up
import {
  loadHistory,
  addProjectToHistory,
  removeProjectFromHistory,
  loadRememberedJobs,
  saveRememberedJobs,
  loadClearedJobIds,
  saveClearedJobIds,
  mergeJobsWithMemory,
  sortAndLimitJobs,
  projectName,
  formatDate,
  type ProjectHistoryEntry,
} from '../homeUtils';

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    config_file_name: 'config.yaml',
    created_at: '2024-01-01T00:00:00Z',
    error: '',
    finished_at: '',
    job_id: 'job-' + Math.random().toString(36).slice(2),
    project_dir: '/test/project',
    started_at: '',
    status: 'completed',
    success: true,
    translator: 'ForGal-json-multi-chat',
    ...overrides,
  };
}

describe('homeUtils — edge cases', () => {
  beforeEach(() => {
    localStorageMock.clear();
    localStorageMock.getItem.mockClear();
    localStorageMock.setItem.mockClear();
  });

  // ── loadHistory ──

  describe('loadHistory', () => {
    it('returns empty array when no history', () => {
      expect(loadHistory()).toEqual([]);
    });

    it('returns empty array when localStorage has corrupted JSON', () => {
      mockStorage['galtransl-project-history'] = '{invalid';
      expect(loadHistory()).toEqual([]);
    });

    it('returns empty array when localStorage has null', () => {
      mockStorage['galtransl-project-history'] = 'null';
      expect(loadHistory()).toEqual([]);
    });

    it('returns entries as-is when localStorage has non-object array items', () => {
      mockStorage['galtransl-home-history-limit'] = '100';
      mockStorage['galtransl-project-history'] = JSON.stringify(['string', 123]);
      const result = loadHistory();
      expect(result).toHaveLength(2);
    });

    it('respects limit parameter', () => {
      const entries: ProjectHistoryEntry[] = Array.from({ length: 50 }, (_, i) => ({
        projectDir: `/project/${i}`,
        configFileName: 'config.yaml',
        lastOpened: new Date().toISOString(),
      }));
      mockStorage['galtransl-project-history'] = JSON.stringify(entries);
      expect(loadHistory(10)).toHaveLength(10);
    });
  });

  // ── addProjectToHistory / removeProjectFromHistory ──

  describe('addProjectToHistory', () => {
    it('adds entry to empty history', () => {
      addProjectToHistory('/test/project', 'config.yaml');
      const history = loadHistory();
      expect(history).toHaveLength(1);
      expect(history[0].projectDir).toBe('/test/project');
    });

    it('moves existing entry to top (dedup)', () => {
      mockStorage['galtransl-home-history-limit'] = '100';
      addProjectToHistory('/a', 'config.yaml');
      addProjectToHistory('/b', 'config.yaml');
      addProjectToHistory('/a', 'config2.yaml');
      const history = loadHistory();
      expect(history).toHaveLength(2);
      expect(history[0].projectDir).toBe('/a');
      expect(history[0].configFileName).toBe('config2.yaml');
    });

    it('handles unicode project dir', () => {
      addProjectToHistory('D:\\解包或汉化用\\项目', 'config.yaml');
      expect(loadHistory()[0].projectDir).toBe('D:\\解包或汉化用\\项目');
    });

    it('handles empty project dir', () => {
      addProjectToHistory('', 'config.yaml');
      expect(loadHistory()).toHaveLength(1);
      expect(loadHistory()[0].projectDir).toBe('');
    });
  });

  describe('removeProjectFromHistory', () => {
    it('removes existing entry', () => {
      addProjectToHistory('/a', 'config.yaml');
      addProjectToHistory('/b', 'config.yaml');
      removeProjectFromHistory('/a');
      const history = loadHistory();
      expect(history).toHaveLength(1);
      expect(history[0].projectDir).toBe('/b');
    });

    it('does nothing when entry does not exist', () => {
      addProjectToHistory('/a', 'config.yaml');
      removeProjectFromHistory('/nonexistent');
      expect(loadHistory()).toHaveLength(1);
    });

    it('does nothing on empty history', () => {
      removeProjectFromHistory('/a');
      expect(loadHistory()).toEqual([]);
    });
  });

  // ── sortAndLimitJobs ──

  describe('sortAndLimitJobs', () => {
    it('returns empty array for empty input', () => {
      expect(sortAndLimitJobs([], 10)).toEqual([]);
    });

    it('sorts by finished_at descending', () => {
      const jobs = [
        makeJob({ job_id: '1', finished_at: '2024-01-01T00:00:00Z' }),
        makeJob({ job_id: '2', finished_at: '2024-03-01T00:00:00Z' }),
        makeJob({ job_id: '3', finished_at: '2024-02-01T00:00:00Z' }),
      ];
      const sorted = sortAndLimitJobs(jobs, 10);
      expect(sorted[0].job_id).toBe('2');
      expect(sorted[1].job_id).toBe('3');
      expect(sorted[2].job_id).toBe('1');
    });

    it('falls back to started_at when finished_at is empty', () => {
      const jobs = [
        makeJob({ job_id: '1', finished_at: '', started_at: '2024-01-01T00:00:00Z' }),
        makeJob({ job_id: '2', finished_at: '', started_at: '2024-03-01T00:00:00Z' }),
      ];
      const sorted = sortAndLimitJobs(jobs, 10);
      expect(sorted[0].job_id).toBe('2');
    });

    it('falls back to created_at when both finished_at and started_at are empty', () => {
      const jobs = [
        makeJob({ job_id: '1', finished_at: '', started_at: '', created_at: '2024-01-01T00:00:00Z' }),
        makeJob({ job_id: '2', finished_at: '', started_at: '', created_at: '2024-03-01T00:00:00Z' }),
      ];
      const sorted = sortAndLimitJobs(jobs, 10);
      expect(sorted[0].job_id).toBe('2');
    });

    it('handles all empty timestamps (sort by 0, stable)', () => {
      const jobs = [
        makeJob({ job_id: '1', finished_at: '', started_at: '', created_at: '' }),
        makeJob({ job_id: '2', finished_at: '', started_at: '', created_at: '' }),
      ];
      const sorted = sortAndLimitJobs(jobs, 10);
      expect(sorted).toHaveLength(2);
    });

    it('handles invalid timestamp strings', () => {
      const jobs = [
        makeJob({ job_id: '1', finished_at: 'invalid-date' }),
        makeJob({ job_id: '2', finished_at: 'also-invalid' }),
      ];
      const sorted = sortAndLimitJobs(jobs, 10);
      expect(sorted).toHaveLength(2);
    });

    it('limits to specified count', () => {
      const jobs = Array.from({ length: 100 }, (_, i) =>
        makeJob({ job_id: `job-${i}`, finished_at: new Date(2024, 0, i + 1).toISOString() }),
      );
      expect(sortAndLimitJobs(jobs, 5)).toHaveLength(5);
    });

    it('limit 0 returns empty array', () => {
      const jobs = [makeJob()];
      expect(sortAndLimitJobs(jobs, 0)).toEqual([]);
    });

    it('negative limit returns empty array', () => {
      const jobs = [makeJob()];
      expect(sortAndLimitJobs(jobs, -1)).toEqual([]);
    });
  });

  // ── mergeJobsWithMemory ──

  describe('mergeJobsWithMemory', () => {
    it('returns empty for both empty inputs', () => {
      expect(mergeJobsWithMemory([], [], 10, new Set())).toEqual([]);
    });

    it('incoming jobs replace existing with same id', () => {
      const existing = [makeJob({ job_id: '1', status: 'running' })];
      const incoming = [makeJob({ job_id: '1', status: 'completed' })];
      const merged = mergeJobsWithMemory(existing, incoming, 10, new Set());
      expect(merged).toHaveLength(1);
      expect(merged[0].status).toBe('completed');
    });

    it('cleared job ids are filtered out', () => {
      const existing = [makeJob({ job_id: '1', status: 'completed' })];
      const incoming = [makeJob({ job_id: '1', status: 'completed' })];
      const cleared = new Set(['1']);
      const merged = mergeJobsWithMemory(existing, incoming, 10, cleared);
      expect(merged).toEqual([]);
    });

    it('existing non-active jobs are kept if not in incoming', () => {
      const existing = [makeJob({ job_id: '1', status: 'completed' })];
      const incoming = [makeJob({ job_id: '2', status: 'running' })];
      const merged = mergeJobsWithMemory(existing, incoming, 10, new Set());
      expect(merged).toHaveLength(2);
    });

    it('existing active jobs are NOT kept if not in incoming (they ended)', () => {
      const existing = [makeJob({ job_id: '1', status: 'running' })];
      const incoming = [makeJob({ job_id: '2', status: 'running' })];
      const merged = mergeJobsWithMemory(existing, incoming, 10, new Set());
      // Existing running job is NOT kept because it's active and not in incoming
      expect(merged).toHaveLength(1);
      expect(merged[0].job_id).toBe('2');
    });

    it('respects limit after merge', () => {
      const existing = Array.from({ length: 50 }, (_, i) =>
        makeJob({ job_id: `e${i}`, status: 'completed', finished_at: `2024-01-${String(i + 1).padStart(2, '0')}T00:00:00Z` }),
      );
      const incoming = Array.from({ length: 50 }, (_, i) =>
        makeJob({ job_id: `i${i}`, status: 'completed', finished_at: `2024-02-${String(i + 1).padStart(2, '0')}T00:00:00Z` }),
      );
      const merged = mergeJobsWithMemory(existing, incoming, 10, new Set());
      expect(merged).toHaveLength(10);
    });
  });

  // ── loadRememberedJobs / saveRememberedJobs ──

  describe('loadRememberedJobs', () => {
    it('returns empty when no stored jobs', () => {
      expect(loadRememberedJobs()).toEqual([]);
    });

    it('returns empty for corrupted JSON', () => {
      mockStorage['galtransl-home-jobs-memory'] = '{invalid';
      expect(loadRememberedJobs()).toEqual([]);
    });

    it('returns empty for null value', () => {
      mockStorage['galtransl-home-jobs-memory'] = 'null';
      expect(loadRememberedJobs()).toEqual([]);
    });

    it('returns empty for non-array JSON', () => {
      mockStorage['galtransl-home-jobs-memory'] = '"string"';
      expect(loadRememberedJobs()).toEqual([]);
    });

    it('filters out active jobs from memory', () => {
      const jobs = [
        makeJob({ job_id: '1', status: 'completed' }),
        makeJob({ job_id: '2', status: 'running' }),
        makeJob({ job_id: '3', status: 'pending' }),
      ];
      mockStorage['galtransl-home-jobs-memory'] = JSON.stringify(jobs);
      const loaded = loadRememberedJobs();
      // Only completed jobs should be loaded (active ones are from the backend)
      expect(loaded.every((j) => j.status === 'completed')).toBe(true);
    });

    it('filters out invalid job entries', () => {
      mockStorage['galtransl-home-jobs-memory'] = JSON.stringify([
        { job_id: '1', status: 'completed', project_dir: '/test' }, // valid
        { job_id: '2', status: 'invalid_status', project_dir: '/test' }, // invalid status
        { status: 'completed' }, // missing job_id
        null,
        'string',
        123,
      ]);
      const loaded = loadRememberedJobs();
      expect(loaded).toHaveLength(1);
      expect(loaded[0].job_id).toBe('1');
    });
  });

  describe('saveRememberedJobs', () => {
    it('saves and reloads correctly', () => {
      const jobs = [makeJob({ job_id: '1', status: 'completed' })];
      saveRememberedJobs(jobs, 10);
      const loaded = loadRememberedJobs();
      expect(loaded).toHaveLength(1);
      expect(loaded[0].job_id).toBe('1');
    });

    it('does not crash on localStorage error', () => {
      localStorageMock.setItem.mockImplementationOnce(() => {
        throw new Error('QuotaExceededError');
      });
      expect(() => saveRememberedJobs([makeJob()], 10)).not.toThrow();
    });
  });

  // ── loadClearedJobIds / saveClearedJobIds ──

  describe('loadClearedJobIds', () => {
    it('returns empty set when no data', () => {
      expect(loadClearedJobIds().size).toBe(0);
    });

    it('returns empty set for corrupted JSON', () => {
      mockStorage['galtransl-home-jobs-cleared'] = '{invalid';
      expect(loadClearedJobIds().size).toBe(0);
    });

    it('filters out non-string entries', () => {
      mockStorage['galtransl-home-jobs-cleared'] = JSON.stringify(['a', 123, null, 'b', true]);
      const ids = loadClearedJobIds();
      expect(ids.has('a')).toBe(true);
      expect(ids.has('b')).toBe(true);
      expect(ids.size).toBe(2);
    });
  });

  describe('saveClearedJobIds', () => {
    it('saves and reloads correctly', () => {
      const ids = new Set(['job-1', 'job-2']);
      saveClearedJobIds(ids);
      const loaded = loadClearedJobIds();
      expect(loaded.has('job-1')).toBe(true);
      expect(loaded.has('job-2')).toBe(true);
    });

    it('handles empty set', () => {
      saveClearedJobIds(new Set());
      // Empty set is stored as []
      expect(loadClearedJobIds().size).toBe(0);
    });

    it('does not crash on localStorage error', () => {
      localStorageMock.setItem.mockImplementationOnce(() => {
        throw new Error('QuotaExceededError');
      });
      expect(() => saveClearedJobIds(new Set(['a']))).not.toThrow();
    });
  });

  // ── projectName ──

  describe('projectName', () => {
    it('extracts last segment from Windows path', () => {
      expect(projectName('C:\\Users\\test\\MyProject')).toBe('MyProject');
    });

    it('extracts last segment from Unix path', () => {
      expect(projectName('/home/user/project')).toBe('project');
    });

    it('handles mixed separators', () => {
      expect(projectName('C:/Users\\test/Project')).toBe('Project');
    });

    it('returns input for path without separators', () => {
      expect(projectName('MyProject')).toBe('MyProject');
    });

    it('handles empty string', () => {
      expect(projectName('')).toBe('');
    });

    it('handles trailing separators', () => {
      expect(projectName('C:\\test\\project\\')).toBe('project');
      expect(projectName('/test/project/')).toBe('project');
    });

    it('handles multiple trailing separators', () => {
      expect(projectName('C:\\test\\project\\\\\\')).toBe('project');
    });

    it('handles root path C:\\', () => {
      // 'C:\\' → split by /[\]/] → ['C:', ''] → pop() = '' → fallback to 'C:\\'
      // Actually: 'C:\\'.replace(/[\\/]+$/, '') = 'C:' → split → ['C:'] → pop = 'C:'
      const result = projectName('C:\\');
      expect(result).toBeTruthy();
    });

    it('handles root path /', () => {
      // projectName('/') = '/'.replace(/[\\/]+$/, '') = '' → split → [''] → pop = '' → '' is falsy → fallback to '/'
      expect(projectName('/')).toBe('/');
    });

    it('handles Unicode names', () => {
      expect(projectName('D:\\解包或汉化用\\我的项目')).toBe('我的项目');
    });
  });

  // ── formatDate ──

  describe('formatDate', () => {
    it('formats valid ISO date', () => {
      const result = formatDate('2024-01-15T10:30:00Z');
      expect(result).toBeTruthy();
      expect(typeof result).toBe('string');
    });

    it('returns input for invalid date string', () => {
      expect(formatDate('invalid-date')).toBe('invalid-date');
    });

    it('handles empty string', () => {
      expect(formatDate('')).toBe('');
    });

    it('handles date without time', () => {
      const result = formatDate('2024-01-15');
      expect(result).toBeTruthy();
    });

    it('handles Unix timestamp string', () => {
      // Not a valid ISO format, but Date constructor might parse it
      const result = formatDate('1705305600000');
      expect(typeof result).toBe('string');
    });

    it('handles far future date', () => {
      const result = formatDate('9999-12-31T23:59:59Z');
      expect(result).toBeTruthy();
    });

    it('handles year 1 date', () => {
      const result = formatDate('0001-01-01T00:00:00Z');
      expect(typeof result).toBe('string');
    });
  });
});
