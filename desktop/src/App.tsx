import "./styles/styles.css";

import { onMount, onCleanup } from "solid-js";
import { TitleBar } from "./components/TitleBar";
import { ActivityBar } from "./components/ActivityBar";
import { SidebarPanel } from "./components/SidebarPanel";
import { MainArea } from "./components/MainArea";
import { StatusBar } from "./components/StatusBar";
import { ToastHost } from "./components/toast/ToastHost";
import { ConfirmHost } from "./components/confirm/ConfirmHost";
import { appState, setAppState } from "./stores/appStore";

function handleGlobalKeyDown(e: KeyboardEvent) {
  if (!e.ctrlKey && !e.metaKey) return;

  switch (e.key) {
    case "f":
      e.preventDefault();
      setAppState({ sidebarOpen: true, sidebarTab: "find" });
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
      // 触发 save 事件，各页面自行响应
      document.dispatchEvent(new CustomEvent("galtransl:save"));
      break;
  }
}

export function App() {
  const sidebarOpen = () => appState.sidebarOpen;

  onMount(() => document.addEventListener("keydown", handleGlobalKeyDown));
  onCleanup(() => document.removeEventListener("keydown", handleGlobalKeyDown));

  return (
    <>
      <TitleBar />
      <div class={`app-body ${!sidebarOpen() ? "sidebar-collapsed" : ""}`}>
        <ActivityBar />
        <div class="sidebar-column">
          <SidebarPanel />
        </div>
        <MainArea />
      </div>
      <StatusBar />
      <ToastHost />
      <ConfirmHost />
    </>
  );
}
