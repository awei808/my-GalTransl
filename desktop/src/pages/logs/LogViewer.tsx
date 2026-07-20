import { createSignal, For, Show, onMount } from "solid-js";
import { getLogs, clearLogs, getLogsByLevel, loadLogsFromFile } from "../../stores/logStore";
import type { LogLevel } from "../../stores/logStore";

export function LogViewer() {
  const [filter, setFilter] = createSignal<LogLevel | "all">("all");

  // 挂载时加载文件日志
  onMount(() => {
    loadLogsFromFile();
  });

  function handleClear() {
    clearLogs();
  }

  const logs = () => getLogsByLevel(filter());
  const allCount = () => getLogs().length;

  function levelIcon(level: LogLevel) {
    switch (level) {
      case "error":
        return "✕";
      case "warning":
        return "⚠";
      case "info":
        return "ℹ";
      case "success":
        return "✓";
    }
  }

  function levelClass(level: LogLevel) {
    return `log-entry log--${level}`;
  }

  function fmtTime(d: Date) {
    return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  const filters: (LogLevel | "all")[] = ["all", "error", "warning", "info", "success"];

  return (
    <div class="page page-logs">
      <div class="logs-toolbar">
        <h2 class="page-title" style={{ "margin-bottom": 0 }}>
          操作日志
        </h2>
        <div class="logs-actions">
          <div class="logs-filters">
            <For each={filters}>
              {(f) => (
                <button
                  class={`btn btn--sm ${filter() === f ? "btn--primary" : ""}`}
                  onClick={() => setFilter(f)}
                >
                  {f === "all" ? `全部 (${allCount()})` : f}
                </button>
              )}
            </For>
          </div>
          <button class="btn btn--sm" onClick={handleClear}>
            清空
          </button>
        </div>
      </div>

      <Show
        when={logs().length > 0}
        fallback={
          <div class="logs-empty">
            <p>暂无操作日志</p>
            <p class="logs-empty-hint">所有操作记录将自动保存到文件。</p>
          </div>
        }
      >
        <div class="logs-list">
          <For each={logs().slice().reverse()}>
            {(entry) => (
              <div class={levelClass(entry.level)}>
                <span class="log-level">{levelIcon(entry.level)}</span>
                <span class="log-time">{fmtTime(entry.ts)}</span>
                <span class="log-source" title={entry.source}>
                  {entry.source}
                </span>
                <span class="log-msg">{entry.message}</span>
              </div>
            )}
          </For>
        </div>
      </Show>
    </div>
  );
}
