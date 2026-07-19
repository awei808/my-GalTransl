import {
  createSignal,
  createEffect,
  onMount,
  onCleanup,
  Show,
} from "solid-js";
import { appState } from "../../stores/appStore";
import { toast } from "../../stores/toastStore";
import { confirm } from "../../stores/confirmStore";
import { fetchProjectRuntime, fetchProjectProgress, stopProjectTranslation } from "../../lib/api/project";
import { fetchTranslators, submitJob } from "../../lib/api/general";
import { decodeProjectDir } from "../../lib/api/client";
import type {
  ProjectRuntimeResponse,
  ProjectProgressResponse,
  TranslatorOption,
} from "../../lib/api/types";

/* ── 步骤指示器 ── */
function StepIndicator(props: { stage: string; index: number; total: number }) {
  const dots = () => {
    const arr: ("done" | "current" | "pending")[] = [];
    for (let i = 0; i < props.total; i++) {
      if (i < props.index) arr.push("done");
      else if (i === props.index) arr.push("current");
      else arr.push("pending");
    }
    return arr;
  };

  return (
    <div class="step-indicator">
      <div class="step-dots">
        {dots().map((s, i) => (
          <div
            class={`step-dot step-dot--${s}`}
            title={`步骤 ${i + 1}/${props.total}`}
          />
        ))}
      </div>
      <span class="step-label">
        {props.stage || "等待中"} ({props.index + 1}/{props.total})
      </span>
    </div>
  );
}

/* ── 统计数据行 ── */
function StatRow(props: { label: string; value: string | number }) {
  return (
    <div class="stat-row">
      <span class="stat-label">{props.label}</span>
      <span class="stat-value">{props.value}</span>
    </div>
  );
}

