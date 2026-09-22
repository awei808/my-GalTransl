/**
 * MCP 连接状态 API — 读取后端 /api/mcp-status。
 *
 * MCP 服务是被外部 agent 按需拉起的独立进程，后端靠心跳文件判定其存活
 * （见 GalTransl/mcp_heartbeat.py），前端只消费该判定结果。
 */
import { apiRequest } from "./client";

/** 后端 /api/mcp-status 的响应结构 */
export interface McpStatus {
  /** 心跳是否新鲜（90s 内刷新过） */
  available: boolean;
  /** 心跳文件绝对路径 */
  path: string;
  /** MCP 进程 pid（仅参考，不可用于存活判定） */
  pid: number | null;
  /** 对外暴露的工具数量 */
  tools: number;
  started_at: string;
  updated_at: string;
  /** 心跳文件距今秒数 */
  age_seconds: number | null;
  stale_after_seconds: number;
}

/** 读取 MCP 连接状态；失败由调用方决定降级表现 */
export function fetchMcpStatus(): Promise<McpStatus> {
  return apiRequest<McpStatus>("/api/mcp-status");
}
