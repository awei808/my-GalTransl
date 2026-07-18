import "./styles/styles.css";

import { TitleBar } from "./components/TitleBar";
import { ActivityBar } from "./components/ActivityBar";
import { SidebarPanel } from "./components/SidebarPanel";
import { MainArea } from "./components/MainArea";
import { StatusBar } from "./components/StatusBar";
import { ToastHost } from "./components/toast/ToastHost";
import { ConfirmHost } from "./components/confirm/ConfirmHost";
import { appState } from "./stores/appStore";

export function App() {
  const sidebarOpen = () => appState.sidebarOpen;

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