/* ── 翻译控制台 ── */
export function TranslateConsole() {
  const [runtime, setRuntime] = createSignal<ProjectRuntimeResponse | null>(null);
  const [progress, setProgress] = createSignal<ProjectProgressResponse | null>(null);
  const [translators, setTranslators] = createSignal<TranslatorOption[]>([]);
  const [selectedBackend, setSelectedBackend] = createSignal<string>("");
  const [running, setRunning] = createSignal(false);
  const [dropdownOpen, setDropdownOpen] = createSignal(false);
  const [submitting, setSubmitting] = createSignal(false);

  let pollTimer: ReturnType<typeof setInterval> | undefined;
  let dropdownRef: HTMLDivElement | undefined;
  let pollErrorCount = 0;
  let prevJobStatus = "";

  // 仅在项目打开时轮询
  createEffect(() => {
    const pid = appState.activeProjectId;
    if (!pid) {
      setRuntime(null);
      setProgress(null);
      setRunning(false);
      prevJobStatus = "";
      clearInterval(pollTimer);
      pollTimer = undefined;
      return;
    }

    async function poll() {
      try {
        const [rt, pg] = await Promise.all([
          fetchProjectRuntime(pid),
          fetchProjectProgress(pid),
        ]);
        pollErrorCount = 0;
        setRuntime(rt);
        setProgress(pg);
        const status = rt.job?.status;
        setRunning(status === "running");

        // 检测状态变更，弹出通知
        if (prevJobStatus && prevJobStatus !== status) {
          if (status === "completed") toast.success("翻译任务已完成");
          else if (status === "failed") toast.error(`翻译失败: ${rt.job?.error || "未知错误"}`);
          else if (status === "cancelled") toast.info("翻译任务已取消");
        }
        prevJobStatus = status || "";
      } catch {
        pollErrorCount++;
        if (pollErrorCount === 3) {
          toast.warning("后端连接异常，请检查后端是否运行");
        }
      }
    }

    poll();
    pollTimer = setInterval(poll, 3000);

    // 翻译器列表只加载一次
    if (translators().length === 0) {
      fetchTranslators()
        .then((list) => {
          setTranslators(list);
          if (list.length > 0 && !selectedBackend()) {
            setSelectedBackend(list[0].name);
          }
        })
        .catch(() => {});
    }
  });

  onCleanup(() => {
    clearInterval(pollTimer);
  });

  function handleStart() {
    const pid = appState.activeProjectId;
    if (!pid || !selectedBackend()) {
      toast.warning("请先选择后端并打开项目");
      return;
    }
    if (submitting()) return; // 防止重复点击
    // project_dir 必须是真实路径，不能是 base64 编码
    const realPath = decodeProjectDir(pid);
    if (!realPath) {
      toast.error("项目路径解析失败");
      return;
    }
    setSubmitting(true);
    submitJob({
      project_dir: realPath,
      config_file_name: "config.yaml",
      translator: selectedBackend(),
    })
      .then(() => toast.success("翻译任务已提交"))
      .catch((e) => toast.error(`提交失败: ${e.message}`))
      .finally(() => setSubmitting(false));
  }

  function handleStop() {
    const pid = appState.activeProjectId;
    if (!pid) return;

    confirm
      .show({
        title: "停止翻译",
        message: "确定要停止当前翻译任务吗？",
        tone: "warning",
      })
      .then((r) => {
        if (r.confirmed) {
          stopProjectTranslation(pid)
            .then(() => toast.info("翻译已停止"))
            .catch((e: Error) => toast.error(`停止失败: ${e.message}`));
        }
      });
  }

  // 点击外部关闭下拉菜单
  function handleOutsideClick(e: MouseEvent) {
    if (dropdownRef && !dropdownRef.contains(e.target as Node)) {
      setDropdownOpen(false);
    }
  }
  onMount(() => document.addEventListener("click", handleOutsideClick));
  onCleanup(() => document.removeEventListener("click", handleOutsideClick));

  const summary = () => runtime()?.summary;
  const hasProject = () => !!appState.activeProjectId;
  const isRunning = () => running();

  return (
    <div class="page page-translate">
      <Show
        when={hasProject()}
        fallback={
          <div class="translate-empty">
            <p>请先打开翻译项目</p>
          </div>
        }
      >
        {/* ── 顶部统计区 4:1 ── */}
        <div class="translate-header">
          <div class="translate-stats">
            <div class="stats-grid">
              <StatRow
                label="总行数"
                value={summary()?.total ?? "—"}
              />
              <StatRow
                label="已翻译"
                value={
                  summary()
                    ? `${summary()!.translated} / ${summary()!.total}`
                    : "—"
                }
              />
              <StatRow
                label="进度"
                value={
                  summary()
                    ? `${summary()!.percent.toFixed(1)}%`
                    : "—"
                }
              />
            </div>
            {/* 进度条 */}
            <div class="progress-bar-container">
              <div
                class="progress-bar-fill"
                style={{
                  width: `${summary()?.percent ?? 0}%`,
                }}
              />
            </div>
            {/* 步骤指示器 */}
            <StepIndicator
              stage={runtime()?.stage ?? ""}
              index={runtime()?.stage_index ?? 0}
              total={runtime()?.stage_total ?? 1}
            />
            <div class="stats-row-secondary">
              <Show when={summary()}>
                <span>
                  速度: {summary()!.translation_speed_lpm.toFixed(1)} 条/秒
                </span>
                <Show when={summary()!.eta_seconds != null}>
                  <span>
                    ETA: {formatETA(summary()!.eta_seconds!)}
                  </span>
                </Show>
              </Show>
              <Show when={runtime()?.current_file}>
                <span class="current-file">
                  当前文件: {runtime()!.current_file}
                </span>
              </Show>
            </div>
          </div>

          {/* ── 右侧操作区 ── */}
          <div class="translate-actions">
            <div class="backend-selector" ref={dropdownRef}>
              <Show when={!isRunning()}>
                <div class="backend-select-wrapper">
                  <div
                    class="backend-select-trigger"
                    onClick={() => setDropdownOpen(!dropdownOpen())}
                  >
                    <span>{selectedBackend() || "选择后端"}</span>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                      <path d="M5 9l7 7 7-7" />
                    </svg>
                  </div>
                  <Show when={dropdownOpen()}>
                    <div class="backend-dropdown">
                      {translators().map((t) => (
                        <div
                          class={`backend-option ${selectedBackend() === t.name ? "active" : ""}`}
                          onClick={() => {
                            setSelectedBackend(t.name);
                            setDropdownOpen(false);
                          }}
                        >
                          {t.name}
                        </div>
                      ))}
                    </div>
                  </Show>
                </div>
                <button class="btn btn--primary" onClick={handleStart} disabled={submitting()}>
                  {submitting() ? "提交中…" : "启动流程"}
                </button>
              </Show>
              <Show when={isRunning()}>
                <button class="btn btn--danger" onClick={handleStop}>
                  停止翻译
                </button>
              </Show>
            </div>
          </div>
        </div>

        {/* ── 下方两栏 ── */}
        <div class="translate-body">
          <div class="translate-panel">
            <div class="panel-header">当前提示词</div>
            <div class="panel-content">
              {runtime()?.latest_prompt_preview || "等待翻译开始…"}
            </div>
          </div>
          <div class="translate-divider" />
          <div class="translate-panel">
            <div class="panel-header">译文拼接</div>
            <div class="panel-content">
              {runtime()?.latest_assembled_preview || "等待翻译开始…"}
            </div>
          </div>
        </div>
      </Show>
    </div>
  );
}

function formatETA(seconds: number): string {
  if (seconds <= 0) return "计算中…";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  if (m >= 60) {
    const h = Math.floor(m / 60);
    return `${h}h ${m % 60}m`;
  }
  return `${m}分 ${s}秒`;
}
