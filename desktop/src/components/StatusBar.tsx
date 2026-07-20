import { appState } from "../stores/appStore";

export function StatusBar() {
  const phaseLabel = () => {
    switch (appState.connectionPhase) {
      case "online":
        return "已连接";
      case "connecting":
        return "连接中…";
      case "reconnecting":
        return "重连中…";
      case "offline":
        return "离线";
    }
  };

  const phaseClass = () => `status-${appState.connectionPhase}`;

  return (
    <footer class="statusbar">
      <div class="statusbar-left">
        <span class={`status-indicator ${phaseClass()}`}>{phaseLabel()}</span>
        {appState.activeProjectId && <span class="status-project">项目已打开</span>}
      </div>
      <div class="statusbar-right">
        <span class="status-view">{appState.activeView}</span>
      </div>
    </footer>
  );
}
