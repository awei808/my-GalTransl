import { Icon } from "./icons/Icon";
import { appState, setAppState, navigateTo, type ActiveView, type SidebarTab } from "../stores/appStore";
import { toast } from "../stores/toastStore";
import { confirm } from "../stores/confirmStore";
import { buildProjectOutput, validateBuild } from "../lib/api/project";
import { getErrorMessage } from "../lib/errors";
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
  if (tab.view === appState.activeView) return true;
  const sidebarTab = SIDEBAR_TAB_OF[tab.view];
  if (sidebarTab && sidebarTab === appState.sidebarTab) return true;
  return false;
}

export function ActivityBar() {
  return (
    <nav class="activitybar">
      <div class="activitybar-top">
        {tabs.map((tab) => (
          <button
            class={`activitybar-btn ${isActive(tab) ? "active" : ""}`}
            onClick={() => handleTabClick(tab)}
            title={tab.label}
            aria-label={tab.label}
          >
            <Icon name={tab.icon} size={22} />
            <span class="activitybar-label">{tab.label}</span>
          </button>
        ))}
      </div>
    </nav>
  );
}
