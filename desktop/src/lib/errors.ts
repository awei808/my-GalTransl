import { ApiError } from "./api";

export function normalizeError(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    return error.message;
  }

  if (error instanceof Error && error.message.trim()) {
    return error.message;
  }

  return fallback;
}

/** 从未知类型的错误中取出可读消息（用于 catch 块，避免 any）。 */
export function getErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) {
    return error.message;
  }
  return String(error);
}

/**
 * 是否为「请求超时」——apiRequest 到点主动 abort 时抛出的 408。
 * 超时不等于业务失败：后端可能仍在处理（如慢模型的 token 探活），
 * 调用方应据此避免自动重试，否则会让后端重复跑完整流程。
 */
export function isApiTimeoutError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 408;
}
