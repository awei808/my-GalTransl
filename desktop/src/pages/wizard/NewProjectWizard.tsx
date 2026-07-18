import {
  createSignal,
  createEffect,
  createMemo,
  onMount,
  onCleanup,
  Show,
  For,
} from "solid-js";
import { open } from "@tauri-apps/plugin-dialog";
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWebviewWindow } from "@tauri-apps/api/webviewWindow";
import { navigateTo, openProject } from "../../stores/appStore";
import { toast } from "../../stores/toastStore";
import { Icon } from "../../components/icons/Icon";
import {
  fetchDefaultProjectConfigTemplate,
  fetchPlugins,
  fetchTranslationGuidelines,
  submitJob,
  fetchJob,
} from "../../lib/api/general";
import {
  fetchProjectConfig,
  updateProjectConfig,
} from "../../lib/api/project";
import { encodeProjectDir } from "../../lib/api/client";
import { setSelectedBackendProfile } from "../../lib/api/preferences";
import type { PluginInfo } from "../../lib/api/types";
import { StepProjectInfo } from "./StepProjectInfo";
import { StepImportFiles } from "./StepImportFiles";
import { StepBackendSelect } from "./StepBackendSelect";
import { StepSettings } from "./StepSettings";
import { StepExtractNames } from "./StepExtractNames";

const STEPS = ["项目位置", "导入文件", "翻译后端", "常用设置", "提取人名"];
const LAST_PARENT_DIR_KEY = "galtransl-new-project-last-parent-dir";

