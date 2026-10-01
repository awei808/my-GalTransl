import { createEffect, createSignal, For, on, onCleanup, Show } from "solid-js";
import type { MetadataEntry } from "../../lib/api/types";
import { fetchPerFileMetadata } from "../../lib/api/project";
import { getErrorMessage } from "../../lib/errors";
import { appState } from "../../stores/appStore";
import { toast } from "../../stores/toastStore";
import { RouteMapViewer } from "./RouteMapViewer";
import { RunPanel } from "./RunPanel";

/**
 * 路线图工作台：上半区渲染剧情路线图（节点右键多选文件），
 * 底边栏是按指令提交翻译任务的执行终端（不经 AI，直连 /api/jobs）。
 */
export function RouteAgentPage() {
  const [entry, setEntry] = createSignal<MetadataEntry | null>(null);
  const [loading, setLoading] = createSignal(false);
  const [selectedFiles, setSelectedFiles] = createSignal<string[]>([]);
  const [dockHeight, setDockHeight] = createSignal(320);
  let removeResizeListeners: (() => void) | null = null;

  function startResize(e: PointerEvent) {
    e.preventDefault();
    const startY = e.clientY;
    const startH = dockHeight();
    const onMove = (ev: PointerEvent) => {
      setDockHeight(Math.min(window.innerHeight - 200, Math.max(160, startH + (startY - ev.clientY))));
    };
    removeResizeListeners = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      removeResizeListeners = null;
    };
    const onUp = () => {
      removeResizeListeners?.();
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }

  onCleanup(() => removeResizeListeners?.());

  let refreshSeq = 0;

  async function refresh() {
    const pid = appState.activeProjectId;
    if (!pid) return;
    const seq = ++refreshSeq;
    setLoading(true);
    try {
      // plotroute 是固定文件（PlotRouteMap.json），filename 传空串命中 /metadata/plotroute/ 路由
      const res = await fetchPerFileMetadata(pid, "plotroute", "");
      // 单调序号守卫：切项目或并发 refresh 时，过期响应不得覆盖新结果
      if (appState.activeProjectId !== pid || seq !== refreshSeq) return;
      setEntry(res.exists && res.entry ? res.entry : null);
    } catch (e) {
      if (appState.activeProjectId === pid && seq === refreshSeq)
        toast.error(`读取路线图失败: ${getErrorMessage(e)}`);
    } finally {
      if (appState.activeProjectId === pid && seq === refreshSeq) setLoading(false);
    }
  }

  createEffect(
    on(
      () => appState.activeProjectId,
      () => {
        setSelectedFiles([]);
        setEntry(null);
        void refresh();
      },
    ),
  );

  function toggleSelect(filename: string) {
    setSelectedFiles((files) =>
      files.includes(filename) ? files.filter((f) => f !== filename) : [...files, filename],
    );
  }

  return (
    <div class="route-agent-page">
      <header class="route-agent-header">
        <div>
          <h2 class="page-title">路线图工作台</h2>
          <p class="page-description">
            查看剧情路线图，右键节点选择文件加入执行范围，再由执行终端按指令提交翻译后端。
          </p>
        </div>
        <button type="button" class="route-agent-refresh" disabled={loading()} onClick={() => void refresh()}>
          {loading() ? "加载中…" : "刷新"}
        </button>
      </header>
      <Show when={appState.activeProjectId} fallback={<div class="route-agent-no-project">请先打开一个项目</div>}>
        <div class="route-agent-main">
          <RouteMapViewer entry={entry()} selectedFiles={selectedFiles()} onToggleSelect={toggleSelect} />
          <div class="route-agent-resizer" onPointerDown={startResize} />
          <section class="route-agent-dock" style={{ height: `${dockHeight()}px` }}>
            <div class="route-agent-dock-header">
              <div class="route-agent-dock-tabs">
                <span class="route-agent-dock-title">执行终端</span>
              </div>
              <div class="route-agent-selected">
                <Show when={selectedFiles().length > 0} fallback={<span class="route-agent-selected-empty">未选择文件</span>}>
                  <span class="route-agent-selected-count">已选 {selectedFiles().length} 个文件：</span>
                  <div class="route-agent-chips">
                    <For each={selectedFiles()}>
                      {(file) => (
                        <span class="route-chip" title={file}>
                          {file}
                          <button
                            type="button"
                            class="route-chip-remove"
                            onClick={() => toggleSelect(file)}
                            aria-label={`移除 ${file}`}
                          >
                            ×
                          </button>
                        </span>
                      )}
                    </For>
                  </div>
                  <button type="button" class="route-agent-clear" onClick={() => setSelectedFiles([])}>
                    清空
                  </button>
                </Show>
              </div>
            </div>
            <div class="route-agent-dock-body">
              <RunPanel projectId={appState.activeProjectId!} selectedFiles={selectedFiles()} />
            </div>
          </section>
        </div>
      </Show>
    </div>
  );
}
