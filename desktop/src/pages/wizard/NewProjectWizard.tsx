import { createSignal, createMemo, createEffect, Show, For } from "solid-js";
import { open } from "@tauri-apps/plugin-dialog";
import { invoke } from "@tauri-apps/api/core";
import { openProject } from "../../stores/appStore";
import { toast } from "../../stores/toastStore";
import { confirm } from "../../stores/confirmStore";
import { getErrorMessage } from "../../lib/errors";
import { Icon } from "../../components/icons/Icon";
import {
  fetchPlugins,
  fetchTranslationGuidelines,
  submitJob,
  fetchJob,
} from "../../lib/api/general";
import {
  fetchProjectConfig,
  updateProjectConfig,
  initProject,
  importProjectFiles,
  fetchWorkspaceRoot,
} from "../../lib/api/project";
import { ensureDesktopBackendReady, ApiError } from "../../lib/api/client";
import { setSelectedBackendProfile } from "../../lib/api/preferences";
import type { PluginInfo, Job } from "../../lib/api/types";
import { StepProjectInfo } from "./StepProjectInfo";
import { StepImportFiles } from "./StepImportFiles";
import { StepBackendSelect } from "./StepBackendSelect";
import { StepSettings } from "./StepSettings";
import { StepExtractNames } from "./StepExtractNames";
import { StepPipelineSettings } from "./StepPipelineSettings";

const STEPS = ["项目位置", "导入文件", "翻译后端", "常用设置", "流水线与全局设置", "提取人名"];

// 流水线阶段开关的默认值（全部开启）
const DEFAULT_STAGE_ENABLED: Record<string, boolean> = {
  enableValidate: true,
  enableCompress: true,
  enableGlobalPrompt: true,
  enableGenDic: true,
  enableFileMeta: true,
  enableBatchMeta: true,
  enableTranslate: true,
};

/* 等待任务结束（completed/failed/cancelled），带超时保护 */
const JOB_POLL_INTERVAL = 2000;
const JOB_TIMEOUT_MS = 10 * 60 * 1000;

function waitForJob(jobId: string): Promise<Job> {
  return new Promise((resolve, reject) => {
    const start = Date.now();
    const tick = async () => {
      try {
        const s = await fetchJob(jobId);
        if (s.status === "completed" || s.status === "failed" || s.status === "cancelled") {
          resolve(s);
          return;
        }
        if (Date.now() - start > JOB_TIMEOUT_MS) {
          reject(new Error("等待人名提取超时"));
          return;
        }
        setTimeout(tick, JOB_POLL_INTERVAL);
      } catch {
        if (Date.now() - start > JOB_TIMEOUT_MS) {
          reject(new Error("无法获取人名提取任务状态"));
          return;
        }
        setTimeout(tick, 3000);
      }
    };
    tick();
  });
}

