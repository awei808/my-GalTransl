import { createSignal, createEffect, For, Show, onMount } from "solid-js";
import { appState, navigateTo, openProject } from "../../stores/appStore";
import { toast } from "../../stores/toastStore";
import { fetchVersion } from "../../lib/api/general";
import { fetchJobs } from "../../lib/api/general";
import { ensureDesktopBackendReady, encodeProjectDir } from "../../lib/api/client";
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
(window as any).__addRecentProject = addRecentProject;

export function HomePage() {
  const [version, setVersion] = createSignal("");
  const [recent, setRecent] = createSignal<RecentProject[]>([]);
  const [jobs, setJobs] = createSignal<Job[]>([]);
  const [loadingVersion, setLoadingVersion] = createSignal(true);

  onMount(() => {
    setRecent(getRecentProjects());

    fetchVersion()
      .then((v) => setVersion(v))
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
    } catch {
      toast.warning("无法连接后端，部分功能可能不可用");
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
      case "running": return "运行中";
      case "completed": return "已完成";
      case "failed": return "失败";
      case "cancelled": return "已取消";
      case "pending": return "等待中";
      default: return status;
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
          {loadingVersion ? "检查版本中…" : version() ? `v${version()}` : ""}
        </p>
        <div class="home-info">
          <p>
            项目地址：
            <a href="https://github.com/xxnuo/GalTransl" target="_blank" rel="noopener">
              github.com/xxnuo/GalTransl
            </a>
          </p>
        </div>
      </div>

      <div class="home-panels">
        {/* ── 最近项目 ── */}
        <div class="home-panel">
          <h3 class="home-panel-title">最近项目</h3>
          <Show
            when={recent().length > 0}
            fallback={<p class="home-panel-empty">暂无最近项目</p>}
          >
            <div class="home-list">
              <For each={recent()}>
                {(p) => (
                  <div class="home-list-item clickable" onClick={() => handleOpenRecent(p.dir)}>
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                      <path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z" />
                    </svg>
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
          <Show
            when={jobs().length > 0}
            fallback={<p class="home-panel-empty">暂无任务记录</p>}
          >
            <div class="home-list">
              <For each={jobs()}>
                {(job) => (
                  <div class="home-list-item">
                    <div class="home-job-left">
                      <span class="home-list-name">{job.translator}</span>
                      <span class="home-list-meta">{new Date(job.created_at).toLocaleString("zh-CN")}</span>
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
