import { createEffect, createSignal, For, on, onCleanup, Show } from "solid-js";
import { decodeProjectDir } from "../../lib/api/client";
import {
  fetchJob,
  fetchProjectLogs,
  stopProjectTranslation,
  submitJob,
} from "../../lib/api";
import {
  getActiveConfigFileName,
} from "../../stores/appStore";
import { loadRouteAgentPlan, saveRouteAgentPlan } from "../../lib/api/preferences";
import { getErrorMessage } from "../../lib/errors";
import { confirm } from "../../stores/confirmStore";
import { toast } from "../../stores/toastStore";

interface InstructionDef {
  id: string;
  label: string;
  description: string;
}

/** 工作台可执行的翻译流程后端（指令 -> 引擎 ID） */
const INSTRUCTIONS: InstructionDef[] = [
  { id: "ForGal-json-translate", label: "翻译", description: "主翻译后端（多轮/单轮对话）" },
  { id: "ForGal-full-pipeline", label: "完整流水线", description: "按需自动串联 0-7 各阶段" },
  { id: "ForFileMetaData", label: "文件元数据", description: "生成 pass1 文件级元数据" },
  { id: "ForBatchMetaData", label: "批次划分", description: "生成 pass2 批次级元数据" },
  { id: "ForGlobalPrompt", label: "全局分析", description: "生成全局游戏分析" },
  { id: "ForPlotRouteMap", label: "重生成路线图", description: "基于文件元数据重新生成剧情路线图（始终全项目）" },
  { id: "ForImproveTranslation", label: "译文改进", description: "为可改进句生成备选译文" },
  { id: "ForBRStation", label: "换行修复", description: "为换行异常句生成备选译文" },
  { id: "ForJPResidue", label: "残留修复", description: "为残留日文句生成备选译文" },
  { id: "ForBanWordFix", label: "用词修复", description: "为用词不当句生成备选译文" },
  { id: "ForSemCheck", label: "语义复核", description: "标记疑似语义错误" },
  { id: "ForSemCheckAgain", label: "二次复核", description: "复核疑似错误并剔除误报" },
  { id: "ForFixRound", label: "统一修复", description: "按 gpt.afterTranslation 的 fix 条目组合修复" },
];

/** 注入开关 -> 配置键映射（「跟随项目配置」时不写入 config_overrides） */
const INJECTION_TOGGLES: { key: string; label: string }[] = [
  { key: "internals.promptBlocks.globalPrompt", label: "全局分析+路线图剧情" },
  { key: "internals.promptBlocks.plotMetadata", label: "文件元数据" },
  { key: "internals.promptBlocks.batchMetadata", label: "批次元数据" },
  { key: "internals.promptBlocks.glossary", label: "GPT字典术语表" },
  { key: "internals.promptBlocks.translationGuideline", label: "翻译规范" },
  { key: "internals.forglobalprompt.inject_name_table", label: "人名表（全局分析）" },
];

type FilesMode = "selected" | "all" | "custom";
type ToggleValue = "default" | "on" | "off";

interface InstructionConfig {
  filesMode: FilesMode;
  customFilesText: string;
  injections: Record<string, ToggleValue>;
  advancedText: string;
}

function defaultConfig(): InstructionConfig {
  return {
    filesMode: "selected",
    customFilesText: "",
    injections: {},
    advancedText: "",
  };
}

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

  function buildFileFilter(): string[] | undefined {
    const mode = cfg().filesMode;
    if (mode === "all") return undefined;
    if (mode === "custom") {
      return cfg()
        .customFilesText.split(/[\n,，]/)
        .map((s) => s.trim())
        .filter(Boolean);
    }
    return props.selectedFiles.slice();
  }

  function buildOverrides(): Record<string, unknown> {
    const overrides: Record<string, unknown> = {};
    for (const { key } of INJECTION_TOGGLES) {
      const value = cfg().injections[key] ?? "default";
      if (value === "on") overrides[key] = true;
      else if (value === "off") overrides[key] = false;
    }
    const text = cfg().advancedText.trim();
    if (text) {
      const parsed = JSON.parse(text) as Record<string, unknown>;
      Object.assign(overrides, parsed);
    }
    return overrides;
  }

  async function handleRun() {
    const pid = props.projectId;
    if (!pid || submitting()) return;
    if (jobStatus() === "running" || jobStatus() === "pending") {
      toast.warning("已有任务在运行，请先等待完成或停止");
      return;
    }
    const inst = INSTRUCTIONS.find((i) => i.id === selectedId())!;
    let fileFilter: string[] | undefined;
    let overrides: Record<string, unknown>;
    try {
      fileFilter = buildFileFilter();
      overrides = buildOverrides();
    } catch (e) {
      toast.error(`高级覆盖 JSON 解析失败: ${getErrorMessage(e)}`);
      return;
    }
    if (fileFilter && fileFilter.length === 0) {
      toast.warning("文件范围为空：请先在路线图中右键选择文件，或把范围改为全部/自定义列表");
      return;
    }
    const scopeDesc = fileFilter ? `选定的 ${fileFilter.length} 个文件` : "全部文件";
    const overrideDesc = Object.keys(overrides).length > 0 ? `，覆盖 ${Object.keys(overrides).length} 项注入配置` : "";
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
      ...(fileFilter ? { file_filter: fileFilter } : {}),
      ...(Object.keys(overrides).length > 0 ? { config_overrides: overrides } : {}),
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

  // 任务状态 + 引擎日志轮询（翻译控制台同款 /runtime 之外，这里直接轮任务与日志做终端）
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
