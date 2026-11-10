/**
 * FixReportPanel — 本次任务修复/改进报告（按文件分组展示）。
 *
 * 数据来自 GET /api/projects/:id/fix-report：后端在修复/改进轮采纳点采集
 * （BaseSparseFixRound._parse_fix_response），任务收尾时持久化，由翻译控制台
 * 在任务完成或用户打开"修复报告"标签页时拉取并经 props 传入。
 *
 * 复用 problem-* 全局样式类（与 SidebarPanel 的 ProblemList/AltList 同构）：
 * 统计条 + 按文件分组折叠列表；点击条目跳校对页定位到该句。
 */
import { createSignal, For, Show } from "solid-js";

import { appState, setAppState } from "../stores/appStore";
import type { AppState } from "../stores/appStore";
import { engineShortName } from "../lib/engineDisplay";
import type {
  FixReportEntry,
  FixReportFileGroup,
  FixReportResponse,
} from "../lib/api/types";

/** 问题类型 chip 文案截断上限（problems 含"类型：细节"，细节可能较长） */
const PROBLEM_CHIP_MAX = 24;

function splitProblemNames(problems: string): string[] {
  return problems
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function formatTime(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function FixReportPanel(props: { report: FixReportResponse | null }) {
  // 已展开的文件集合（默认折叠，点击文件行右侧图标展开；内联读取以启用细粒度追踪）
  const [expandedFiles, setExpandedFiles] = createSignal<Set<string>>(new Set());
  function toggleFile(filename: string) {
    setExpandedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(filename)) next.delete(filename);
      else next.add(filename);
      return next;
    });
  }

  const groups = (): FixReportFileGroup[] => props.report?.files ?? [];

  function jumpToEntry(filename: string, index: number) {
    const patch: Partial<AppState> = {
      activeView: "review",
      reviewJumpToIndex: index,
    };
    // 仅当切换文件时才设 activeFilePath，同文件跳转不用重载
    if (filename !== appState.activeFilePath) {
      patch.activeFilePath = filename;
    }
    setAppState(patch);
  }

  return (
    <div class="fix-report-panel">
      <Show
        when={(props.report?.total ?? 0) > 0}
        fallback={
          <p class="sidebar-placeholder">
            {!props.report?.exists
              ? "暂无修复/改进记录"
              : !props.report.finished_at
                ? "任务尚未结束，修复报告待本次执行完成后生成"
                : "本次任务暂无修复/改进记录"}
          </p>
        }
      >
        <div class="problem-stats">
          <span class="problem-stats-total">
            共修复 {props.report?.total ?? 0} 条 · 涉及 {groups().length} 个文件
          </span>
          <Show when={formatTime(props.report?.finished_at ?? "")}>
            <span class="fix-report-meta">完成于 {formatTime(props.report?.finished_at ?? "")}</span>
          </Show>
        </div>
        <For each={groups()}>
          {(group) => (
            <div class="problem-group">
              {/* 文件行：左侧文件名，右侧展开/收起切换图标 */}
              <div class="problem-filename-row">
                <span class="problem-filename">{group.filename}</span>
                <span class="problem-filename-count">
                  {group.count}
                  {group.truncated ? "+" : ""}
                </span>
                <button
                  class="problem-toggle"
                  data-open={expandedFiles().has(group.filename)}
                  aria-expanded={expandedFiles().has(group.filename)}
                  aria-label={
                    expandedFiles().has(group.filename)
                      ? "收起修复列表"
                      : "展开修复列表"
                  }
                  onClick={() => toggleFile(group.filename)}
                >
                  {/* chevron-down：展开朝下，收起经 CSS rotate(-90deg) 平滑变为朝右 */}
                  <svg
                    class="problem-toggle-icon"
                    viewBox="0 0 16 16"
                    width="14"
                    height="14"
                    aria-hidden="true"
                  >
                    <path
                      d="M4 6l4 4 4-4"
                      fill="none"
                      stroke="currentColor"
                      stroke-width="1.6"
                      stroke-linecap="round"
                      stroke-linejoin="round"
                    />
                  </svg>
                </button>
              </div>
              <Show when={expandedFiles().has(group.filename)}>
                <For each={group.entries}>
                  {(entry) => (
                    <FixReportEntryRow
                      entry={entry}
                      onJump={() => jumpToEntry(entry.filename, entry.index)}
                    />
                  )}
                </For>
                <Show when={group.truncated}>
                  <p class="fix-report-meta fix-report-more">
                    明细较多，仅显示前 {group.entries.length} 条（共 {group.count} 条）
                  </p>
                </Show>
              </Show>
            </div>
          )}
        </For>
        <Show when={props.report?.truncated}>
          <p class="fix-report-meta fix-report-more">明细超出展示上限，部分记录未展示（计数仍完整）</p>
        </Show>
      </Show>
    </div>
  );
}

function FixReportEntryRow(props: { entry: FixReportEntry; onJump: () => void }) {
  const problemChips = () =>
    splitProblemNames(props.entry.problems).map((name) =>
      name.length > PROBLEM_CHIP_MAX ? name.slice(0, PROBLEM_CHIP_MAX) + "…" : name,
    );
  return (
    <div class="problem-entry" onClick={() => props.onJump()}>
      <span class="problem-index">#{props.entry.index}</span>
      <div class="fix-report-entry-body">
        <div class="fix-report-old">{props.entry.old_dst}</div>
        <div class="fix-report-new">{props.entry.new_dst}</div>
        <div class="fix-report-chips">
          <Show
            when={props.entry.writes_main}
            fallback={<span class="fix-report-chip">写入备选译文</span>}
          >
            <span class="fix-report-chip fix-report-chip--main">已覆盖主译文</span>
          </Show>
          <Show when={props.entry.backend}>
            <span class="fix-report-chip">{engineShortName(props.entry.backend)}</span>
          </Show>
          <Show when={props.entry.speaker}>
            <span class="fix-report-chip">{props.entry.speaker}</span>
          </Show>
          <For each={problemChips()}>
            {(name) => <span class="fix-report-chip">{name}</span>}
          </For>
        </div>
      </div>
    </div>
  );
}
