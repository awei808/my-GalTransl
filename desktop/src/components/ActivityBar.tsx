import { Icon } from "./icons/Icon";
import { appState, setAppState, navigateTo, type ActiveView } from "../stores/appStore";
import { toast } from "../stores/toastStore";
import { confirm } from "../stores/confirmStore";
import { buildProjectOutput } from "../lib/api/project";
import { getErrorMessage } from "../lib/errors";

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

  const result = await confirm.show({
    title: "构建输出",
    message: "将从缓存文件生成最终输出文件。此操作会覆盖已有的输出文件。是否继续？",
    confirmText: "开始构建",
    tone: "info",
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

function handleTabClick(tab: TabDef) {
  if (tab.view === "build-output") {
    handleBuildOutput();
    return;
  }

  const fullPageViews = ["dict", "settings", "backend-profiles", "plugins", "prompt-templates"];
  if (fullPageViews.includes(tab.view)) {
    navigateTo(tab.view as ActiveView);
    setAppState({ sidebarOpen: false });
  } else if (["search", "problems"].includes(tab.view)) {
    const alreadyOpen =
      appState.sidebarOpen && appState.sidebarTab === (tab.view === "search" ? "find" : tab.view);
    setAppState({
      sidebarOpen: !alreadyOpen,
      sidebarTab: alreadyOpen ? null : tab.view === "search" ? "find" : (tab.view as "problems"),
    });
  } else {
    navigateTo(tab.view as "translate" | "review");
    setAppState({
      sidebarOpen: true,
      sidebarTab: tab.view === "review" ? "explorer" : null,
    });
  }
}

function isActive(tab: TabDef) {
  const state = appState;
  if (tab.view === state.activeView) return true;
  if (["search", "problems"].includes(tab.view) && tab.view === state.sidebarTab) return true;
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