export function NewProjectWizard() {
  const [currentStep, setCurrentStep] = createSignal(0);
  const [feedback, setFeedback] = createSignal<{
    type: "success" | "error" | "info";
    message: string;
  } | null>(null);

  // Step 1
  const [projectName, setProjectName] = createSignal("");
  const [projectCreated, setProjectCreated] = createSignal(false);
  const [projectDir, setProjectDir] = createSignal("");
  const [projectId, setProjectId] = createSignal("");
  // 后端 workspace 根目录（应用程序同目录下），用于实时预览项目完整路径
  const [workspaceRoot, setWorkspaceRoot] = createSignal("");

  // Step 2
  const [importedFiles, setImportedFiles] = createSignal<string[]>([]);

  // Step 3
  const [selectedBackend, setSelectedBackend] = createSignal("__default__");

  // Step 4
  const [filePlugins, setFilePlugins] = createSignal<PluginInfo[]>([]);
  const [selectedFilePlugin, setSelectedFilePlugin] = createSignal("file_galtransl_json");
  const [textPlugins, setTextPlugins] = createSignal<PluginInfo[]>([]);
  const [selectedTextPlugin, setSelectedTextPlugin] = createSignal("text_common_normalfix");
  const [workersPerProject, setWorkersPerProject] = createSignal(16);
  const [language, setLanguage] = createSignal("zh-cn");
  const [guidelines, setGuidelines] = createSignal<string[]>([]);
  const [translationGuideline, setTranslationGuideline] = createSignal("");

  // Step 5：外部信息、流水线阶段开关与「生成示例文件」勾选
  const [gameInfo, setGameInfo] = createSignal("");
  const [stageEnabled, setStageEnabled] = createSignal<Record<string, boolean>>({
    ...DEFAULT_STAGE_ENABLED,
  });
  const [sampleStages, setSampleStages] = createSignal<Set<string>>(new Set());

  // Step 6
  const [nameJobStatus, setNameJobStatus] = createSignal<
    "idle" | "running" | "completed" | "failed"
  >("idle");
  const [nameJobMessage, setNameJobMessage] = createSignal("");
  // 完成阶段是否正在等待后端/提取（用于禁用按钮、显示进度）
  const [finishing, setFinishing] = createSignal(false);

  const gtInputDir = createMemo(() => {
    const d = projectDir();
    if (!d) return "";
    const sep = d.includes("/") ? "/" : "\\";
    return `${d}${sep}gt_input`;
  });

  // 实时预览：输入项目名即拼接后端 workspace 根目录下的完整预期路径
  const previewDir = createMemo(() => {
    const root = workspaceRoot();
    const name = projectName().trim();
    if (!root || !name) return "";
    const sep = root.includes("/") ? "/" : "\\";
    return `${root}${sep}${name}`;
  });

  // 进入向导即拉取后端 workspace 根目录（位于应用程序同目录下）
  createEffect(() => {
    fetchWorkspaceRoot()
      .then((res) => setWorkspaceRoot(res.workspace_root))
      .catch(() => setWorkspaceRoot(""));
  });

  // 创建项目（全部走后端 /api/projects/init，含目录布局、config.yaml 与示例缓存）
  async function handleCreateProject() {
    const name = projectName();
    if (!name.trim()) {
      setFeedback({ type: "error", message: "请输入项目名称" });
      return;
    }
    // 后端守卫：激活失败明确提示，而非静默报错
    try {
      await ensureDesktopBackendReady({ timeoutMs: 25000 });
    } catch {
      toast.error("无法启动后端服务，请先手动运行 run_backend.py 后再试");
      return;
    }
    try {
      const res = await initProject(name, false);
      applyInitResult(res);
    } catch (err) {
      // 项目已存在：征询是否覆盖（覆盖仅重建布局与 config.yaml，保留译文/缓存）
      if (err instanceof ApiError && err.status === 409) {
        const result = await confirm.show({
          title: "目标项目已存在",
          message: `项目「${name}」已存在于后端工作区。\n\n点击「覆盖」将重新生成 config.yaml（已有的译文、缓存等文件会保留）；点击「取消」可返回修改项目名。`,
          confirmText: "覆盖",
          cancelText: "取消",
          tone: "warning",
        });
        if (!result.confirmed) return;
        try {
          const res = await initProject(name, true);
          applyInitResult(res);
        } catch (err2) {
          setFeedback({ type: "error", message: `覆盖失败: ${getErrorMessage(err2)}` });
        }
      } else {
        setFeedback({ type: "error", message: `创建失败: ${getErrorMessage(err)}` });
      }
    }
  }

  function applyInitResult(res: { project_id: string; project_dir: string }) {
    setProjectDir(res.project_dir);
    setProjectId(res.project_id);
    setProjectCreated(true);
    setFeedback({ type: "success", message: "项目创建成功！" });
  }

  // 导入文件（文件或文件夹均可；文件夹会被递归展开，交由后端读取写入 gt_input）
  async function importPathsToInput(paths: string[]) {
    const pid = projectId();
    if (!pid || paths.length === 0) return;
    try {
      const res = await importProjectFiles(pid, paths);
      const imported = res.imported || [];
      const skipped = res.skipped || [];
      if (imported.length === 0) {
        setFeedback({
          type: "info",
          message: skipped.length > 0 ? "所选文件已存在，跳过导入。" : "没有可导入的文件。",
        });
        return;
      }
      // 去重：过滤掉本次会话已导入的同名条目（保留首次出现）
      const existing = new Set(importedFiles().map((n) => n.toLowerCase()));
      const unique = imported.filter((n) => {
        const k = n.toLowerCase();
        if (existing.has(k)) return false;
        existing.add(k);
        return true;
      });
      setImportedFiles((prev) => [...prev, ...unique]);
      setFeedback({
        type: "success",
        message: `已导入 ${unique.length} 个文件`,
      });
    } catch (err) {
      setFeedback({
        type: "error",
        message: `导入失败: ${getErrorMessage(err)}`,
      });
    }
  }

  // 文件导入（实际路径来自原生拖拽或文件选择）
  async function handleImportPaths(paths: string[]) {
    await importPathsToInput(paths);
  }

  // 文件选择器
  async function handleFilePick() {
    if (!gtInputDir()) return;
    const s = await open({ multiple: true });
    if (!s) return;
    const paths = (Array.isArray(s) ? s : [s]) as string[];
    await importPathsToInput(paths);
  }

  async function handleOpenInputFolder() {
    const d = gtInputDir();
    if (!d) return;
    try {
      await invoke("open_folder", { path: d });
    } catch (err) {
      setFeedback({
        type: "error",
        message: `打开失败: ${getErrorMessage(err)}`,
      });
    }
  }

  // 保存设置
  async function handleSaveSettings() {
    const dir = projectDir();
    const pid = projectId();
    if (!dir || !pid) return;

    // 后端守卫：激活失败明确提示，而非静默报错
    try {
      await ensureDesktopBackendReady({ timeoutMs: 25000 });
    } catch {
      toast.error("无法启动后端服务，请先手动运行 run_backend.py 后再试");
      return;
    }

    try {
      const res = await fetchProjectConfig(pid, "config.yaml");
      const config = { ...res.config };
      const common: Record<string, unknown> = {
        ...((config.common as Record<string, unknown>) || {}),
        workersPerProject: workersPerProject(),
        language: language(),
        // 动态句数调整默认开启（已取代原"单次翻译句数 gpt.numPerRequestTranslate"，后者由后端默认值接管）
        "gpt.dynamicNumPerRequestTranslate": false,
        "gpt.contextNum": 8,
      };
      // 翻译规范文件：写入 common 段扁平键 gpt.translation_guideline（后端 CProjectConfig.getKey
      // 只读该位置，见 GalTransl/ConfigHelper.py；不要写顶层 gpt 段，否则选择会被后端忽略）。
      if (translationGuideline()) {
        common["gpt.translation_guideline"] = translationGuideline();
      } else {
        delete common["gpt.translation_guideline"];
      }
      // 清理旧版本误写进顶层 gpt 段的 translation_guideline 残留（段内无其它键时删除整段）
      if (config.gpt && typeof config.gpt === "object") {
        const gptSection = { ...(config.gpt as Record<string, unknown>) };
        delete gptSection.translation_guideline;
        if (Object.keys(gptSection).length === 0) delete config.gpt;
        else config.gpt = gptSection;
      }
      config.common = common;
      config.plugin = {
        ...((config.plugin as Record<string, unknown>) || {}),
        filePlugin: selectedFilePlugin(),
        textPlugins: [selectedTextPlugin()],
      };

      // AI 令牌不再写入项目 config.yaml：统一由程序全局「后端配置」管理，
      // 翻译时由后端在运行时应用选中的 profile（见 Service.run_job_async）。

      // 外部信息（游戏信息）写入 config 的 externals.gameInfo 段
      const gi = gameInfo();
      if (gi.trim()) {
        config.externals = {
          ...((config.externals as Record<string, unknown>) || {}),
          gameInfo: gi,
        };
      } else {
        const ext = (config.externals as Record<string, unknown>) || {};
        delete ext.gameInfo;
        if (Object.keys(ext).length === 0) delete config.externals;
        else config.externals = ext;
      }

      // 流水线阶段开关写入 config 的 internals.pipeline 段
      const stages = stageEnabled();
      const stagesDisabled = Object.entries(stages)
        .filter(([, enabled]) => !enabled)
        .map(([k]) => k);
      if (stagesDisabled.length > 0) {
        const internals = (config.internals as Record<string, unknown>) || {};
        const pipeline = (internals.pipeline as Record<string, unknown>) || {};
        for (const k of stagesDisabled) pipeline[k] = false;
        internals.pipeline = pipeline;
        config.internals = internals;
      }

      await updateProjectConfig(pid, {
        config,
        config_file_name: "config.yaml",
        // 后端据此生成用户勾选的示例 JSON 模板（独立于禁用阶段）
        pipeline: stages,
        sample_stages: [...sampleStages()],
      });
      setSelectedBackendProfile(dir, selectedBackend());
      setFeedback({ type: "success", message: "设置已保存" });
    } catch (err) {
      setFeedback({
        type: "error",
        message: `保存失败: ${getErrorMessage(err)}`,
      });
    }
  }

  // 完成：提取人名 + 打开项目
  async function handleFinish() {
    const dir = projectDir();
    const pid = projectId();
    if (!dir || !pid || finishing()) return;

    // 后端守卫：激活失败明确提示，而非静默报错
    try {
      await ensureDesktopBackendReady({ timeoutMs: 30000 });
    } catch {
      toast.error("无法启动后端服务，请先手动运行 run_backend.py 后再试");
      return;
    }

    // 若有文件，先提交 dump-name 任务并等待完成，再打开项目
    // （保证向导卸载前页面始终可见提取进度，避免无声后台运行）
    if (importedFiles().length > 0) {
      setFinishing(true);
      setNameJobStatus("running");
      setNameJobMessage("正在提取人名…");
      try {
        const job = await submitJob({
          project_dir: dir,
          config_file_name: "config.yaml",
          translator: "dump-name",
        });
        const result = await waitForJob(job.job_id);
        if (result.status === "failed" || result.status === "cancelled") {
          setNameJobStatus("failed");
          setNameJobMessage(result.error || "提取失败");
          toast.error(`人名提取失败: ${result.error || "未知错误"}`);
        } else {
          setNameJobStatus("completed");
          setNameJobMessage(
            result.success ? "人名提取完成！" : `提取完成但有警告: ${result.error || ""}`,
          );
        }
      } catch (err) {
        setNameJobStatus("failed");
        setNameJobMessage(getErrorMessage(err));
        toast.error(`人名提取失败: ${getErrorMessage(err)}`);
      } finally {
        setFinishing(false);
      }
    } else {
      setNameJobStatus("completed");
      setNameJobMessage("gt_input 中没有文件，已跳过人名提取。");
    }

    // 提取结束（或无需提取）后再打开项目，避免向导卸载后人名提取无反馈
    // 新建项目恒使用 config.yaml，显式传入跳过探测
    openProject(pid, { configFileName: "config.yaml" });
    toast.success("项目已创建并打开");
  }

  // 第3步：加载后端配置
  createEffect(() => {
    if (currentStep() === 3) {
      fetchPlugins()
        .then((p) => {
          setFilePlugins(p.filter((x: PluginInfo) => x.type === "file"));
          setTextPlugins(p.filter((x: PluginInfo) => x.type === "text"));
        })
        .catch(() => {});
      fetchTranslationGuidelines()
        .then((list: string[]) => {
          setGuidelines(list);
          if (!translationGuideline() && list.length > 0) {
            setTranslationGuideline(list.includes("日译中_增强") ? "日译中_增强" : list[0]);
          }
        })
        .catch(() => {});
    }
  });

  const canNext = () => {
    if (currentStep() === 0) return projectCreated();
    return true;
  };

  const stepProgress = () => Math.round(((currentStep() + 1) / STEPS.length) * 100);

  function handleBack() {
    setCurrentStep((s) => Math.max(0, s - 1));
  }

  async function handleNext() {
    // 从第4步（常用设置）离开时自动保存
    if (currentStep() === 3) {
      await handleSaveSettings();
    }
    // 从第5步（流水线与全局设置）离开时保存外部信息与阶段开关
    if (currentStep() === 4) {
      await handleSaveSettings();
    }
    setCurrentStep((s) => Math.min(STEPS.length - 1, s + 1));
  }

  return (
    <div class="page wizard-page">
      <h2 class="page-title">新建项目</h2>
      <p class="page-description">按照向导创建一个新的翻译项目。</p>

      {/* 步骤指示器 */}
      <ul class="wizard-steps">
        <For each={STEPS}>
          {(label, i) => (
            <li
              class={`wizard-step ${i() === currentStep() ? "wizard-step--active" : ""} ${i() < currentStep() ? "wizard-step--completed" : ""}`}
            >
              <span class="wizard-step__number">
                {i() < currentStep() ? <Icon name="check" size={12} /> : i() + 1}
              </span>
              <span class="wizard-step__label">{label}</span>
            </li>
          )}
        </For>
      </ul>

      {/* 内容 */}
      <div class="wizard-content">
        <div class="wizard-step-summary">
          <div class="wizard-step-summary__top">
            <span>
              第 {currentStep() + 1} / {STEPS.length} 步
            </span>
            <strong>{STEPS[currentStep()]}</strong>
          </div>
          <div class="wizard-step-summary__bar">
            <span style={{ width: `${stepProgress()}%` }} />
          </div>
        </div>

        <div class="wizard-step-stage">
          {currentStep() === 0 && (
            <StepProjectInfo
              projectName={projectName()}
              projectDir={projectDir()}
              previewDir={previewDir()}
              projectCreated={projectCreated()}
              onProjectNameChange={setProjectName}
              onProjectCreatedChange={setProjectCreated}
              onCreateProject={handleCreateProject}
            />
          )}
          {currentStep() === 1 && (
            <StepImportFiles
              gtInputDir={gtInputDir()}
              importedFiles={importedFiles()}
              onImportPaths={handleImportPaths}
              onFilePick={handleFilePick}
              onOpenInputFolder={handleOpenInputFolder}
            />
          )}
          {currentStep() === 2 && (
            <StepBackendSelect
              selectedBackend={selectedBackend()}
              onBackendChange={setSelectedBackend}
            />
          )}
          {currentStep() === 3 && (
            <StepSettings
              selectedFilePlugin={selectedFilePlugin()}
              selectedTextPlugin={selectedTextPlugin()}
              workersPerProject={workersPerProject()}
              language={language()}
              translationGuideline={translationGuideline()}
              guidelines={guidelines()}
              filePlugins={filePlugins()}
              textPlugins={textPlugins()}
              onFilePluginChange={setSelectedFilePlugin}
              onTextPluginChange={setSelectedTextPlugin}
              onWorkersChange={setWorkersPerProject}
              onLanguageChange={setLanguage}
              onGuidelineChange={setTranslationGuideline}
              onSaveSettings={handleSaveSettings}
            />
          )}
          {currentStep() === 4 && (
            <StepPipelineSettings
              gameInfo={gameInfo()}
              stageEnabled={stageEnabled()}
              sampleStages={sampleStages()}
              onGameInfoChange={setGameInfo}
              onStageToggle={(key, enabled) =>
                setStageEnabled((prev) => ({ ...prev, [key]: enabled }))
              }
              onSampleToggle={(key, checked) => {
                // 勾选「生成示例文件」时联动禁用该阶段：空模板需跳过生成，
                // 待用户填好内容后在设置页重新开启该阶段
                if (checked) {
                  setStageEnabled((prev) => ({ ...prev, [key]: false }));
                }
                setSampleStages((prev) => {
                  const next = new Set(prev);
                  if (checked) next.add(key);
                  else next.delete(key);
                  return next;
                });
              }}
            />
          )}
          {currentStep() === 5 && (
            <StepExtractNames nameJobStatus={nameJobStatus()} nameJobMessage={nameJobMessage()} />
          )}
        </div>

        {/* 反馈消息 */}
        <Show when={feedback()}>
          <div class={`wizard-feedback wizard-feedback--${feedback()!.type}`}>
            {feedback()!.message}
          </div>
        </Show>
      </div>

      {/* 导航按钮 */}
      <div class="wizard-nav">
        <button class="btn" onClick={handleBack} disabled={currentStep() === 0 || finishing()}>
          上一步
        </button>
        {currentStep() < 5 ? (
          <button
            class="btn btn--primary"
            onClick={handleNext}
            disabled={!canNext() || finishing()}
          >
            下一步
          </button>
        ) : (
          <button class="btn btn--primary" onClick={handleFinish} disabled={finishing()}>
            {finishing() ? "正在提取人名…" : "完成并打开项目"}
          </button>
        )}
      </div>
    </div>
  );
}
