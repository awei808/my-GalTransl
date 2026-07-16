import { describe, it, expect, beforeEach, vi } from 'vitest';
import { apiRequest, ApiError } from '../client';

describe('apiRequest — HTTP error handling (P1)', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('returns JSON on 200', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify({ status: 'ok' }), { status: 200 }),
    );
    const result = await apiRequest<{ status: string }>('/api/test');
    expect(result).toEqual({ status: 'ok' });
  });

  it('throws ApiError on 400', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify({ error: 'Bad request' }), { status: 400 }),
    );
    await expect(apiRequest('/api/test')).rejects.toThrow(ApiError);
  });

  it('throws ApiError on 404', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response('', { status: 404 }),
    );
    await expect(apiRequest('/api/test')).rejects.toThrow(ApiError);
  });

  it('throws ApiError on 500 with status code', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response('', { status: 500 }),
    );
    try {
      await apiRequest('/api/test');
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(500);
    }
  });

  it('handles network error with status 0', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(new Error('Failed to fetch'));
    try {
      await apiRequest('/api/test');
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(0);
      expect((err as ApiError).message).toContain('无法连接到后端');
    }
  });

  it('passes init to fetch', async () => {
    const mockFetch = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify({}), { status: 200 }),
    );
    await apiRequest('/api/test', { method: 'DELETE' });
    const args = mockFetch.mock.calls[0] as [string, RequestInit?];
    expect(args[1]?.method).toBe('DELETE');
  });
});

describe('ApiError class', () => {
  it('creates error with message and status', () => {
    const err = new ApiError('Something went wrong', 500);
    expect(err.message).toBe('Something went wrong');
    expect(err.status).toBe(500);
    expect(err.name).toBe('ApiError');
    expect(err).toBeInstanceOf(Error);
  });

  it('network error has status 0', () => {
    const err = new ApiError('无法连接到后端：http://127.0.0.1:12333', 0);
    expect(err.status).toBe(0);
    expect(err.message).toContain('无法连接');
  });
});
