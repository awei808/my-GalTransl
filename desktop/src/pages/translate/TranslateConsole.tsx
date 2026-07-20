import { createSignal, createEffect, onMount, onCleanup, Show } from "solid-js";
import { appState, getActiveConfigFileName, navigateTo } from "../../stores/appStore";
import { toast } from "../../stores/toastStore";
import { getErrorMessage } from "../../lib/errors";
import { confirm } from "../../stores/confirmStore";
import {
  fetchProjectRuntime,
  stopProjectTranslation,
} from "../../lib/api/project";
import { fetchTranslators, submitJob, checkModelAvailability } from "../../lib/api/general";
import { decodeProjectDir } from "../../lib/api/client";
import { resolveSelectedBackendProfile, getSelectedBackendProfileJobPayload } from "../../lib/api/preferences";
import type {
  ModelCheckResult,
  ProjectRuntimeResponse,
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
          <div class={`step-dot step-dot--${s}`} title={`步骤 ${i + 1}/${props.total}`} />
        ))}
      </div>
      <span class="step-label">
        {props.stage || "等待中"} ({props.index + 1}/{props.total})
      </span>
    </div>
  );
}

/* ── 统计数据行 ── */
function StatRow(props: { label: string; value: string | number; tone?: "error" | "default" }) {
  return (
    <div class="stat-row">
      <span class="stat-label">{props.label}</span>
      <span class={`stat-value ${props.tone === "error" ? "stat-value--error" : ""}`}>
        {props.value}
      </span>
    </div>
  );
}

