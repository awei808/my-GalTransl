import { Match, Switch, createSignal, onCleanup } from "solid-js";
import { appState, setAppState } from "../stores/appStore";

/* ── 文件浏览器 ── */
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

/* ── 查找替换 ── */
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
        <div class="find-actions" style="display:flex; gap:8px; margin-top:8px;">
          <button class="btn btn--sm">查找</button>
          <button class="btn btn--sm">替换全部</button>
        </div>
      </div>
    </div>
  );
}

/* ── 问题检测 ── */
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

/* ── 空侧栏 ── */
function EmptySidebar() {
  return (
    <div class="sidebar-panel">
      <div class="sidebar-content">
        <p class="sidebar-placeholder">侧边栏</p>
      </div>
    </div>
  );
}

/* ── 侧边栏容器（含拖拽调整宽度） ── */
export function SidebarPanel() {
  const tab = () => appState.sidebarTab;
  const [dragging, setDragging] = createSignal(false);

  function handlePointerDown(e: PointerEvent) {
    e.preventDefault();
    setDragging(true);

    const root = document.documentElement;

    function handlePointerMove(e: PointerEvent) {
      // sidebar width = pointer x - sidebar-left (which is 48px for activity bar)
      const sidebarLeft = 48;
      const newWidth = Math.max(180, Math.min(500, e.clientX - sidebarLeft));
      root.style.setProperty("--sidebar-expanded-width", `${newWidth}px`);
    }

    function handlePointerUp() {
      setDragging(false);
      document.removeEventListener("pointermove", handlePointerMove);
      document.removeEventListener("pointerup", handlePointerUp);
    }

    document.addEventListener("pointermove", handlePointerMove);
    document.addEventListener("pointerup", handlePointerUp);
  }

  onCleanup(() => {
    setDragging(false);
  });

  return (
    <div class="sidebar-wrapper" style={{ position: "relative" }}>
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
      <div
        class={`sidebar-resize-handle ${dragging() ? "active" : ""}`}
        onPointerDown={handlePointerDown}
      />
    </div>
  );
}
