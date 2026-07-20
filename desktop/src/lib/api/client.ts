/**
 * API Client — HTTP wrapper, error handling, backend URL management.
 */
import { invoke } from "@tauri-apps/api/core";
import type { ErrorResponse } from "./types";
import { getHideBackendConsolePreference } from "./preferences";

const DEFAULT_BACKEND_URL = "http://127.0.0.1:12333";
let runtimeBackendBaseUrl: string | null = null;

// ---- API Error ----

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
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
  return configured ? configured.replace(/\/$/, "") : DEFAULT_BACKEND_URL;
}

export function setRuntimeBackendBaseUrl(url: string | null) {
  runtimeBackendBaseUrl = url ? url.trim().replace(/\/+$/, "") : null;
}

function shouldUseManagedDesktopBackend() {
  const baseUrl = getBackendBaseUrl();

  if (baseUrl === DEFAULT_BACKEND_URL) {
    return true;
  }

  try {
    const parsed = new URL(baseUrl);
    return (
      parsed.port === "12333" &&
      (parsed.hostname === "127.0.0.1" || parsed.hostname === "localhost")
    );
  } catch {
    return false;
  }
}

// ---- Project ID helpers ----

export function encodeProjectDir(projectDir: string): string {
  const bytes = new TextEncoder().encode(projectDir);
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function decodeProjectDir(token: string): string {
  if (!token) return "";
  try {
    let base64 = token.replace(/-/g, "+").replace(/_/g, "/");
    while (base64.length % 4 !== 0) {
      base64 += "=";
    }
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i);
    }
    return new TextDecoder().decode(bytes);
  } catch {
    return "";
  }
}

/**
 * 探测项目目录下真实配置文件名（config.inc.yaml 优先于 config.yaml）。
 * 前端打开项目时调用，用于将真实配置名贯通到各 API，避免写死 config.yaml。
 */
export async function fetchProjectConfigName(projectId: string): Promise<string> {
  const data = await apiRequest<{ project_dir: string; config_file_name: string }>(
    `/api/projects/${projectId}/config-name`,
  );
  return data.config_file_name || "config.yaml";
}

// ---- Desktop backend management ----

/** 轻量探活：后端是否已在监听（用于避免无谓的启动流程与误导性提示）。超时即视为不可达。 */
export async function isBackendReachable(timeoutMs = 800): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(`${getBackendBaseUrl()}/api/version`, {
      method: "GET",
      signal: controller.signal,
    });
    return resp.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

export async function ensureDesktopBackendReady(options?: {
  hideConsole?: boolean;
  timeoutMs?: number;
}) {
  if (
    typeof window === "undefined" ||
    !("__TAURI_INTERNALS__" in window) ||
    !shouldUseManagedDesktopBackend()
  ) {
    return null;
  }

  // 快速探活：后端已在监听时直接返回，跳过 Rust spawn_blocking 与无谓的冷启动
  if (await isBackendReachable(800)) {
    return "backend-already-ready";
  }

  try {
    return await invoke<string>("ensure_backend_ready", {
      hideConsole: options?.hideConsole ?? getHideBackendConsolePreference(),
      timeoutMs: options?.timeoutMs,
    });
  } catch (e) {
    // 兜底：Rust 端可能因探活偶发失败 / 冷启动刚超时而报错，但 HTTP 实际已可达
    // （后端刚起来、或 Rust 的 tcp_port_listening 对已在监听的端口偶发误判），
    // 此时视为就绪——项目仍可通过 HTTP 正常打开，避免误报“无法启动后端服务”。
    if (await isBackendReachable(800)) {
      return "backend-already-ready";
    }
    throw e;
  }
}
