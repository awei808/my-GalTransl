/**
 * API Client — HTTP wrapper, error handling, backend URL management.
 */
import { invoke } from '@tauri-apps/api/core';
import type { ErrorResponse } from './types';
import { getHideBackendConsolePreference } from './preferences';

const DEFAULT_BACKEND_URL = 'http://127.0.0.1:12333';
let runtimeBackendBaseUrl: string | null = null;

// ---- API Error ----

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

// ---- Internal ----

export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const baseUrl = getBackendBaseUrl();

  // 30 秒超时，防止请求挂死
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30000);
  const mergedInit: RequestInit = { ...init, signal: controller.signal };

  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, mergedInit);
  } catch (err: unknown) {
    clearTimeout(timeout);
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError(`请求超时：${baseUrl}${path}`, 408);
    }
    throw new ApiError(`无法连接到后端：${baseUrl}`, 0);
  }
  clearTimeout(timeout);

  const data = (await response.json().catch(() => ({}))) as T & ErrorResponse;
  if (!response.ok) {
    throw new ApiError(data.error || `请求失败：${response.status}`, response.status);
  }

  return data;
}

export function getBackendBaseUrl() {
  if (runtimeBackendBaseUrl) {
    return runtimeBackendBaseUrl;
  }
  const configured = import.meta.env.VITE_BACKEND_URL?.trim();
  return configured ? configured.replace(/\/$/, '') : DEFAULT_BACKEND_URL;
}

export function setRuntimeBackendBaseUrl(url: string | null) {
  runtimeBackendBaseUrl = url ? url.trim().replace(/\/+$/, '') : null;
}

function shouldUseManagedDesktopBackend() {
  const baseUrl = getBackendBaseUrl();

  if (baseUrl === DEFAULT_BACKEND_URL) {
    return true;
  }

  try {
    const parsed = new URL(baseUrl);
    return parsed.port === '12333' && (parsed.hostname === '127.0.0.1' || parsed.hostname === 'localhost');
  } catch {
    return false;
  }
}

// ---- Project ID helpers ----

export function encodeProjectDir(projectDir: string): string {
  const bytes = new TextEncoder().encode(projectDir);
  let binary = '';
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function decodeProjectDir(token: string): string {
  if (!token) return '';
  try {
    let base64 = token.replace(/-/g, '+').replace(/_/g, '/');
    while (base64.length % 4 !== 0) {
      base64 += '=';
    }
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    return new TextDecoder().decode(bytes);
  } catch {
    return '';
  }
}

// ---- Desktop backend management ----

export async function ensureDesktopBackendReady(options?: { hideConsole?: boolean; timeoutMs?: number }) {
  if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window) || !shouldUseManagedDesktopBackend()) {
    return null;
  }

  return invoke<string>('ensure_backend_ready', {
    hideConsole: options?.hideConsole ?? getHideBackendConsolePreference(),
    timeoutMs: options?.timeoutMs,
  });
}
