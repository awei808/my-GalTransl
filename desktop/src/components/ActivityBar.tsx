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
  { icon: "translate", view: "translate", label: "翻译控制台" },
  { icon: "review", view: "review", label: "校对审核" },
  { icon: "search", view: "search", label: "查找替换" },
  { icon: "problems", view: "problems", label: "问题检测" },
  { icon: "settings", view: "settings", label: "设置" },
];

function handleTabClick(tab: TabDef) {
  if (tab.view === "settings") {
    navigateTo("settings");
    setAppState({ sidebarOpen: false });
  } else if (["search", "problems"].includes(tab.view)) {
    setAppState({
      sidebarOpen: true,
      sidebarTab: tab.view as "search" | "problems",
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
      {tabs.map((tab) => (
        <button
          class={`activitybar-btn ${isActive(tab) ? "active" : ""}`}
          onClick={() => handleTabClick(tab)}
          title={tab.label}
          aria-label={tab.label}
        >
          {/* SVG icons — placeholder boxes for now, will be replaced with Icon component */}
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="3" width="18" height="18" rx="2" />
          </svg>
        </button>
      ))}
    </nav>
  );
}