/* ── 翻译控制台 ── */
export function TranslateConsole() {
  const [runtime, setRuntime] = createSignal<ProjectRuntimeResponse | null>(null);
  const [translators, setTranslators] = createSignal<TranslatorOption[]>([]);
  const [selectedBackend, setSelectedBackend] = createSignal<string>("");
  const [running, setRunning] = createSignal(false);
  const [dropdownOpen, setDropdownOpen] = createSignal(false);
  const [submitting, setSubmitting] = createSignal(false);

  // 模型可用性检测状态
  type ModelCheckState = "idle" | "checking" | "ok" | "error" | "na";
  const [modelCheckState, setModelCheckState] = createSignal<ModelCheckState>("idle");
  const [modelCheckResult, setModelCheckResult] = createSignal<ModelCheckResult | null>(null);
  let checkingToken = 0; // 防止并发/过期响应覆盖最新结果
  let retryTimer: ReturnType<typeof setTimeout> | undefined; // 自动重试定时器
  const MAX_AUTO_RETRIES = 3; // 错误状态下最多自动重试 3 次

  let pollTimer: ReturnType<typeof setInterval> | undefined;
  let dropdownRef: HTMLDivElement | undefined;
  let pollErrorCount = 0;
  let prevJobStatus = "";

  // 仅在项目打开时轮询
  createEffect(() => {
    const pid = appState.activeProjectId;
    if (!pid) {
      setRuntime(null);
      setRunning(false);
      prevJobStatus = "";
      clearInterval(pollTimer);
      pollTimer = undefined;
      return;
    }

    const projectId = pid;

    async function poll() {
      try {
        const rt = await fetchProjectRuntime(projectId);
        pollErrorCount = 0;
        setRuntime(rt);
        const status = rt.job?.status;
        // pending（已排队）/ running 都视为运行中，确保有反馈与停止按钮
        setRunning(status === "running" || status === "pending");

        // 检测状态变更并通知用户；失败时主动停止任务
        if (status && prevJobStatus !== status) {
          if (status === "running") {
            toast.info("翻译任务已开始");
          } else if (status === "completed") {
            toast.success("翻译任务已完成");
          } else if (status === "failed") {
            toast.error(`翻译失败: ${rt.job?.error || "未知错误"}`);
            // 主动停止任务，清理后端残留进程
            stopProjectTranslation(projectId)
              .then(() => {})
              .catch(() => {});
          } else if (status === "cancelled") {
            toast.info("翻译任务已取消");
          }
        }
        if (status) prevJobStatus = status;
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
    clearTimeout(retryTimer);
  });

  // 主动检测所选后端的模型可用性
  let autoRetryCount = 0;
  async function runModelCheck(isAutoRetry = false) {
    const pid = appState.activeProjectId;
    const backend = selectedBackend();
    if (!pid || !backend) return;
    // 自动重试仅对 error 状态触发
    if (isAutoRetry && modelCheckState() !== "error") return;

    const realPath = decodeProjectDir(pid);
    if (!realPath) return;
    // 统一使用程序全局「后端配置」中的令牌进行检测（不再读项目自身 config.yaml 的 tokens）
    const { name, profile } = resolveSelectedBackendProfile(realPath);

    const token = ++checkingToken;
    setModelCheckState("checking");
    setModelCheckResult(null);
    try {
      const res = await checkModelAvailability({
        projectId: pid,
        translator: backend,
        configFileName: getActiveConfigFileName(),
        backendProfile: name,
        backendProfileData: profile ?? undefined,
      });
      if (token !== checkingToken) return; // 已有更新的请求，丢弃本次
      setModelCheckResult(res);
      if (!res.applicable) {
        setModelCheckState("na"); // 本地/特殊端点，无需 token 检测
      } else {
        setModelCheckState(res.ok ? "ok" : "error");
      }
      // 检测成功或不再适用，清除重试状态
      autoRetryCount = 0;
      clearTimeout(retryTimer);
      retryTimer = undefined;
    } catch (e) {
      if (token !== checkingToken) return;
      setModelCheckResult({
        ok: false,
        applicable: true,
        available: 0,
        total: 0,
        engine: backend,
        message: getErrorMessage(e) || "检测请求失败",
      });
      setModelCheckState("error");
      // 错误状态下自动重试（最多 MAX_AUTO_RETRIES 次，间隔 10 秒）
      if (!isAutoRetry && autoRetryCount < MAX_AUTO_RETRIES) {
        autoRetryCount++;
        retryTimer = setTimeout(() => runModelCheck(true), 10000);
      }
    }
  }

  // 后端变化（或项目打开、切换后端、配置名解析完成）时主动触发一次检测。
  // 注意：这里刻意不依赖 isRunning()——否则任务一取消(running 翻转为 false)
  // 就会重跑检测，而刚取消时后端可能正处于收尾态，检测瞬败会被误判为
  // “模型可用性检测失败”并触发自动重试。手动停止由 handleStop 单独处理。
  createEffect(() => {
    const pid = appState.activeProjectId;
    const backend = selectedBackend();
    void appState.activeConfigFileName; // 依赖：真实配置名解析完成后重跑检测
    if (!pid || !backend) return;
    runModelCheck();
  });

  function handleStart() {
    const pid = appState.activeProjectId;
    if (!pid || !selectedBackend()) {
      toast.warning("请先选择后端并打开项目");
      return;
    }
    // 检测未通过（且确实适用 token 检测）时，二次确认后再启动
    if (modelCheckState() === "error" && modelCheckResult()?.applicable) {
      confirm
        .show({
          title: "模型可用性未通过",
          message: `检测显示：${modelCheckResult()!.message}。仍要启动翻译任务吗？`,
          tone: "warning",
        })
        .then((r) => {
          if (r.confirmed) doSubmit();
        });
      return;
    }
    doSubmit();
  }

  function doSubmit() {
    const pid = appState.activeProjectId;
    if (!pid || !selectedBackend()) return;
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
      config_file_name: getActiveConfigFileName(),
      translator: selectedBackend(),
      // 统一使用程序全局「后端配置」中的令牌进行翻译（不再读项目自身 config.yaml 的 tokens）
      ...getSelectedBackendProfileJobPayload(realPath),
    })
      .then(() => toast.success("翻译任务已提交，正在启动…"))
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
          // 用户主动停止：清除任何待触发的自动重试，避免被误判为“模型检测失败”而重试
          clearTimeout(retryTimer);
          retryTimer = undefined;
          autoRetryCount = 0;
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

  // 检测状态文案
  const modelCheckText = () => {
    const s = modelCheckState();
    const r = modelCheckResult();
    if (s === "checking") return "检测中…";
    if (s === "na") return "本地 / 特殊端点，无需检测";
    if (s === "ok") return r ? `可用 ${r.available}/${r.total}` : "可用";
    if (s === "error") {
      const base = r ? r.message : "检测失败";
      if (autoRetryCount > 0 && autoRetryCount < MAX_AUTO_RETRIES) {
        return `${base} · ${10}s 后自动重试 (${autoRetryCount}/${MAX_AUTO_RETRIES})`;
      }
      return base;
    }
    return "";
  };

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
              <StatRow label="总行数" value={summary()?.total ?? "—"} />
              <StatRow
                label="已翻译"
                value={summary() ? `${summary()!.translated} / ${summary()!.total}` : "—"}
              />
              <StatRow label="进度" value={summary() ? `${summary()!.percent.toFixed(1)}%` : "—"} />
              <StatRow
                label="失败条目"
                value={summary()?.failed ?? "—"}
                tone={summary() && summary()!.failed > 0 ? "error" : "default"}
              />
              <StatRow
                label="问题条目"
                value={summary()?.problems ?? "—"}
                tone={summary() && summary()!.problems > 0 ? "error" : "default"}
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
                <span>速度: {summary()!.translation_speed_lpm.toFixed(1)} 条/秒</span>
                <Show when={summary()!.eta_seconds != null}>
                  <span>ETA: {formatETA(summary()!.eta_seconds!)}</span>
                </Show>
              </Show>
              <Show when={runtime()?.current_file}>
                <span class="current-file">当前文件: {runtime()!.current_file}</span>
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
                    <svg
                      width="12"
                      height="12"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      stroke-width="2"
                    >
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

            {/* 模型可用性检测 */}
            <div class="model-check">
              <button
                class="btn btn--ghost model-check__btn"
                onClick={() => {
                  autoRetryCount = 0;
                  clearTimeout(retryTimer);
                  runModelCheck();
                }}
                disabled={modelCheckState() === "checking" || !selectedBackend() || isRunning()}
                title="主动检测所选后端的模型/token 可用性"
              >
                {(() => {
                  const s = modelCheckState();
                  if (s === "checking") return "检测中…";
                  if (s === "error") {
                    const suffix =
                      autoRetryCount > 0
                        ? " (" + autoRetryCount + "/" + MAX_AUTO_RETRIES + ")"
                        : "";
                    return "重新检测" + suffix;
                  }
                  return "检测可用性";
                })()}
              </button>
              <Show when={modelCheckState() !== "idle"}>
                <span class={`model-check__status model-check__status--${modelCheckState()}`}>
                  <span class="model-check__dot" />
                  <span class="model-check__text">{modelCheckText()}</span>
                </span>
                {/* 示例 key 提示：提供快捷跳转到后端配置页 */}
                <Show when={
                  modelCheckState() === "error" &&
                  /example|示例/i.test(modelCheckResult()?.message ?? "")
                }>
                  <button
                    class="btn btn--sm btn--primary model-check__goto-config"
                    onClick={() => navigateTo("backend-profiles")}
                    title="前往后端配置页面设置真实的 API Key"
                  >
                    去配置令牌 →
                  </button>
                </Show>
              </Show>
            </div>
          </div>
        </div>

        {/* ── 下方两栏 ── */}
        <div class="translate-body">
          <div class="translate-panel">
            <div class="panel-header">当前提示词</div>
            <div class="panel-content">{runtime()?.latest_prompt_preview || "等待翻译开始…"}</div>
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
