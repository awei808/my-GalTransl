import { createSignal, createEffect, createMemo, onMount, onCleanup, Show, For, Switch, Match } from "solid-js";
import { appState, setAppState, getActiveConfigFileName, navigateTo, type ModelCheckState } from "../../stores/appStore";
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

/* ── 任务类型 → 中文标签（用于 toast 命名，必须包含任务类型）── */
function taskTypeLabel(translator: string): string {
  switch (translator) {
    case "ForGal-json-multi-chat":
    case "ForGal-json":
      return "文件翻译";
    case "ForFileMetaData":
      return "文件级元数据";
    case "ForBatchMetaData":
      return "批次级元数据";
    case "ForGlobalPrompt":
      return "全局分析";
    case "ForGal-full-pipeline":
      return "完整流水线";
    case "GenDic":
      return "术语表生成";
    default:
      return translator || "翻译任务";
  }
}

/* ── 文件描述：单文件显示文件名；批次模式显示“当前文件 X 第 N/M 批次” ── */
function fileDesc(
  rt: ProjectRuntimeResponse | null,
  filename: string,
): string {
  const batch = rt?.current_batch ?? 0;
  const total = rt?.batch_total ?? 0;
  if (batch > 0 && total > 0) {
    return `当前文件 ${filename} 第 ${batch}/${total} 批次`;
  }
  return `文件 ${filename}`;
}

/* ── 后端下拉项的简洁中文说明（面向零基础用户，直接跟在后端名字后）── */
const BACKEND_HINTS: Record<string, string> = {
  "ForGal-json-multi-chat": "按批次翻译原文，为取得最好效果，建议在最后执行本后端",
  "ForGal-json": "逐句翻译原文（旧格式兼容）",
  "ForFileMetaData": "AI 分析每个文件的剧情与角色",
  "ForBatchMetaData": "AI 分析文件内容，将文本按剧情划分批次，并生成每个批次的指导语言风格",
  "ForGlobalPrompt": "AI通读全文，生成世界观与角色档案作为全局提示词",
  "ForGal-full-pipeline": "一键跑完字典+全局、文件、批次分析+翻译全流程（不建议启用）",
  "GenDic": "AI 自动生成gpt字典",
};
function backendHint(name: string): string {
  return BACKEND_HINTS[name] || "";
}

