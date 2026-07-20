import { createStore } from "solid-js/store";
import { fetchProjectConfigName } from "../lib/api/client";

// ── 类型 ──

export type ActiveView =
  | "home"
  | "translate"
  | "review"
  | "settings"
  | "new-project"
  | "logs"
  | "dict"
  | "backend-profiles"
  | "plugins"
  | "prompt-templates"
  | "project-config";

export type ConnectionPhase = "offline" | "connecting" | "online" | "reconnecting";

export type SidebarTab = "explorer" | "find" | "problems" | null;

export interface AppState {
  // 导航
  activeView: ActiveView;
  sidebarOpen: boolean;
  sidebarTab: SidebarTab;

  // 项目
  activeProjectId: string | null;
  activeConfigFileName: string | null;
  /** 真实配置名是否正在探测中（打开项目时短暂为 true，避免页面用回退名 config.yaml 提前请求导致 404） */
  configNameDetecting: boolean;
  activeFilePath: string | null;
  dirtyFiles: string[];

  // 连接
  connectionPhase: ConnectionPhase;
  connectionTimeoutMs: number;

  // 后端
  backendOnline: boolean;
}

// ── 默认状态 ──

export const defaultState: AppState = {
  activeView: "home",
  sidebarOpen: false,
  sidebarTab: null,
  activeProjectId: null,
  activeConfigFileName: null,
  configNameDetecting: false,
  activeFilePath: null,
  dirtyFiles: [],
  connectionPhase: "offline",
  connectionTimeoutMs: 20000,
  backendOnline: false,
};

// ── Store ──

export const [appState, setAppState] = createStore<AppState>(defaultState);

// ── Actions ──

export function navigateTo(view: ActiveView) {
  setAppState("activeView", view);
  if (view === "settings") {
    setAppState({ sidebarOpen: false, sidebarTab: null });
  }
  if (view === "new-project") {
    setAppState({ sidebarOpen: false, sidebarTab: null });
  }
}

/**
 * 打开项目。会异步探测该项目的真实配置文件名并写入 store，
 * 供各页面/API 贯通使用（config.inc.yaml 项目不再被写死的 config.yaml 覆盖）。
 * @param configFileName 若调用方已知真实配置名（如新建项目恒为 config.yaml）可直接传入，跳过探测。
 */
export async function openProject(projectId: string, opts?: { configFileName?: string }) {
  // 先同步设置导航与项目 ID，保证页面立即切换
  setAppState({
    activeProjectId: projectId,
    activeConfigFileName: opts?.configFileName ?? null,
    activeView: "translate",
    sidebarOpen: true,
    sidebarTab: null,
  });

  // 未显式提供时，向后端探测真实配置名（config.inc.yaml 优先于 config.yaml）
  if (!opts?.configFileName) {
    setAppState("configNameDetecting", true);
    try {
      const name = await fetchProjectConfigName(projectId);
      // 探测期间用户可能已切换到别的项目，仅当仍是同一项目时才写入
      if (appState.activeProjectId === projectId) {
        setAppState("activeConfigFileName", name);
      }
    } catch {
      // 探测失败则保持 null，调用方回退到 config.yaml 默认
    } finally {
      // 仅当仍是同一项目时才清除“探测中”标志，避免快速切换项目时误清新项目的标志
      if (appState.activeProjectId === projectId) {
        setAppState("configNameDetecting", false);
      }
    }
  }
}

/** 取得当前项目真实配置文件名，未探测到时回退 config.yaml */
export function getActiveConfigFileName(): string {
  return appState.activeConfigFileName || "config.yaml";
}

export function closeProject() {
  setAppState({
    activeProjectId: null,
    activeConfigFileName: null,
    configNameDetecting: false,
    activeView: "home",
    sidebarOpen: false,
    sidebarTab: null,
    activeFilePath: null,
    dirtyFiles: [],
  });
}

export function markDirty(filePath: string) {
  setAppState("dirtyFiles", (files) => [...new Set([...files, filePath])]);
}

export function markClean(filePath: string) {
  setAppState("dirtyFiles", (files) => files.filter((f) => f !== filePath));
}
