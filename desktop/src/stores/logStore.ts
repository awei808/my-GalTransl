import { createStore } from "solid-js/store";

export type LogLevel = "error" | "warning" | "info" | "success";

export interface LogEntry {
  id: string;
  ts: Date;
  level: LogLevel;
  message: string;
  source?: string; // e.g. "toast" | "action" | "api"
}

interface LogState {
  entries: LogEntry[];
  maxSize: number;
}

let counter = 0;
function uid() {
  return `log-${Date.now()}-${++counter}`;
}

const [logState, setLogState] = createStore<LogState>({
  entries: [],
  maxSize: 200,
});

/** 添加一条日志 */
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
    if (next.length > logState.maxSize) {
      return next.slice(next.length - logState.maxSize);
    }
    return next;
  });
  return entry;
}

/** 便捷方法 */
export const log = {
  error(msg: string, source?: string) { return pushLog("error", msg, source); },
  warning(msg: string, source?: string) { return pushLog("warning", msg, source); },
  info(msg: string, source?: string) { return pushLog("info", msg, source); },
  success(msg: string, source?: string) { return pushLog("success", msg, source); },
};

/** 获取所有日志 */
export function getLogs() {
  return logState.entries;
}

/** 清空日志 */
export function clearLogs() {
  setLogState("entries", []);
}

/** 按级别过滤 */
export function getLogsByLevel(level: LogLevel | "all") {
  if (level === "all") return logState.entries;
  return logState.entries.filter((e) => e.level === level);
}
