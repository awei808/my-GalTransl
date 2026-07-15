import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  ApiError,
  encodeProjectDir,
  decodeProjectDir,
  setRuntimeBackendBaseUrl,
  getBackendBaseUrl,
} from '../client';

describe('encodeProjectDir / decodeProjectDir — edge cases', () => {
  it('round-trip with empty string', () => {
    const encoded = encodeProjectDir('');
    expect(encoded).toBe('');
    expect(decodeProjectDir(encoded)).toBe('');
  });

  it('round-trip with ASCII path', () => {
    const path = 'C:\\Users\\test\\project';
    const encoded = encodeProjectDir(path);
    expect(decodeProjectDir(encoded)).toBe(path);
  });

  it('round-trip with Unicode path', () => {
    const path = 'D:\\解包或汉化用\\我的项目';
    const encoded = encodeProjectDir(path);
    expect(decodeProjectDir(encoded)).toBe(path);
  });

  it('round-trip with spaces and special chars', () => {
    const path = 'C:\\Program Files (x86)\\test [v2] & more';
    const encoded = encodeProjectDir(path);
    expect(decodeProjectDir(encoded)).toBe(path);
  });

  it('round-trip with very long path', () => {
    const path = 'C:\\' + 'a'.repeat(10000);
    const encoded = encodeProjectDir(path);
    expect(decodeProjectDir(encoded)).toBe(path);
  });

  it('round-trip with forward slashes', () => {
    const path = '/home/user/project';
    const encoded = encodeProjectDir(path);
    expect(decodeProjectDir(encoded)).toBe(path);
  });

  it('round-trip with Japanese characters', () => {
    const path = 'C:\\Users\\テスト\\プロジェクト';
    const encoded = encodeProjectDir(path);
    expect(decodeProjectDir(encoded)).toBe(path);
  });

  it('encoded output contains no + or / (URL-safe base64url)', () => {
    const paths = [
      'C:\\test\\+++path',
      'D:\\folder/sub',
      'E:\\??\\!!',
    ];
    for (const path of paths) {
      const encoded = encodeProjectDir(path);
      expect(encoded).not.toMatch(/[+/=]/);
    }
  });

  it('decodeProjectDir handles already-encoded token', () => {
    const original = 'C:\\test\\path';
    const encoded = encodeProjectDir(original);
    // Double-encode then single-decode should NOT give original
    const doubleEncoded = encodeProjectDir(encoded);
    expect(decodeProjectDir(doubleEncoded)).toBe(encoded);
  });

  it('decodeProjectDir with invalid base64 returns empty string', () => {
    expect(decodeProjectDir('!!!invalid!!!')).toBe('');
  });

  it('decodeProjectDir with empty string returns empty', () => {
    expect(decodeProjectDir('')).toBe('');
  });

  it('decodeProjectDir with padding-only string returns empty', () => {
    expect(decodeProjectDir('====')).toBe('');
  });
});

describe('ApiError — edge cases', () => {
  it('constructs with status 0 (network error)', () => {
    const err = new ApiError('network error', 0);
    expect(err.status).toBe(0);
    expect(err.message).toBe('network error');
    expect(err.name).toBe('ApiError');
  });

  it('constructs with status 404', () => {
    const err = new ApiError('not found', 404);
    expect(err.status).toBe(404);
  });

  it('constructs with status 500', () => {
    const err = new ApiError('server error', 500);
    expect(err.status).toBe(500);
  });

  it('constructs with negative status (unusual but not prevented)', () => {
    const err = new ApiError('weird', -1);
    expect(err.status).toBe(-1);
  });

  it('constructs with very large status', () => {
    const err = new ApiError('huge', 99999);
    expect(err.status).toBe(99999);
  });

  it('constructs with empty message', () => {
    const err = new ApiError('', 400);
    expect(err.message).toBe('');
  });

  it('constructs with unicode message', () => {
    const err = new ApiError('连接失败', 503);
    expect(err.message).toBe('连接失败');
  });

  it('is an instance of Error', () => {
    const err = new ApiError('test', 500);
    expect(err).toBeInstanceOf(Error);
  });

  it('can be thrown and caught', () => {
    expect(() => {
      throw new ApiError('thrown', 400);
    }).toThrow(ApiError);
  });
});

describe('setRuntimeBackendBaseUrl / getBackendBaseUrl — edge cases', () => {
  beforeEach(() => {
    setRuntimeBackendBaseUrl(null);
  });

  it('returns default URL when no runtime URL set', () => {
    expect(getBackendBaseUrl()).toBe('http://127.0.0.1:12333');
  });

  it('sets and gets runtime URL', () => {
    setRuntimeBackendBaseUrl('http://localhost:8080');
    expect(getBackendBaseUrl()).toBe('http://localhost:8080');
  });

  it('trims trailing slash from runtime URL', () => {
    setRuntimeBackendBaseUrl('http://localhost:8080/');
    expect(getBackendBaseUrl()).toBe('http://localhost:8080');
  });

  it('trims multiple trailing slashes', () => {
    setRuntimeBackendBaseUrl('http://localhost:8080///');
    expect(getBackendBaseUrl()).toBe('http://localhost:8080');
  });

  it('trims whitespace from runtime URL', () => {
    setRuntimeBackendBaseUrl('  http://localhost:8080  ');
    expect(getBackendBaseUrl()).toBe('http://localhost:8080');
  });

  it('null clears runtime URL (falls back to default)', () => {
    setRuntimeBackendBaseUrl('http://localhost:8080');
    setRuntimeBackendBaseUrl(null);
    expect(getBackendBaseUrl()).toBe('http://127.0.0.1:12333');
  });

  it('empty string clears runtime URL', () => {
    setRuntimeBackendBaseUrl('http://localhost:8080');
    setRuntimeBackendBaseUrl('');
    expect(getBackendBaseUrl()).toBe('http://127.0.0.1:12333');
  });

  it('whitespace-only string clears runtime URL', () => {
    setRuntimeBackendBaseUrl('   ');
    expect(getBackendBaseUrl()).toBe('http://127.0.0.1:12333');
  });

  it('URL with path preserves path', () => {
    setRuntimeBackendBaseUrl('http://localhost:8080/api/v2');
    expect(getBackendBaseUrl()).toBe('http://localhost:8080/api/v2');
  });

  it('URL with port and trailing slash + path', () => {
    setRuntimeBackendBaseUrl('http://localhost:8080/api/');
    expect(getBackendBaseUrl()).toBe('http://localhost:8080/api');
  });

  it('rapid switching between URLs', () => {
    setRuntimeBackendBaseUrl('http://a:1');
    setRuntimeBackendBaseUrl('http://b:2');
    setRuntimeBackendBaseUrl('http://c:3');
    expect(getBackendBaseUrl()).toBe('http://c:3');

    setRuntimeBackendBaseUrl(null);
    expect(getBackendBaseUrl()).toBe('http://127.0.0.1:12333');
  });
});
