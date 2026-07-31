/**
 * 前端日志统一入口 — 所有前端日志经 POST /api/log 发往后端由单一 handler 收集。
 */
import { apiRequest } from "./client";

export type LogLevel = "debug" | "info" | "warn" | "error";

export function sendLog(
  message: string,
  level: LogLevel = "info",
  source = "frontend",
  projectId?: string,
): void {
  // 日志发送不应阻塞 UI；失败静默忽略，避免影响主流程
  const body: Record<string, unknown> = { source, level, message };
  if (projectId) body.project_id = projectId;
  apiRequest<{ ok: boolean }>("/api/log", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).catch(() => {
    /* 日志上报失败不影响业务 */
  });
}
