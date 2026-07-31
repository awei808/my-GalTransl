import { createStore } from "solid-js/store";
import { sendLog, type LogLevel as BackendLogLevel } from "../lib/api/log";

export type LogLevel = "error" | "warning" | "info" | "success";

export interface LogEntry {
  id: string;
  ts: Date;
  level: LogLevel;
  message: string;
  source?: string;
}

interface LogState {
  entries: LogEntry[];
  maxSize: number;
}

let counter = 0;
function uid() {
  return `log-${Date.now()}-${++counter}`;
}

// 当前活动翻译项目的 id，由 App 在项目切换时同步；用于把前端日志归集到对应项目目录。
let currentProjectId: string | undefined;

/** 由 App 在项目切换时调用，使后续前端日志带上 project_id，归集到对应翻译项目目录。 */
export function setLogProject(projectId: string | null | undefined): void {
  currentProjectId = projectId || undefined;
}

const [logState, setLogState] = createStore<LogState>({
  entries: [],
  maxSize: 200,
});

// 后端日志级别（api/log）较前端细分，这里做映射：success->info、warning->warn
function toBackendLevel(level: LogLevel): BackendLogLevel {
  if (level === "warning") return "warn";
  if (level === "success") return "info";
  return level;
}

/** 添加一条日志（内存留存 + 上报后端统一落盘 frontend.log） */
export function pushLog(level: LogLevel, message: string, source?: string) {
  const entry: LogEntry = {
    id: uid(),
    ts: new Date(),
    level,
    message,
    source,
  };
  setLogState("entries", (entries) => {
    const next = [...entries, entry];
    return next.length > logState.maxSize ? next.slice(next.length - logState.maxSize) : next;
  });
  sendLog(message, toBackendLevel(level), source || "frontend", currentProjectId);
  return entry;
}

/** 便捷方法 */
export const log = {
  error(msg: string, source?: string) {
    return pushLog("error", msg, source);
  },
  warning(msg: string, source?: string) {
    return pushLog("warning", msg, source);
  },
  info(msg: string, source?: string) {
    return pushLog("info", msg, source);
  },
  success(msg: string, source?: string) {
    return pushLog("success", msg, source);
  },
};

/** 获取所有日志 */
export function getLogs() {
  return logState.entries;
}

/** 清空日志（仅内存；后端 frontend.log 由后端统一管理） */
export function clearLogs() {
  setLogState("entries", []);
}

/** 按级别过滤 */
export function getLogsByLevel(level: LogLevel | "all") {
  if (level === "all") return logState.entries;
  return logState.entries.filter((e) => e.level === level);
}
