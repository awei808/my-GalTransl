import { createEffect, createSignal, For, on, onCleanup, Show } from "solid-js";
import { decodeProjectDir } from "../../lib/api/client";
import { fetchJob, fetchProjectLogs, stopProjectTranslation, submitJob } from "../../lib/api";
import { getActiveConfigFileName } from "../../stores/appStore";
import { loadRouteAgentPlan, saveRouteAgentPlan } from "../../lib/api/preferences";
import { getErrorMessage } from "../../lib/errors";
import { confirm } from "../../stores/confirmStore";
import { toast } from "../../stores/toastStore";
import {
  buildJobExtras,
  defaultConfig,
  INJECTION_TOGGLES,
  INSTRUCTIONS,
  type InstructionConfig,
  type ToggleValue,
} from "./plan";

/**
 * 执行终端：按指令（后端）配置文件范围与注入内容，直接提交 /api/jobs 绕过 AI 执行，
 * 并轮询任务状态与引擎日志做终端式展示。
 */
export function RunPanel(props: { projectId: string; selectedFiles: string[] }) {
  const [selectedId, setSelectedId] = createSignal(INSTRUCTIONS[0].id);
  const [configs, setConfigs] = createSignal<Record<string, InstructionConfig>>({});
  const [jobId, setJobId] = createSignal("");
  const [jobStatus, setJobStatus] = createSignal("");
  const [lines, setLines] = createSignal<string[]>([]);
  const [submitting, setSubmitting] = createSignal(false);
  let terminalRef: HTMLDivElement | undefined;
  let pollTimer: ReturnType<typeof setInterval> | undefined;
  let disposed = false;

  const cfg = (): InstructionConfig => configs()[selectedId()] ?? defaultConfig();

  // 按项目持久化执行配置（便利性缓存）
  createEffect(
    on(
      () => props.projectId,
      (pid) => {
        setJobId("");
        setJobStatus("");
        setLines([]);
        setSelectedId(INSTRUCTIONS[0].id);
        setConfigs((pid && (loadRouteAgentPlan(pid) as Record<string, InstructionConfig> | null)) || {});
      },
    ),
  );
  createEffect(() => {
    const pid = props.projectId;
    const snapshot = configs();
    if (pid && Object.keys(snapshot).length > 0) saveRouteAgentPlan(pid, snapshot);
  });

  function updateConfig(patch: Partial<InstructionConfig>) {
    setConfigs((m) => ({ ...m, [selectedId()]: { ...cfg(), ...patch } }));
  }

  async function handleRun() {
    const pid = props.projectId;
    if (!pid || submitting()) return;
    if (jobStatus() === "running" || jobStatus() === "pending") {
      toast.warning("已有任务在运行，请先等待完成或停止");
      return;
    }
    const inst = INSTRUCTIONS.find((i) => i.id === selectedId())!;
    let extras: { file_filter?: string[]; config_overrides?: Record<string, unknown> };
    try {
      const built = buildJobExtras(selectedId(), cfg(), props.selectedFiles);
      if (built === null) {
        toast.warning("文件范围为空：请先在路线图中右键选择文件，或把范围改为全部/自定义列表");
        return;
      }
      extras = built;
    } catch (e) {
      toast.error(`高级覆盖 JSON 解析失败: ${getErrorMessage(e)}`);
      return;
    }
    const fileFilter = extras.file_filter;
    const scopeDesc = fileFilter ? `选定的 ${fileFilter.length} 个文件` : "全部文件";
    const overrideDesc =
      extras.config_overrides !== undefined
        ? `，覆盖 ${Object.keys(extras.config_overrides).length} 项注入配置`
        : "";
    const result = await confirm.show({
      title: "执行确认",
      message: `将在${scopeDesc}上执行「${inst.label}」${overrideDesc}。`,
      tone: "info",
      confirmText: "执行",
    });
    if (!result.confirmed) return;
    const realPath = decodeProjectDir(pid);
    if (!realPath) {
      toast.error("项目路径解析失败");
      return;
    }
    setSubmitting(true);
    submitJob({
      project_dir: realPath,
      config_file_name: getActiveConfigFileName(),
      translator: inst.id,
      ...extras,
    })
      .then((job) => {
        toast.success(`「${inst.label}」任务已提交`);
        setLines([`── ${new Date().toLocaleTimeString()} 提交「${inst.label}」（${scopeDesc}）──`]);
        setJobId(job.job_id);
        setJobStatus("pending");
      })
      .catch((e) => toast.error(`提交失败: ${getErrorMessage(e)}`))
      .finally(() => setSubmitting(false));
  }

  // 任务状态 + 引擎日志轮询（单一定时器：先查状态，再拉日志尾部）
  createEffect(
    on(
      () => [props.projectId, jobId()] as const,
      ([pid, jid]) => {
        clearInterval(pollTimer);
        pollTimer = undefined;
        if (!pid || !jid) return;
        const poll = async () => {
          try {
            const job = await fetchJob(jid);
            if (disposed || props.projectId !== pid) return;
            setJobStatus(job.status);
            if (job.status === "completed") {
              clearInterval(pollTimer);
              pollTimer = undefined;
              toast.success(`任务 ${jid} 已完成`);
            } else if (job.status === "failed") {
              clearInterval(pollTimer);
              pollTimer = undefined;
              toast.error(`任务 ${jid} 失败: ${job.error || "未知错误"}`);
            }
            const logs = await fetchProjectLogs(pid, 400, "engine");
            if (disposed || props.projectId !== pid) return;
            if (logs.exists) {
              setLines(logs.lines.slice());
              if (terminalRef) terminalRef.scrollTop = terminalRef.scrollHeight;
            }
          } catch {
            // 轮询失败静默，下一轮重试
          }
        };
        void poll();
        pollTimer = setInterval(() => void poll(), 2000);
      },
    ),
  );
  onCleanup(() => {
    disposed = true;
    clearInterval(pollTimer);
  });

  async function handleStop() {
    const pid = props.projectId;
    if (!pid) return;
    try {
      await stopProjectTranslation(pid);
      toast.info("已请求停止任务");
    } catch (e) {
      toast.error(`停止失败: ${getErrorMessage(e)}`);
    }
  }

  const isRunning = () => jobStatus() === "running" || jobStatus() === "pending";

  return (
    <div class="run-panel">
      <div class="run-config">
        <div class="run-row">
          <label class="run-label">指令</label>
          <select
            class="run-select"
            value={selectedId()}
            onChange={(e) => setSelectedId(e.currentTarget.value)}
          >
            <For each={INSTRUCTIONS}>{(inst) => <option value={inst.id}>{inst.label}</option>}</For>
          </select>
          <span class="run-desc">{INSTRUCTIONS.find((i) => i.id === selectedId())?.description}</span>
        </div>
        <div class="run-row">
          <label class="run-label">文件范围</label>
          <div class="run-files">
            <label>
              <input
                type="radio"
                name="run-files-mode"
                checked={cfg().filesMode === "selected"}
                onChange={() => updateConfig({ filesMode: "selected" })}
              />
              Agent 所选（{props.selectedFiles.length}）
            </label>
            <label>
              <input
                type="radio"
                name="run-files-mode"
                checked={cfg().filesMode === "all"}
                onChange={() => updateConfig({ filesMode: "all" })}
              />
              全部文件
            </label>
            <label>
              <input
                type="radio"
                name="run-files-mode"
                checked={cfg().filesMode === "custom"}
                onChange={() => updateConfig({ filesMode: "custom" })}
              />
              自定义
            </label>
          </div>
        </div>
        <Show when={cfg().filesMode === "custom"}>
          <div class="run-row">
            <label class="run-label">文件列表</label>
            <textarea
              class="run-custom-files"
              placeholder="每行一个文件名（支持完整路径/文件名/去扩展名）"
              value={cfg().customFilesText}
              onInput={(e) => updateConfig({ customFilesText: e.currentTarget.value })}
            />
          </div>
        </Show>
        <div class="run-row">
          <label class="run-label">注入覆盖</label>
          <div class="run-injections">
            <For each={INJECTION_TOGGLES}>
              {({ key, label }) => (
                <label class="run-injection">
                  <span>{label}</span>
                  <select
                    value={cfg().injections[key] ?? "default"}
                    onChange={(e) =>
                      updateConfig({ injections: { ...cfg().injections, [key]: e.currentTarget.value as ToggleValue } })
                    }
                  >
                    <option value="default">跟随项目配置</option>
                    <option value="on">强制开启</option>
                    <option value="off">强制关闭</option>
                  </select>
                </label>
              )}
            </For>
          </div>
        </div>
        <div class="run-row">
          <label class="run-label">高级覆盖</label>
          <textarea
            class="run-advanced"
            placeholder='额外 config_overrides（JSON 对象），如 {"gpt.afterTranslation": ["brfix"]}'
            value={cfg().advancedText}
            onInput={(e) => updateConfig({ advancedText: e.currentTarget.value })}
          />
        </div>
        <div class="run-actions">
          <button type="button" class="run-submit" disabled={submitting() || !props.projectId} onClick={() => void handleRun()}>
            执行
          </button>
          <Show when={isRunning()}>
            <button type="button" class="run-stop" onClick={() => void handleStop()}>
              停止
            </button>
          </Show>
          <Show when={jobId()}>
            <span class="run-job-status">任务 {jobId()} · {jobStatus() || "—"}</span>
          </Show>
        </div>
      </div>
      <div class="run-terminal" ref={terminalRef}>
        <Show when={lines().length > 0} fallback={<div class="run-terminal-empty">提交任务后此处显示引擎日志。</div>}>
          <For each={lines()}>{(line) => <div class="run-terminal-line">{line}</div>}</For>
        </Show>
      </div>
    </div>
  );
}
