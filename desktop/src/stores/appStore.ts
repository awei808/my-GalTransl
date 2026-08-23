import { createStore } from "solid-js/store";
import { fetchProjectConfigName } from "../lib/api/client";
import type { CacheReplaceField, FileNode, ModelCheckResult } from "../lib/api/types";

// 模型可用性检测状态（与 TranslateConsole 共用，全局持久以避免组件重挂后重复检测）
export type ModelCheckState = "idle" | "checking" | "ok" | "error" | "na";

/** 查找替换侧边栏发起的纯前端替换请求（只改校对页当前打开文件的内存 entries，不写盘） */
export interface ReplaceRequest {
  query: string;
  replacement: string;
  field: CacheReplaceField;
  /** 请求发起时的目标文件（校对页 activeFilePath），消费时校验防误替换 */
  targetFile: string;
  /** 指定时仅替换该 index 的条目（「替换单个」）；缺省为文件内全部替换 */
  onlyIndex?: number;
}

export interface ModelCheckSnapshot {
  state: ModelCheckState;
  result: ModelCheckResult | null;
  backend: string;
  projectId: string | null;
}

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

export type SidebarTab = "explorer" | "find" | "problems" | "alt" | null;

export interface AppState {
  // 导航
  activeView: ActiveView;
  /** 待确认的目标视图：从校对审核页切走且存在未保存修改时由 navigateTo 置位，
      ReviewPage 弹「保存/不保存/取消」确认后消费；确认完成置 null 并切换。 */
  pendingView: ActiveView | null;
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
  /** 翻译控制台当前选中的后端（全局持久，避免组件重挂丢失/切项目后失效） */
  selectedBackend: string;
  /** 缓存目录树（由 cacheWatcher 轮询刷新，供文件浏览器渲染） */
  cacheTree: FileNode[];
  /** 缓存树版本：监控发现"当前打开文件"大小变化时自增，驱动 ReviewPage 局部刷新 */
  cacheVersion: number;
  /** 问题列表版本：监控发现"任一缓存文件"大小变化时自增，驱动问题侧栏刷新（覆盖非当前文件被外部修改的盲区） */
  problemVersion: number;
  /** 模型可用性检测快照（全局持久，避免翻译控制台组件重挂后重复检测/丢失结果） */
  modelCheck: ModelCheckSnapshot;
  /** 上一轮 /runtime 轮询到的任务状态（全局持久，避免切回页面时把"运行中"误判为"刚开始"而重复弹窗） */
  prevJobStatus: string;
  /** 侧边栏问题列表请求跳转到的条目索引（ReviewPage 加载文件后执行滚动，完成后自动清空） */
  reviewJumpToIndex: number | null;
  /** 查找替换侧边栏发起的纯前端替换请求（ReviewPage 消费后自动清空） */
  replaceRequest: ReplaceRequest | null;
  /** 设置类页面滚动目标标识（ActivityBar 快捷按钮点击后写入，目标页面渲染完成后滚动并自动清空） */
  settingsScrollTarget: string | null;

  // 应用级「可见文本」查找（浏览器式 Ctrl+F，扫描 main-area 当前渲染文本）
  globalFindOpen: boolean;
  globalFindQuery: string;
  /** 当前匹配项序号（从 1 开始；无匹配为 -1） */
  globalFindIndex: number;
  /** 匹配总数 */
  globalFindCount: number;
}

// ── 默认状态 ──

export const defaultState: AppState = {
  activeView: "home",
  pendingView: null,
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
  selectedBackend: "",
  cacheTree: [],
  cacheVersion: 0,
  problemVersion: 0,
  modelCheck: { state: "idle", result: null, backend: "", projectId: null },
  prevJobStatus: "",
  reviewJumpToIndex: null,
  replaceRequest: null,
  settingsScrollTarget: null,
  globalFindOpen: false,
  globalFindQuery: "",
  globalFindIndex: -1,
  globalFindCount: 0,
};

// ── Store ──

export const [appState, setAppState] = createStore<AppState>(defaultState);

// ── Actions ──

export function navigateTo(view: ActiveView) {
  // 从校对审核页切走且存在未保存修改（dirtyFiles 由 ReviewPage 在编辑/保存时维护）：
  // 不直接切换，置 pendingView 由 ReviewPage 弹「保存/不保存/取消」确认，
  // 避免未保存修改被静默保存或静默丢弃（与校对页内切换文件的确认语义一致）
  if (appState.activeView === "review" && view !== "review" && appState.dirtyFiles.length > 0) {
    setAppState({ pendingView: view });
    return;
  }
  setAppState({ activeView: view });
  if (view === "settings" || view === "new-project") {
    setAppState({ sidebarOpen: false, sidebarTab: null });
  }
  if (view !== "review") {
    // 离开 review 视图时清除残留的跳转标记，避免切回时误触发滚动
    setAppState("reviewJumpToIndex", null);
  }
  if (view !== "settings" && view !== "project-config") {
    // 离开设置类视图时清除残留的滚动目标，避免切回时误触发滚动
    setAppState("settingsScrollTarget", null);
  }
}

/**
 * 打开项目。会异步探测该项目的真实配置文件名并写入 store，
 * 供各页面/API 贯通使用（config.inc.yaml 项目不再被写死的 config.yaml 覆盖）。
 * @param configFileName 若调用方已知真实配置名（如新建项目恒为 config.yaml）可直接传入，跳过探测。
 */
export async function openProject(projectId: string, opts?: { configFileName?: string }) {
  // 先同步设置导航与项目 ID，保证页面立即切换；同时重置上一项目的状态残留
  // （旧 activeFilePath/dirtyFiles/cacheTree/reviewJumpToIndex 等被带到新项目会
  //  造成打开错误文件、误弹未保存确认、文件树闪现旧项目）
  setAppState({
    activeProjectId: projectId,
    activeConfigFileName: opts?.configFileName ?? null,
    activeView: "translate",
    pendingView: null,
    sidebarOpen: true,
    sidebarTab: null,
    activeFilePath: null,
    dirtyFiles: [],
    cacheTree: [],
    cacheVersion: 0,
    problemVersion: 0,
    reviewJumpToIndex: null,
    replaceRequest: null,
    prevJobStatus: "",
    modelCheck: { state: "idle", result: null, backend: "", projectId: null },
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
      // 仅当仍是同一项目时才清除"探测中"标志，避免快速切换项目时误清新项目的标志
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
    pendingView: null,
    sidebarOpen: false,
    sidebarTab: null,
    activeFilePath: null,
    dirtyFiles: [],
    replaceRequest: null,
    prevJobStatus: "",
    modelCheck: { state: "idle", result: null, backend: "", projectId: null },
  });
}

export function markDirty(filePath: string) {
  setAppState("dirtyFiles", (files) => [...new Set([...files, filePath])]);
}

export function markClean(filePath: string) {
  setAppState("dirtyFiles", (files) => files.filter((f) => f !== filePath));
}
