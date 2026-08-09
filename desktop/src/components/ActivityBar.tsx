import { createSignal, onCleanup, onMount, Show } from "solid-js";
import { Icon } from "./icons/Icon";
import { appState, setAppState, navigateTo, type ActiveView, type SidebarTab } from "../stores/appStore";
import { toast } from "../stores/toastStore";
import { confirm } from "../stores/confirmStore";
import { buildProjectOutput, validateBuild } from "../lib/api/project";
import { getErrorMessage } from "../lib/errors";
import { getShowShortcutButtonsPreference, SHORTCUT_BUTTONS_CHANGE_EVENT } from "../lib/api/preferences";
import type { FileNode } from "../lib/api/types";

const SAMPLE_CACHE_FILENAME = "_示例缓存文件.json";
const PASS3_PREFIX = "pass3_cache/";

/**
 * 在翻译控制台点击查找/问题/备选时自动选一个默认缓存文件显示到主界面。
 * 优先级：上次打开的文件 → 文件夹中第一个非示例译文缓存文件 → 示例缓存文件 → 不显示(null)。
 */
function pickDefaultCacheFile(): string | null {
  const tree = appState.cacheTree;

  // 1) 上次打开的文件（会话内仍保留且仍存在树中）
  const last = appState.activeFilePath;
  if (last) {
    const exists = (() => {
      const walk = (ns: FileNode[]): boolean => {
        for (const n of ns) {
          if (n.is_file && n.path === last) return true;
          if (!n.is_file && n.children && walk(n.children)) return true;
        }
        return false;
      };
      return walk(tree);
    })();
    if (exists) return last;
  }

  // 收集 pass3_cache 下的译文缓存文件（示例文件被后端过滤，不在此列）
  const pass3Files: string[] = [];
  const collect = (ns: FileNode[]) => {
    for (const n of ns) {
      if (!n.is_file) {
        if (n.children) collect(n.children);
        continue;
      }
      if (n.path.startsWith(PASS3_PREFIX)) pass3Files.push(n.path);
    }
  };
  collect(tree);

  // 2) 第一个非示例译文缓存文件
  if (pass3Files.length > 0) return pass3Files[0];
  // 3) 示例缓存文件：后端 _collect_cache_files 会过滤下划线前缀文件，
  //    故示例文件不会出现在 cacheTree 中；按后端新建项目约定回退到固定路径
  return `${PASS3_PREFIX}${SAMPLE_CACHE_FILENAME}`;
}

interface TabDef {
  icon: string;
  view: string;
  label: string;
  /** 快捷按钮跳转的目标视图（点击后 navigateTo 到该视图） */
  shortcutView?: ActiveView;
  /** 快捷按钮跳转后要滚动到的目标元素 id */
  shortcutScrollTarget?: string;
  /** 快捷按钮是否需要已打开项目 */
  needsProject?: boolean;
}

const tabs: TabDef[] = [
  { icon: "play-stroke", view: "translate", label: "翻译控制台" },
  { icon: "edit", view: "review", label: "校对审核" },
  { icon: "search", view: "search", label: "查找替换" },
  { icon: "alert-circle", view: "problems", label: "问题检测" },
  { icon: "swap", view: "alt", label: "查看备选" },
  { icon: "book", view: "dict", label: "字典管理" },
  { icon: "terminal", view: "build-output", label: "构建输出" },
  { icon: "settings", view: "settings", label: "设置" },
];

/** 底部快捷入口按钮（可被「设置 → 前端显示相关」开关隐藏） */
const shortcutTabs: TabDef[] = [
  {
    icon: "server",
    view: "settings-backend",
    label: "项目设置",
    shortcutView: "project-config",
    needsProject: true,
  },
  {
    icon: "exclamation",
    view: "project-problems",
    label: "问题检测项",
    shortcutView: "project-config",
    shortcutScrollTarget: "pc-group-problem-analyze",
    needsProject: true,
  },
];

async function handleBuildOutput() {
  const pid = appState.activeProjectId;
  if (!pid) {
    toast.warning("请先打开一个项目");
    return;
  }

  // 构建前校验（仅提示，不阻断构建）
  const issues: string[] = [];
  try {
    const v = await validateBuild(pid);
    if (v.missing_files && v.missing_files.length > 0) {
      issues.push(
        `缺少缓存的输入文件 ${v.missing_files.length} 个：\n${v.missing_files
          .slice(0, 5)
          .join("\n")}${v.missing_files.length > 5 ? "\n…" : ""}`,
      );
    }
    if (v.content_issues && v.content_issues.length > 0) {
      issues.push(
        `缓存内容异常 ${v.content_issues.length} 处：\n${v.content_issues
          .slice(0, 5)
          .map((c) => `${c.file}: ${c.issue}`)
          .join("\n")}${v.content_issues.length > 5 ? "\n…" : ""}`,
      );
    }
  } catch {
    // 校验失败不阻断构建，直接进入构建确认
  }

  const baseMsg = "将从缓存文件生成最终输出文件。此操作会覆盖已有的输出文件。是否继续？";
  const message =
    issues.length > 0
      ? `构建前校验发现以下问题（不影响继续构建）：\n\n${issues.join("\n\n")}\n\n${baseMsg}`
      : baseMsg;

  const result = await confirm.show({
    title: "构建输出",
    message,
    confirmText: "继续构建",
    cancelText: "取消",
    tone: issues.length > 0 ? "warning" : "info",
  });
  if (!result.confirmed) return;

  toast.info("正在构建输出文件...");
  try {
    const res = await buildProjectOutput(pid);
    toast.success(`构建完成：共生成 ${res.total_built} 个文件`);
    if (res.errors && res.errors.length > 0) {
      toast.warning(`${res.errors.length} 个文件构建出错`);
    }
  } catch (e) {
    toast.error(`构建失败: ${getErrorMessage(e)}`);
  }
}

