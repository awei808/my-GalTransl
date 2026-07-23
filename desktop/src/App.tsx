import "./styles/styles.css";

import { onMount, onCleanup, Show } from "solid-js";
import { open } from "@tauri-apps/plugin-shell";
import { TitleBar } from "./components/TitleBar";
import { ActivityBar } from "./components/ActivityBar";
import { SidebarPanel } from "./components/SidebarPanel";
import { MainArea } from "./components/MainArea";
import { StatusBar } from "./components/StatusBar";
import { ToastHost } from "./components/toast/ToastHost";
import { ConfirmHost } from "./components/confirm/ConfirmHost";
import { appState, setAppState } from "./stores/appStore";

function handleExternalLinkClick(e: MouseEvent) {
  const anchor = (e.target as HTMLElement | null)?.closest("a");
  if (!anchor) return;
  const href = anchor.getAttribute("href") || "";
  // 外部 http(s) 链接走系统浏览器，避免 Tauri 内嵌弹窗（弹窗关闭会误触发窗口 Destroyed）
  if (/^https?:\/\//i.test(href)) {
    e.preventDefault();
    open(href).catch(() => window.open(href, "_blank", "noopener"));
  }
}

function handleGlobalKeyDown(e: KeyboardEvent) {
  if (!e.ctrlKey && !e.metaKey) return;

  switch (e.key) {
    case "f":
      e.preventDefault();
      document.dispatchEvent(new CustomEvent("galtransl:find-in-file"));
      break;
    case "h":
      e.preventDefault();
      setAppState({ sidebarOpen: true, sidebarTab: "find" });
      break;
    case "b":
      e.preventDefault();
      setAppState("sidebarOpen", (s: boolean) => !s);
      break;
    case "s":
      e.preventDefault();
      document.dispatchEvent(new CustomEvent("galtransl:save"));
      break;
  }
}

export function App() {
  const sidebarOpen = () => appState.sidebarOpen;
  // 翻译控制台为只读监控页，不渲染文件浏览器/查找/问题侧边栏
  const showSidebar = () => appState.activeView !== "translate";
  // 应用栏类名：translate 视图收为两列（仅 ActivityBar + 主区）；其余视图按 sidebarOpen 折叠/展开
  const bodyClass = () => (showSidebar() ? (!sidebarOpen() ? "sidebar-collapsed" : "") : "no-sidebar");

  onMount(() => {
    document.addEventListener("keydown", handleGlobalKeyDown);
    document.addEventListener("click", handleExternalLinkClick);
  });
  onCleanup(() => {
    document.removeEventListener("keydown", handleGlobalKeyDown);
    document.removeEventListener("click", handleExternalLinkClick);
  });

  return (
    <>
      <TitleBar />
      <div class={`app-body ${bodyClass()}`}>
        <ActivityBar />
        <Show when={showSidebar()}>
          <div class="sidebar-column">
            <SidebarPanel />
          </div>
        </Show>
        <MainArea />
      </div>
      <StatusBar />
      <ToastHost />
      <ConfirmHost />
    </>
  );
}
