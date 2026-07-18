import { Match, Switch } from "solid-js";
import { appState } from "../stores/appStore";

function FileExplorer() {
  return (
    <div class="sidebar-panel">
      <div class="sidebar-header">文件浏览器</div>
      <div class="sidebar-content">
        <p class="sidebar-placeholder">选择项目文件开始校对</p>
      </div>
    </div>
  );
}

function FindReplacePanel() {
  return (
    <div class="sidebar-panel">
      <div class="sidebar-header">查找替换</div>
      <div class="sidebar-content">
        <div class="find-input-group">
          <input class="find-input" type="text" placeholder="查找" />
        </div>
        <div class="find-input-group">
          <input class="find-input" type="text" placeholder="替换为" />
        </div>
        <div class="find-actions">
          <button class="btn btn--sm">查找</button>
          <button class="btn btn--sm">替换全部</button>
        </div>
      </div>
    </div>
  );
}

function ProblemList() {
  return (
    <div class="sidebar-panel">
      <div class="sidebar-header">问题检测</div>
      <div class="sidebar-content">
        <p class="sidebar-placeholder">暂无问题</p>
      </div>
    </div>
  );
}

function EmptySidebar() {
  return (
    <div class="sidebar-panel">
      <div class="sidebar-content">
        <p class="sidebar-placeholder">侧边栏</p>
      </div>
    </div>
  );
}

export function SidebarPanel() {
  const tab = () => appState.sidebarTab;

  return (
    <Switch>
      <Match when={tab() === "explorer"}>
        <FileExplorer />
      </Match>
      <Match when={tab() === "find"}>
        <FindReplacePanel />
      </Match>
      <Match when={tab() === "problems"}>
        <ProblemList />
      </Match>
      <Match when={!tab()}>
        <EmptySidebar />
      </Match>
    </Switch>
  );
}
