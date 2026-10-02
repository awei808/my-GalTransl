import { createSignal, For, Show, onMount } from "solid-js";
import { Icon } from "../../components/icons";
import { setAppState, openProject, navigateTo, navigateToGuide } from "../../stores/appStore";
import { toast } from "../../stores/toastStore";
import { fetchVersion } from "../../lib/api/general";
import { fetchJobs } from "../../lib/api/general";
import {
  ensureDesktopBackendReady,
  encodeProjectDir,
  isBackendReachable,
} from "../../lib/api/client";
import { fetchProjectFiles } from "../../lib/api/project";
import type { Job } from "../../lib/api/types";

const RECENT_PROJECTS_KEY = "galtransl-recent-projects";
const MAX_RECENT = 10;

interface RecentProject {
  dir: string;
  name: string;
  openedAt: number;
}

function getRecentProjects(): RecentProject[] {
  try {
    const raw = localStorage.getItem(RECENT_PROJECTS_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function addRecentProject(dir: string) {
  const list = getRecentProjects().filter((p) => p.dir !== dir);
  const name = dir.split(/[/\\]/).pop() || dir;
  list.unshift({ dir, name, openedAt: Date.now() });
  if (list.length > MAX_RECENT) list.length = MAX_RECENT;
  localStorage.setItem(RECENT_PROJECTS_KEY, JSON.stringify(list));
}

/** 外部暴露：供 TitleBar 在打开项目后调用 */
window.__addRecentProject = addRecentProject;

/** 首页加载后自动激活后端 */
async function autoActivateBackend() {
  // Rust 的 setup() 已尝试启动后端，这里只需确认连通
  let needsStart = true;
  try {
    needsStart = !(await isBackendReachable(800));
  } catch {
    needsStart = true;
  }
  try {
    if (needsStart) toast.info("正在激活后端服务…");
    await ensureDesktopBackendReady({ timeoutMs: 25000 });
    setAppState({ connectionPhase: "online", backendOnline: true });
    toast.success("后端已就绪");
  } catch {
    toast.warning("后端未连接，部分功能可能需要手动启动 run_backend.py");
    setAppState({ connectionPhase: "offline", backendOnline: false });
  }
}

export function HomePage() {
  const [version, setVersion] = createSignal("");
  const [recent, setRecent] = createSignal<RecentProject[]>([]);
  const [jobs, setJobs] = createSignal<Job[]>([]);
  const [loadingVersion, setLoadingVersion] = createSignal(true);

  onMount(() => {
    setRecent(getRecentProjects());

    // 首页首次绘制完成后再异步激活后端，避免阻塞首屏响应
    requestAnimationFrame(() => requestAnimationFrame(autoActivateBackend));

    fetchVersion()
      .then((v) => setVersion(v.version))
      .catch(() => {})
      .finally(() => setLoadingVersion(false));

    fetchJobs()
      .then((j) => setJobs(j.slice(0, 20)))
      .catch(() => {});
  });

  async function handleOpenRecent(dir: string) {
    toast.info("正在打开项目...");
    try {
      await ensureDesktopBackendReady({ timeoutMs: 30000 });
      setAppState({ connectionPhase: "online", backendOnline: true });
    } catch {
      toast.warning("无法连接后端，部分功能可能不可用");
      setAppState({ connectionPhase: "offline", backendOnline: false });
    }
    const projectId = encodeProjectDir(dir);
    try {
      await fetchProjectFiles(projectId);
    } catch {
      toast.error("项目文件不可用");
      return;
    }
    openProject(projectId);
  }

  function statusLabel(status: string) {
    switch (status) {
      case "running":
        return "运行中";
      case "completed":
        return "已完成";
      case "failed":
        return "失败";
      case "cancelled":
        return "已取消";
      case "pending":
        return "等待中";
      default:
        return status;
    }
  }

  function statusClass(status: string) {
    return `job-status--${status}`;
  }

  return (
    <div class="page page-home">
      <div class="home-welcome">
        <h1 class="home-logo">GalTransl</h1>
        <p class="home-subtitle">视觉小说翻译工具</p>
        <p class="home-version">
          {loadingVersion() ? "检查版本中…" : version() ? `v${version()}` : ""}
        </p>
        <div class="home-info">
          <p>
            项目地址：
                <a href="https://github.com/awei808/my-GalTransl" target="_blank" rel="noopener">
                  github.com/awei808/my-GalTransl
                </a>
          </p>
        </div>
        <button class="btn btn--primary home-cta" onClick={() => navigateTo("new-project")}>
          <Icon name="plus" size={16} />
          新建项目向导
        </button>
      </div>

      <div class="home-panels">
        {/* ── 快速上手 ── */}
        <div class="home-panel">
          <h3 class="home-panel-title">快速上手</h3>
          <div class="home-steps">
            <div class="home-step clickable" onClick={() => navigateTo("new-project")}>
              <span class="home-step-num">1</span>
              <span class="home-step-text">
                <b>新建项目</b>
                <small>用向导创建项目并导入剧本</small>
              </span>
            </div>
            <div class="home-step clickable" onClick={() => navigateTo("backend-profiles")}>
              <span class="home-step-num">2</span>
              <span class="home-step-text">
                <b>配置 API</b>
                <small>填入接口地址与密钥</small>
              </span>
            </div>
            <div class="home-step clickable" onClick={() => navigateTo("translate")}>
              <span class="home-step-num">3</span>
              <span class="home-step-text">
                <b>启动翻译</b>
                <small>九阶段流水线自动执行</small>
              </span>
            </div>
            <button class="home-guide-link" onClick={() => navigateToGuide("01-getting-started.md")}>
              查看完整指南 →
            </button>
          </div>
        </div>

        {/* ── 最近项目 ── */}
        <div class="home-panel">
          <h3 class="home-panel-title">最近项目</h3>
          <Show when={recent().length > 0} fallback={<p class="home-panel-empty">暂无最近项目</p>}>
            <div class="home-list">
              <For each={recent()}>
                {(p) => (
                  <div class="home-list-item clickable" onClick={() => handleOpenRecent(p.dir)}>
                    <Icon name="folder" size={16} />
                    <span class="home-list-name">{p.name}</span>
                    <span class="home-list-meta">{p.dir}</span>
                  </div>
                )}
              </For>
            </div>
          </Show>
        </div>

        {/* ── 最近任务 ── */}
        <div class="home-panel">
          <h3 class="home-panel-title">最近任务</h3>
          <Show when={jobs().length > 0} fallback={<p class="home-panel-empty">暂无任务记录</p>}>
            <div class="home-list">
              <For each={jobs()}>
                {(job) => (
                  <div class="home-list-item">
                    <div class="home-job-left">
                      <span class="home-list-name">{job.translator}</span>
                      <span class="home-list-meta">
                        {new Date(job.created_at).toLocaleString("zh-CN")}
                      </span>
                    </div>
                    <span class={`home-job-status ${statusClass(job.status)}`}>
                      {statusLabel(job.status)}
                    </span>
                  </div>
                )}
              </For>
            </div>
          </Show>
        </div>
      </div>
    </div>
  );
}
