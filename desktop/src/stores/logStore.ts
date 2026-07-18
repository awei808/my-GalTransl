import { createStore } from "solid-js/store";
import { invoke } from "@tauri-apps/api/core";
import { appDataDir } from "@tauri-apps/api/path";

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

const [logState, setLogState] = createStore<LogState>({
  entries: [],
  maxSize: 200,
});

// ── 文件路径缓存 ──
let _logFilePath: string | null = null;

function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

async function resolveLogFilePath(): Promise<string | null> {
  if (!isTauri()) return null;
  if (_logFilePath) return _logFilePath;
  const dir = await appDataDir();
  try {
    await invoke("create_dir", { path: `${dir}logs` });
  } catch {}
  _logFilePath = `${dir}logs/frontend-${todayStr()}.log`;
  return _logFilePath;
}

function todayStr(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function logLine(entry: LogEntry): string {
  const time = entry.ts.toISOString();
  return `[${time}][${entry.level.toUpperCase()}]${entry.source ? `[${entry.source}]` : ""} ${entry.message}`;
}

// ── 异步写入文件（fire-and-forget） ──
function flushToFile(entry: LogEntry) {
  resolveLogFilePath()
    .then((path) => {
      if (!path) return;
      return invoke("append_text_file", { path, content: logLine(entry) });
    })
    .catch(() => {});
}

/** 添加一条日志（同步写入内存 + 异步写入文件） */
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
    return next.length > logState.maxSize
      ? next.slice(next.length - logState.maxSize)
      : next;
  });
  flushToFile(entry);
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

/** 清空日志（内存+文件） */
export function clearLogs() {
  setLogState("entries", []);
  resolveLogFilePath()
    .then((path) => {
      if (path) invoke("write_text_file", { path, content: "" });
    })
    .catch(() => {});
}

/** 按级别过滤 */
export function getLogsByLevel(level: LogLevel | "all") {
  if (level === "all") return logState.entries;
  return logState.entries.filter((e) => e.level === level);
}

/** 从文件加载今日日志到内存（去重合并） */
export async function loadLogsFromFile() {
  const path = await resolveLogFilePath();
  if (!path) return;
  try {
    const content: string = await invoke("read_text_file", { path });
    if (!content) return;
    const lines = content.trim().split("\n");
    const fileEntries: LogEntry[] = [];
    for (const line of lines) {
      const match = line.match(
        /^\[(.+?)\]\[(ERROR|WARNING|INFO|SUCCESS)\](?:\[(.+?)\])? (.+)$/
      );
      if (!match) continue;
      fileEntries.push({
        id: `file-${fileEntries.length}`,
        ts: new Date(match[1]),
        level: match[2].toLowerCase() as LogLevel,
        source: match[3] || undefined,
        message: match[4],
      });
    }
    // 合并去重
    setLogState("entries", (existing) => {
      const existingSet = new Set(
        existing.map((e) => `${e.ts.getTime()}-${e.level}-${e.message}`)
      );
      const toAdd = fileEntries.filter(
        (fe) =>
          !existingSet.has(`${fe.ts.getTime()}-${fe.level}-${fe.message}`)
      );
      const merged = [...existing, ...toAdd];
      merged.sort((a, b) => a.ts.getTime() - b.ts.getTime());
      return merged.length > logState.maxSize
        ? merged.slice(merged.length - logState.maxSize)
        : merged;
    });
  } catch {
    // 文件不存在 -> 首次运行，正常
  }
}