// 侧栏类按钮（查找/问题检测/查看备选）的 view -> SidebarTab 映射，消除硬编码三元
const SIDEBAR_TAB_OF: Record<string, SidebarTab> = {
  search: "find",
  problems: "problems",
  alt: "alt",
};

function handleTabClick(tab: TabDef) {
  // 快捷按钮：跳转到对应设置类视图并设置滚动目标，由目标页面渲染完成后滚动
  if (tab.shortcutView) {
    if (tab.needsProject && !appState.activeProjectId) {
      toast.warning("请先打开一个项目");
      return;
    }
    navigateTo(tab.shortcutView);
    setAppState({ sidebarOpen: false, settingsScrollTarget: tab.shortcutScrollTarget });
    return;
  }

  if (tab.view === "build-output") {
    handleBuildOutput();
    return;
  }

  // 查找替换 / 问题检测 / 查看备选 属于校对审核场景的侧边栏工具；
  // 翻译控制台下默认不渲染侧边栏，点这几个按钮直接跳转到校对审核页并打开对应面板
  if (tab.view === "search" || tab.view === "problems" || tab.view === "alt") {
    navigateTo("review");
    setAppState({
      sidebarOpen: true,
      sidebarTab: SIDEBAR_TAB_OF[tab.view],
      activeFilePath: pickDefaultCacheFile(),
    });
    return;
  }

  const fullPageViews = ["dict", "settings", "backend-profiles", "plugins", "prompt-templates"];
  if (fullPageViews.includes(tab.view)) {
    navigateTo(tab.view as ActiveView);
    setAppState({ sidebarOpen: false });
    return;
  }

  const sidebarTab = SIDEBAR_TAB_OF[tab.view];
  if (sidebarTab) {
    const alreadyOpen = appState.sidebarOpen && appState.sidebarTab === sidebarTab;
    setAppState({
      sidebarOpen: !alreadyOpen,
      sidebarTab: alreadyOpen ? null : sidebarTab,
    });
    return;
  }

  navigateTo(tab.view as "translate" | "review");
  setAppState({
    sidebarOpen: true,
    sidebarTab: tab.view === "review" ? "explorer" : null,
  });
}

function isActive(tab: TabDef) {
  // 底部快捷入口为跳转工具而非视图导航，不参与高亮，避免与常规导航按钮的 active 语义混淆
  if (tab.shortcutView) return false;
  if (tab.view === appState.activeView) return true;
  const sidebarTab = SIDEBAR_TAB_OF[tab.view];
  if (sidebarTab && sidebarTab === appState.sidebarTab) return true;
  return false;
}

function renderTabButton(tab: TabDef) {
  return (
    <button
      class={`activitybar-btn ${isActive(tab) ? "active" : ""}`}
      onClick={() => handleTabClick(tab)}
      title={tab.label}
      aria-label={tab.label}
    >
      <Icon name={tab.icon} size={22} />
      <span class="activitybar-label">{tab.label}</span>
    </button>
  );
}

export function ActivityBar() {
  const [showShortcuts, setShowShortcuts] = createSignal(getShowShortcutButtonsPreference());
  onMount(() => {
    const handler = () => setShowShortcuts(getShowShortcutButtonsPreference());
    window.addEventListener(SHORTCUT_BUTTONS_CHANGE_EVENT, handler);
    onCleanup(() => window.removeEventListener(SHORTCUT_BUTTONS_CHANGE_EVENT, handler));
  });

  // 底部快捷入口可由「设置 → 前端显示相关」关闭；用函数形式使其响应 showShortcuts() 变化
  const visibleShortcutTabs = () =>
    showShortcuts() ? shortcutTabs.map(renderTabButton) : [];

  return (
    <nav class="activitybar">
      <div class="activitybar-top">{tabs.map(renderTabButton)}</div>
      <div class="activitybar-bottom">
        <Show when={showShortcuts()}>
          <span class="activitybar-section-label">快捷入口</span>
          {visibleShortcutTabs()}
        </Show>
      </div>
    </nav>
  );
}
