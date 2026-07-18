import { Icon } from "./icons/Icon";
import {
  appState,
  setAppState,
  navigateTo,
} from "../stores/appStore";

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

function handleTabClick(tab: TabDef) {
  const fullPageViews = ["dict", "settings", "backend-profiles", "plugins", "prompt-templates", "build-output"];
  if (fullPageViews.includes(tab.view)) {
    navigateTo(tab.view as any);
    setAppState({ sidebarOpen: false });
  } else if (["search", "problems"].includes(tab.view)) {
    const alreadyOpen =
      appState.sidebarOpen && appState.sidebarTab === tab.view;
    setAppState({
      sidebarOpen: !alreadyOpen,
      sidebarTab: alreadyOpen ? null : (tab.view as "search" | "problems"),
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
  if (
    ["search", "problems"].includes(tab.view) &&
    tab.view === state.sidebarTab
  )
    return true;
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
          </button>
        ))}
      </div>
    </nav>
  );
}