/* ── 翻译控制台 ── */
export function TranslateConsole() {
  const [runtime, setRuntime] = createSignal<ProjectRuntimeResponse | null>(null);
  const [translators, setTranslators] = createSignal<TranslatorOption[]>([]);
  const [running, setRunning] = createSignal(false);
  const [dropdownOpen, setDropdownOpen] = createSignal(false);
  const [submitting, setSubmitting] = createSignal(false);

  // 模型可用性检测状态（全局持久，见 appStore.modelCheck，避免组件重挂后重复检测/丢失结果）
  const modelCheckState = () => appState.modelCheck.state;
  const modelCheckResult = () => appState.modelCheck.result;
  const setModelCheckState = (s: ModelCheckState) =>
    setAppState("modelCheck", "state", s);
  const setModelCheckResult = (r: ModelCheckResult | null) =>
    setAppState("modelCheck", "result", r);
  let checkingToken = 0; // 防止并发/过期响应覆盖最新结果
  let retryTimer: ReturnType<typeof setTimeout> | undefined; // 自动重试定时器
  const MAX_AUTO_RETRIES = 3; // 错误状态下最多自动重试 3 次

  let pollTimer: ReturnType<typeof setInterval> | undefined;
  let dropdownRef: HTMLDivElement | undefined;
  let pollErrorCount = 0;
  // prevJobStatus 已提升为全局 appState.prevJobStatus（见 appStore），避免切回页面时误弹“已开始”

  // 右侧面板标签切换
  type PanelTab = "assembled" | "errors" | "files" | "flow";
  const [panelTab, setPanelTab] = createSignal<PanelTab>("assembled");

  // 文件级 toast 追踪（开始 / 出错 / 完成），避免重复弹窗
  const prevFilesCompleted = new Set<string>();
  const prevFilesStarted = new Set<string>();
  const prevFilesFailed = new Set<string>();
  const prevFileSnapshot = new Map<string, { translated: number; failed: number }>();

  // 仅在项目打开时轮询
  createEffect(() => {
    const pid = appState.activeProjectId;
    if (!pid) {
      setRuntime(null);
      setRunning(false);
      setAppState("prevJobStatus", "");
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

        const taskType = taskTypeLabel(rt.job?.translator ?? "");

        // ── 文件级 toast：开始 / 出错 / 完成（仅运行中检测，避免重复）──
        if (status === "running" && rt.files) {
          for (const f of rt.files) {
            const prev = prevFileSnapshot.get(f.filename);
            const prevTranslated = prev ? prev.translated : 0;
            const prevFailed = prev ? prev.failed : 0;
            const translatedNow = f.translated;
            const failedNow = f.failed;
            const isComplete = f.total > 0 && translatedNow >= f.total;
            // 开始：已翻译从 0 变为 >0（且尚未完成，避免缓存命中的文件误报“开始”）
            if (!isComplete && translatedNow > 0 && prevTranslated === 0 && !prevFilesStarted.has(f.filename)) {
              prevFilesStarted.add(f.filename);
              toast.info(`【${taskType}】${fileDesc(rt, f.filename)} 开始翻译`);
            }
            // 出错：失败条数从 0 变为 >0
            if (failedNow > 0 && prevFailed === 0 && !prevFilesFailed.has(f.filename)) {
              prevFilesFailed.add(f.filename);
              toast.error(`【${taskType}】${fileDesc(rt, f.filename)} 翻译出错（${failedNow} 条失败）`);
            }
            // 完成：全部条目翻译完
            if (isComplete && !prevFilesCompleted.has(f.filename)) {
              prevFilesCompleted.add(f.filename);
              toast.success(`【${taskType}】${fileDesc(rt, f.filename)} 翻译完成`);
            }
            prevFileSnapshot.set(f.filename, { translated: translatedNow, failed: failedNow });
          }
        }
        // 非运行中时重置追踪
        if (!status || status !== "running") {
          prevFilesCompleted.clear();
          prevFilesStarted.clear();
          prevFilesFailed.clear();
          prevFileSnapshot.clear();
        }

        // ── 任务级 toast：状态变更通知；失败时主动停止任务 ──
        if (status && appState.prevJobStatus !== status) {
          if (status === "running") {
            toast.info(`【${taskType}】翻译任务已开始`);
          } else if (status === "completed") {
            toast.success(`【${taskType}】翻译任务已完成`);
          } else if (status === "failed") {
            toast.error(`【${taskType}】翻译失败: ${rt.job?.error || "未知错误"}`);
            // 主动停止任务，清理后端残留进程
            stopProjectTranslation(projectId)
              .then(() => {})
              .catch(() => {});
          } else if (status === "cancelled") {
            toast.info(`【${taskType}】翻译任务已取消`);
          }
        }
        if (status) setAppState("prevJobStatus", status);
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
          ensureBackendSelection(list);
        })
        .catch(() => {});
    } else {
      ensureBackendSelection(translators());
    }
  });

  // 校验当前选中的后端是否仍有效：无效（空或不在列表）则回退到第一个。
  // 用全局 store 保存选中，避免组件重挂丢失、切项目后失效。
  function ensureBackendSelection(list: TranslatorOption[]) {
    if (list.length === 0) return;
    const cur = appState.selectedBackend;
    if (!cur || !list.some((t) => t.name === cur)) {
      setAppState("selectedBackend", list[0].name);
    }
  }

  onCleanup(() => {
    clearInterval(pollTimer);
    clearTimeout(retryTimer);
  });

  // 主动检测所选后端的模型可用性
  let autoRetryCount = 0;
  async function runModelCheck(isAutoRetry = false) {
    const pid = appState.activeProjectId;
    const backend = appState.selectedBackend;
    if (!pid || !backend) return;
    // 记录正在检测的后端+项目，供“切回页面不重复检测”判定
    setAppState("modelCheck", { backend, projectId: pid });
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
    const backend = appState.selectedBackend;
    void appState.activeConfigFileName; // 依赖：真实配置名解析完成后重跑检测
    if (!pid || !backend) return;
    // 同一 backend+project 已有检测结果（非 idle）时跳过——
    // 避免切回翻译控制台组件重挂后重复触发检测。
    const mc = appState.modelCheck;
    if (mc.projectId === pid && mc.backend === backend && mc.state !== "idle") {
      return;
    }
    runModelCheck();
  });

  function handleStart() {
    const pid = appState.activeProjectId;
    if (!pid || !appState.selectedBackend) {
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
    if (!pid || !appState.selectedBackend) return;
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
      translator: appState.selectedBackend,
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

  const summary = createMemo(() => runtime()?.summary);
  const hasProject = () => !!appState.activeProjectId;
  const isRunning = () => running();

  // 标签数据
  const errors = () => runtime()?.recent_errors ?? [];
  const fileProgress = () => runtime()?.files ?? [];
  const errorCount = () => errors().length;
  const fileCount = () => fileProgress().length;

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
                {/* 多 worker 并发指示（活跃 / 已配置） */}
                <span class="worker-info" title="并发 worker：活跃 / 已配置">
                  并发
                  <span class="worker-bar">
                    <span
                      class="worker-bar-fill"
                      style={{
                        width: `${
                          summary()!.workers_configured > 0
                            ? (summary()!.workers_active / summary()!.workers_configured) * 100
                            : 0
                        }%`,
                      }}
                    />
                  </span>
                  <b>{summary()!.workers_active}</b> / {summary()!.workers_configured}
                </span>
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
                    <span>{appState.selectedBackend || "选择后端"}</span>
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
                          class={`backend-option ${appState.selectedBackend === t.name ? "active" : ""}`}
                          onClick={() => {
                            setAppState("selectedBackend", t.name);
                            setDropdownOpen(false);
                          }}
                        >
                          <div class="backend-option__name">{t.name}</div>
                          <div class="backend-option__hint">{backendHint(t.name) || t.description}</div>
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
                disabled={modelCheckState() === "checking" || !appState.selectedBackend || isRunning()}
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
            {/* ── 标签切换栏（左上角按钮组）── */}
            <div class="panel-tabs">
              <button
                class={`panel-tab ${panelTab() === "assembled" ? "panel-tab--active" : ""}`}
                onClick={() => setPanelTab("assembled")}
              >译文拼接</button>
              <button
                class={`panel-tab ${panelTab() === "errors" ? "panel-tab--active" : ""}`}
                onClick={() => setPanelTab("errors")}
              >
                最近错误{errorCount() > 0 && <span class="panel-tab-badge">{errorCount()}</span>}
              </button>
              <button
                class={`panel-tab ${panelTab() === "files" ? "panel-tab--active" : ""}`}
                onClick={() => setPanelTab("files")}
              >
                文件进度{fileCount() > 0 && <span class="panel-tab-badge">{fileCount()}</span>}
              </button>
              <button
                class={`panel-tab ${panelTab() === "flow" ? "panel-tab--active" : ""}`}
                onClick={() => setPanelTab("flow")}
              >
                流程说明
              </button>
            </div>

            {/* ── 标签内容 ── */}
            <Switch fallback={<div class="panel-content">未知面板</div>}>
              {/* 译文拼接（原内容） */}
              <Match when={panelTab() === "assembled"}>
                <div class="panel-content panel-content--pre">
                  {runtime()?.latest_assembled_preview || "等待翻译开始…"}
                </div>
              </Match>

              {/* 最近错误 */}
              <Match when={panelTab() === "errors"}>
                <div class="panel-content panel-content--scroll">
                  <Show
                    when={errors().length > 0}
                    fallback={
                      <div class="empty-state">
                        <p class="empty-state__title">最近没有错误</p>
                        <p class="empty-state__desc">接口错误、解析错误会显示在这里。</p>
                      </div>
                    }
                  >
                    <For each={errors()}>
                      {(err) => (
                        <div class="error-entry">
                          <div class="error-entry__head">
                            <span class="error-entry__kind">{err.kind}</span>
                            <span class="error-entry__file">{err.filename}</span>
                          </div>
                          <p class="error-entry__msg">{err.message}</p>
                        </div>
                      )}
                    </For>
                  </Show>
                </div>
              </Match>

              {/* 文件进度 */}
              <Match when={panelTab() === "files"}>
                <div class="panel-content panel-content--scroll">
                  <Show
                    when={fileProgress().length > 0}
                    fallback={
                      <div class="empty-state">
                        <p class="empty-state__title">暂无文件进度</p>
                        <p class="empty-state__desc">启动翻译后显示各文件翻译进度。</p>
                      </div>
                    }
                  >
                    <For each={fileProgress()}>
                      {(fp) => {
                        const pct = fp.total > 0 ? Math.round((fp.translated / fp.total) * 100) : 0;
                        const done = fp.translated >= fp.total && fp.total > 0;
                        const statusText = () =>
                          done ? "已完成" : (isRunning() ? "处理中" : "");
                        return (
                          <div class="fp-row">
                            <div class="fp-info">
                              <span class="fp-name" title={fp.filename}>{fp.filename}</span>
                              <span class={`fp-status ${done ? "fp-status--done" : "fp-status--running"}`}>
                                {statusText()}
                              </span>
                            </div>
                            <div class="fp-meta">
                              <span class="fp-count">{fp.translated}/{fp.total}</span>
                              <Show when={(fp.failed ?? 0) > 0}>
                                <span class="fp-fail">· {(fp.failed ?? 0)}失败</span>
                              </Show>
                            </div>
                            <div class="fp-bar-track">
                              <div
                                class={`fp-bar-fill ${done ? "fp-bar-fill--done" : "fp-bar-fill--running"}`}
                                style={{ width: `${pct}%` }}
                              />
                            </div>
                          </div>
                        );
                      }}
                    </For>
                  </Show>
                </div>
              </Match>

              {/* 流程说明 */}
              <Match when={panelTab() === "flow"}>
                <div class="panel-content panel-content--scroll flow-view">
                  <p class="flow-intro">翻译项目全流程</p>
                  <ol class="flow-list">
                    <li><b>准备数据</b>：导入文件，本步在创建项目时已完成</li>
                    <li><b>压缩文本</b>：把 JSON 压缩成对话格式。本步在创建项目时已完成</li>
                    <li><b>全局分析</b>：AI 通读全文，理解世界观与角色关系，生成全局提示词。可使用后端ForGlobalPrompt完成或人工创建</li>
                    <li><b>生成术语表</b>：生成gpt字典。可使用后端gendic完成或人工创建</li>
                    <li><b>文件级元数据</b>：AI 分析每个文件的剧情和角色身份，生成文件级提示词。可使用后端ForFileMetaData完成或人工创建</li>
                    <li><b>划分区间</b>：把文件按剧情拆成几个批次并生成批次级提示词。跳过本步将按照项目设置的每次请求句数来进行下步。可使用后端ForBatchMetaData完成或人工创建</li>
                    <li><b>翻译执行</b>：逐文件、逐批次交给 AI 翻译，注入全局、文件级、批次级提示词提高翻译效果（若有）。使用后端ForGalJsonMulitChat完成</li>
                    <li><b>校对审核</b>：你在界面里逐条检查、修改译文。</li>
                    <li><b>构建输出</b>：把校对后的译文合成最终文件，导出到 output 目录。</li>
                  </ol>
                  <p class="flow-note">翻译完成后，到“校对审核”页面检查译文，改好后点“构建输出”即可。</p>
                </div>
              </Match>
            </Switch>
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
