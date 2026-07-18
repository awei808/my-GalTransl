import { createStore } from "solid-js/store";

// ── 类型 ──

export type ActiveView = "home" | "translate" | "review" | "settings" | "new-project" | "logs" | "dict" | "backend-profiles" | "plugins" | "prompt-templates" | "build-output" | "project-config";

export type ConnectionPhase =
  | "offline"
  | "connecting"
  | "online"
  | "reconnecting";

export type SidebarTab = "explorer" | "find" | "problems" | null;

export interface AppState {
  // 导航
  activeView: ActiveView;
  sidebarOpen: boolean;
  sidebarTab: SidebarTab;

  // 项目
  activeProjectId: string | null;
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

export function openProject(projectId: string) {
  setAppState({
    activeProjectId: projectId,
    activeView: "translate",
    sidebarOpen: true,
    sidebarTab: null,
  });
}

export function closeProject() {
  setAppState({
    activeProjectId: null,
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