export function NewProjectWizard() {
  const [currentStep, setCurrentStep] = createSignal(0);
  const [feedback, setFeedback] = createSignal<{
    type: "success" | "error" | "info";
    message: string;
  } | null>(null);

  // Step 1
  const [parentDir, setParentDir] = createSignal(
    localStorage.getItem(LAST_PARENT_DIR_KEY) || ""
  );
  const [projectName, setProjectName] = createSignal("");
  const [projectCreated, setProjectCreated] = createSignal(false);

  // Step 2
  const [importedFiles, setImportedFiles] = createSignal<string[]>([]);

  // Step 3
  const [selectedBackend, setSelectedBackend] = createSignal("__default__");

  // Step 4
  const [filePlugins, setFilePlugins] = createSignal<PluginInfo[]>([]);
  const [selectedFilePlugin, setSelectedFilePlugin] =
    createSignal("file_galtransl_json");
  const [workersPerProject, setWorkersPerProject] = createSignal(16);
  const [numPerRequest, setNumPerRequest] = createSignal(16);
  const [language, setLanguage] = createSignal("zh-cn");
  const [guidelines, setGuidelines] = createSignal<string[]>([]);
  const [translationGuideline, setTranslationGuideline] = createSignal("");
  const [settingsSaved, setSettingsSaved] = createSignal(false);

  // Step 5
  const [nameJobStatus, setNameJobStatus] = createSignal<
    "idle" | "running" | "completed" | "failed"
  >("idle");
  const [nameJobMessage, setNameJobMessage] = createSignal("");

  const projectDir = createMemo(() => {
    const p = parentDir();
    const n = projectName();
    if (!p || !n) return "";
    const sep = p.includes("/") ? "/" : "\\";
    return `${p}${sep}${n}`;
  });
  const gtInputDir = createMemo(() => {
    const d = projectDir();
    if (!d) return "";
    const sep = d.includes("/") ? "/" : "\\";
    return `${d}${sep}gt_input`;
  });

  // 选择父目录
  async function handleSelectParentDir() {
    const s = await open({ directory: true });
    if (s) {
      const path = typeof s === "string" ? s.replace(/\//g, "\\") : s;
      setParentDir(path);
    }
  }

  // 创建项目
  async function handleCreateProject() {
    const dir = projectDir();
    if (!dir) {
      setFeedback({ type: "error", message: "请选择目录并输入项目名称" });
      return;
    }
    try {
      const sep = dir.includes("/") ? "/" : "\\";
      const template = await fetchDefaultProjectConfigTemplate();
      await invoke("write_text_file", {
        path: `${dir}${sep}config.yaml`,
        content: template,
      });
      await invoke("create_dir", { path: dir });
      await invoke("create_dir", { path: `${dir}${sep}gt_input` });
      await invoke("create_dir", { path: `${dir}${sep}gt_output` });
      await invoke("create_dir", { path: `${dir}${sep}transl_cache` });
      setProjectCreated(true);
      setFeedback({ type: "success", message: "项目创建成功！" });
    } catch (err: any) {
      setFeedback({
        type: "error",
        message: `创建失败: ${err.message || String(err)}`,
      });
    }
  }

  // 导入文件
  async function importPathsToInput(paths: string[]) {
    const inputDir = gtInputDir();
    if (!inputDir || paths.length === 0) return;
    const existing = new Set(importedFiles().map((n) => n.toLowerCase()));
    const namesBatch = new Set<string>();
    const toImport: string[] = [];
    const accepted: string[] = [];
    for (const p of paths) {
      const name = p.split(/[/\\]/).pop() || p;
      const key = name.toLowerCase();
      if (existing.has(key) || namesBatch.has(key)) continue;
      namesBatch.add(key);
      toImport.push(p);
      accepted.push(name);
    }
    if (toImport.length === 0) {
      setFeedback({ type: "info", message: "已过滤重复文件，本次无新增导入。" });
      return;
    }
    try {
      await invoke("copy_files", {
        sources: toImport,
        destinationDir: inputDir,
      });
      setImportedFiles((prev) => [...prev, ...accepted]);
      setFeedback({
        type: "success",
        message:
          toImport.length < paths.length
            ? `已导入 ${toImport.length} 个文件，已过滤 ${paths.length - toImport.length} 个重复文件`
            : `已导入 ${toImport.length} 个文件`,
      });
    } catch (err: any) {
      setFeedback({
        type: "error",
        message: `导入失败: ${err.message || String(err)}`,
      });
    }
  }

  // 文件拖拽
  function handleFileDrop(e: DragEvent) {
    if (!gtInputDir()) return;
    e.preventDefault();

    // 尝试从 dataTransfer.files 获取路径
    const dt = e.dataTransfer;
    if (!dt) return;
    const files = Array.from(dt.files);
    const paths = files
      .map((f) => (f as any).path)
      .filter((p): p is string => Boolean(p?.trim()));

    if (paths.length > 0) {
      importPathsToInput(paths);
      return;
    }

    // 否则尝试解析 text/uri-list
    const uriData = dt.getData("text/uri-list") || dt.getData("text/plain");
    if (uriData) {
      const parsed = uriData
        .split(/\r?\n/)
        .map((l) => l.trim())
        .filter((l) => l && !l.startsWith("#"))
        .map((l) => {
          try {
            if (l.startsWith("file://")) {
              const u = new URL(l);
              const d = decodeURIComponent(u.pathname || "");
              return /^\/[A-Za-z]:/.test(d) ? d.slice(1) : d;
            }
            return decodeURIComponent(l);
          } catch {
            return l;
          }
        })
        .map((p) => p.replace(/\//g, "\\"))
        .filter((p) => /^[A-Za-z]:\\/.test(p) || p.startsWith("\\\\"));
      if (parsed.length > 0) importPathsToInput(parsed);
    }
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
    } catch (err: any) {
      setFeedback({
        type: "error",
        message: `打开失败: ${err.message || String(err)}`,
      });
    }
  }

  // 保存设置
  async function handleSaveSettings() {
    const dir = projectDir();
    if (!dir) return;
    try {
      const pid = encodeProjectDir(dir);
      const res = await fetchProjectConfig(pid, "config.yaml");
      const config = { ...res.config };
      const common: Record<string, unknown> = {
        ...((config.common as Record<string, unknown>) || {}),
        workersPerProject: workersPerProject(),
        language: language(),
        "gpt.numPerRequestTranslate": numPerRequest(),
        "gpt.dynamicNumPerRequestTranslate": false,
        "gpt.contextNum": 8,
      };
      if (translationGuideline()) {
        common["gpt.translation_guideline"] = translationGuideline();
      }
      config.common = common;
      config.plugin = {
        ...((config.plugin as Record<string, unknown>) || {}),
        filePlugin: selectedFilePlugin(),
        textPlugins:
          Array.isArray(
            ((config.plugin as Record<string, unknown>) || {}).textPlugins
          )
            ? ((config.plugin as Record<string, unknown>)).textPlugins
            : [],
      };
      await updateProjectConfig(pid, {
        config,
        config_file_name: "config.yaml",
      });
      setSelectedBackendProfile(dir, selectedBackend());
      setSettingsSaved(true);
      setFeedback({ type: "success", message: "设置已保存" });
    } catch (err: any) {
      setFeedback({
        type: "error",
        message: `保存失败: ${err.message || String(err)}`,
      });
    }
  }

  // 完成：提取人名 + 打开项目
  const handleFinish = createMemo(() => {
    return async () => {
      const dir = projectDir();
      if (!dir) return;

      // 如果有文件，提交 dump-name 任务
      if (importedFiles().length > 0) {
        setNameJobStatus("running");
        try {
          const job = await submitJob({
            project_dir: dir,
            config_file_name: "config.yaml",
            translator: "dump-name",
          });
          // 轮询任务状态
          const poll = async () => {
            try {
              const s = await fetchJob(job.job_id);
              if (s.status === "completed") {
                setNameJobStatus("completed");
                setNameJobMessage(
                  s.success
                    ? "人名提取完成！"
                    : `提取完成但有警告: ${s.error || ""}`
                );
              } else if (s.status === "failed") {
                setNameJobStatus("failed");
                setNameJobMessage(s.error || "提取失败");
              } else {
                setTimeout(poll, 2000);
              }
            } catch {
              setTimeout(poll, 3000);
            }
          };
          poll();
        } catch (err: any) {
          setNameJobStatus("failed");
          setNameJobMessage(err.message || String(err));
        }
      } else {
        setNameJobStatus("completed");
        setNameJobMessage("gt_input 中没有文件，已跳过人名提取。");
      }

      // 打开项目
      const pid = encodeProjectDir(dir);
      openProject(pid);
      toast.success("项目已创建并打开");
    };
  });

  // 保存父目录到 localStorage
  createEffect(() => {
    const p = parentDir();
    if (p.trim()) localStorage.setItem(LAST_PARENT_DIR_KEY, p);
  });

  // 第3步：加载后端配置
  createEffect(() => {
    if (currentStep() === 3) {
      fetchPlugins()
        .then((p) =>
          setFilePlugins(p.filter((x: PluginInfo) => x.type === "file"))
        )
        .catch(() => {});
      fetchTranslationGuidelines()
        .then((list: string[]) => {
          setGuidelines(list);
          if (!translationGuideline() && list.length > 0) {
            setTranslationGuideline(
              list.includes("日译中_增强") ? "日译中_增强" : list[0]
            );
          }
        })
        .catch(() => {});
    }
  });

  // settingsSaved 在后续编辑时复位
  createEffect(() => {
    if (settingsSaved()) {
      // 当用户重新编辑时复位
    }
  });

  const canNext = () => {
    if (currentStep() === 0) return projectCreated();
    if (currentStep() === 3) return settingsSaved();
    return true;
  };

  const stepProgress = () =>
    Math.round(((currentStep() + 1) / STEPS.length) * 100);

  function handleBack() {
    setCurrentStep((s) => Math.max(0, s - 1));
  }

  function handleNext() {
    const cur = currentStep();

    // Step 2 → 3: 自动选择后端配置
    if (cur === 1) {
      // nothing special
    }
    // Step 3 → 4: 加载插件
    if (cur === 2) {
      // done in effect
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
                {i() < currentStep() ? (
                  <Icon name="check" size={12} />
                ) : (
                  i() + 1
                )}
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
            <span>第 {currentStep() + 1} / {STEPS.length} 步</span>
            <strong>{STEPS[currentStep()]}</strong>
          </div>
          <div class="wizard-step-summary__bar">
            <span style={{ width: `${stepProgress()}%` }} />
          </div>
        </div>

        <div class="wizard-step-stage">
          {currentStep() === 0 && (
            <StepProjectInfo
              parentDir={parentDir()}
              projectName={projectName()}
              projectDir={projectDir()}
              projectCreated={projectCreated()}
              onSelectParentDir={handleSelectParentDir}
              onParentDirChange={setParentDir}
              onProjectNameChange={setProjectName}
              onProjectCreatedChange={setProjectCreated}
              onCreateProject={handleCreateProject}
            />
          )}
          {currentStep() === 1 && (
            <StepImportFiles
              gtInputDir={gtInputDir()}
              importedFiles={importedFiles()}
              onFileDrop={handleFileDrop}
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
              workersPerProject={workersPerProject()}
              numPerRequest={numPerRequest()}
              language={language()}
              translationGuideline={translationGuideline()}
              guidelines={guidelines()}
              settingsSaved={settingsSaved()}
              filePlugins={filePlugins()}
              onFilePluginChange={setSelectedFilePlugin}
              onWorkersChange={setWorkersPerProject}
              onNumPerRequestChange={setNumPerRequest}
              onLanguageChange={setLanguage}
              onGuidelineChange={setTranslationGuideline}
              onSaveSettings={handleSaveSettings}
            />
          )}
          {currentStep() === 4 && (
            <StepExtractNames
              nameJobStatus={nameJobStatus()}
              nameJobMessage={nameJobMessage()}
            />
          )}
        </div>

        {/* 反馈消息 */}
        <Show when={feedback()}>
          <div
            class={`wizard-feedback wizard-feedback--${feedback()!.type}`}
          >
            {feedback()!.message}
          </div>
        </Show>
      </div>

      {/* 导航按钮 */}
      <div class="wizard-nav">
        <button
          class="btn"
          onClick={handleBack}
          disabled={currentStep() === 0}
        >
          上一步
        </button>
        {currentStep() < 4 ? (
          <button
            class="btn btn--primary"
            onClick={handleNext}
            disabled={!canNext()}
          >
            下一步
          </button>
        ) : (
          <button class="btn btn--primary" onClick={handleFinish()}>
            完成并打开项目
          </button>
        )}
      </div>
    </div>
  );
}
