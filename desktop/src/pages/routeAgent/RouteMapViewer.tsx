import { createEffect, createSignal, For, on, onCleanup, Show } from "solid-js";
import type { MetadataEntry } from "../../lib/api/types";
import { buildRoutes, getMermaid, parseNodes, ROUTE_COLORS, type PlotRouteMap } from "../review/PlotRoutePanel";

/**
 * 路线图工作台的路线图渲染器：只显示 mermaid 渲染结果，不显示源码。
 * 渲染失败或无 mermaid 时退化为按路线分组的有序矩形列表；
 * 节点/矩形右键（或左键）切换选中，把文件加入底边栏 agent。
 */
export function RouteMapViewer(props: {
  entry: MetadataEntry | null;
  selectedFiles: string[];
  onToggleSelect: (filename: string) => void;
}) {
  const data = (): PlotRouteMap => (props.entry ?? {}) as PlotRouteMap;
  const [renderError, setRenderError] = createSignal("");
  const [zoomLevel, setZoomLevel] = createSignal(1);
  const [tooltip, setTooltip] = createSignal<{ title: string; body: string; x: number; y: number } | null>(null);

  let graphRef: HTMLDivElement | undefined;
  let viewerRef: HTMLDivElement | undefined;
  let svgOrigW = 0;
  let svgOrigH = 0;
  let disposed = false;
  let graphSeq = 0;
  let renderSeq = 0;

  const selected = () => new Set(props.selectedFiles);
  const hasMermaid = () => !!(data().mermaid ?? "").trim();
  /* 渲染失败或无 mermaid → 矩形回退 */
  const useFallback = () => !hasMermaid() || renderError() !== "";

  createEffect(
    on(
      () => props.entry,
      () => {
        setRenderError("");
        setZoomLevel(1);
        void render();
      },
    ),
  );

  onCleanup(() => {
    disposed = true;
    // 清理 mermaid 留在 body 的错误残留（id 为 d<graphId> 前缀）
    document.querySelectorAll('[id^="drouteAgentGraph-"]').forEach((el) => el.remove());
  });

  async function render() {
    if (!graphRef || disposed || !hasMermaid()) return;
    const seq = ++renderSeq;
    const graphId = `routeAgentGraph-${++graphSeq}`;
    setRenderError("");
    try {
      const mermaid = (await getMermaid()).default;
      if (disposed) return;
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "loose",
        theme: "default",
        fontFamily: '"Microsoft YaHei", sans-serif',
        flowchart: { htmlLabels: true, curve: "basis", padding: 12 },
      });
      const { svg, bindFunctions } = await mermaid.render(graphId, data().mermaid ?? "", graphRef);
      if (disposed || seq !== renderSeq) return;
      graphRef.innerHTML = svg;
      bindFunctions?.(graphRef);
      setupSvg();
      bindInteractions();
      applySelection();    } catch (e) {
      if (disposed || seq !== renderSeq) return;
      const msg = String(e instanceof Error ? e.message : e).replace(/[<>&]/g, (c) =>
        ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c] as string),
      );
      if (graphRef) graphRef.innerHTML = "";
      setRenderError(msg);
    }
  }

  function setupSvg() {
    const svg = graphRef?.querySelector("svg");
    if (!svg) return;
    const vb = svg.getAttribute("viewBox");
    if (!vb) return;
    const [, , w, h] = vb.split(/\s+/).map(Number);
    if (!w || !h) return;
    svgOrigW = w;
    svgOrigH = h;
    svg.removeAttribute("style");
    applyZoom();
  }

  function applyZoom() {
    const svg = graphRef?.querySelector("svg");
    if (!svg || !svgOrigW) return;
    svg.setAttribute("width", `${Math.round(svgOrigW * zoomLevel())}px`);
    svg.setAttribute("height", `${Math.round(svgOrigH * zoomLevel())}px`);
  }

  function setZoom(k: number) {
    setZoomLevel(Math.min(3, Math.max(0.1, k)));
    applyZoom();
  }

  function zoomFit() {
    if (!viewerRef || !svgOrigW || !svgOrigH) return;
    const k = Math.min(viewerRef.clientWidth / svgOrigW, viewerRef.clientHeight / svgOrigH, 1);
    setZoom(Math.max(0.1, k));
  }

  /* mermaid 节点 id 形如 "routeAgentGraph-flowchart-R1-13"，取倒数第二段为别名 */
  function findNodeEl(alias: string): HTMLElement | null {
    if (!graphRef) return null;
    return (
      [...graphRef.querySelectorAll<HTMLElement>(".node")].find((n) => {
        const parts = (n.id || "").split("-");
        return parts.length >= 3 && parts[parts.length - 2] === alias;
      }) ?? null
    );
  }

  function routeOf(filename: string): string {
    return data().文件归属?.[filename] ?? "";
  }

  function bindInteractions() {
    const nodes = parseNodes(data().mermaid ?? "");
    const routes = buildRoutes(nodes, data().文件归属 ?? {});
    for (const [alias, label] of nodes) {
      const el = findNodeEl(alias);
      if (!el) continue;
      const route = routeOf(label);
      const routeObj = route ? routes[route] : undefined;
      el.style.cursor = "pointer";
      const toggle = (e: Event) => {
        e.preventDefault();
        e.stopPropagation();
        props.onToggleSelect(label);
      };
      el.addEventListener("contextmenu", toggle);
      el.addEventListener("click", toggle);
      el.addEventListener("mouseenter", (e: MouseEvent) => {
        if (routeObj) {
          for (const a of routeObj.aliases) {
            findNodeEl(a)?.classList.add("plotroute-hl");
          }
        }
        const body = route ? data().节点剧情?.[route] || "该路线暂无剧情摘要" : "该节点未关联路线";
        setTooltip({ title: label, body, x: e.clientX + 14, y: e.clientY + 14 });
      });
      el.addEventListener("mousemove", (e: MouseEvent) => {
        setTooltip((t) => (t ? { ...t, x: e.clientX + 14, y: e.clientY + 14 } : t));
      });
      el.addEventListener("mouseleave", () => {
        graphRef?.querySelectorAll<HTMLElement>(".plotroute-hl").forEach((n) => n.classList.remove("plotroute-hl"));
        setTooltip(null);
      });
    }
  }

  /* 选中集变化后重套高亮；mermaid 重渲染后 DOM 重建也由此恢复 */
  function applySelection() {
    if (!graphRef) return;
    const sel = selected();
    for (const [alias, label] of parseNodes(data().mermaid ?? "")) {
      findNodeEl(alias)?.classList.toggle("route-node-selected", sel.has(label));
    }
  }
  createEffect(() => {
    selected();
    applySelection();
  });

  /* 矩形回退：文件顺序优先取 mermaid 节点顺序，缺 mermaid 时按「文件归属」键序；按路线分组着色 */
  function rectGroups(): { route: string; color: string; files: string[] }[] {
    const fileRoutes = data().文件归属 ?? {};
    const mermaidSrc = data().mermaid ?? "";
    let orderedFiles: string[];
    if (mermaidSrc.trim()) {
      orderedFiles = [...parseNodes(mermaidSrc).values()];
      for (const f of Object.keys(fileRoutes)) {
        if (!orderedFiles.includes(f)) orderedFiles.push(f);
      }
    } else {
      orderedFiles = Object.keys(fileRoutes);
    }
    const groups = new Map<string, { route: string; color: string; files: string[] }>();
    for (const file of orderedFiles) {
      const route = fileRoutes[file] || "未分组";
      if (!groups.has(route)) {
        groups.set(route, { route, color: ROUTE_COLORS[groups.size % ROUTE_COLORS.length], files: [] });
      }
      groups.get(route)!.files.push(file);
    }
    return [...groups.values()];
  }

  const isEmpty = () =>
    !hasMermaid() && Object.keys(data().文件归属 ?? {}).length === 0;

  return (
    <div class="route-viewer" ref={viewerRef} onDragOver={(e) => e.preventDefault()}>
      <Show when={!isEmpty()} fallback={<div class="route-viewer-empty">尚未生成剧情路线图，可先运行完整流水线或让 Agent 创建。</div>}>
        <Show
          when={!useFallback()}
          fallback={
            <div class="route-fallback">
              <Show when={renderError()}>
                <div class="route-fallback-error">mermaid 渲染失败（{renderError()}），已退化为列表展示</div>
              </Show>
              <For each={rectGroups()}>
                {(group) => (
                  <div class="route-fallback-group">
                    <div class="route-fallback-route" style={{ color: group.color }}>
                      {group.route}
                    </div>
                    <div class="route-fallback-rects">
                      <For each={group.files}>
                        {(file) => (
                          <button
                            type="button"
                            class="route-rect"
                            classList={{ "route-rect--selected": selected().has(file) }}
                            style={{ "border-color": group.color }}
                            title={`${file}（${routeOf(file) || "未分组"}）点击/右键切换选中`}
                            onClick={() => props.onToggleSelect(file)}
                            onContextMenu={(e) => {
                              e.preventDefault();
                              props.onToggleSelect(file);
                            }}
                          >
                            {file}
                          </button>
                        )}
                      </For>
                    </div>
                  </div>
                )}
              </For>
            </div>
          }
        >
          <div class="route-graph" ref={graphRef} />
        </Show>
      </Show>
      <div class="route-viewer-toolbar">
        <button type="button" onClick={() => setZoom(zoomLevel() * 1.2)} title="放大">
          +
        </button>
        <button type="button" onClick={() => setZoom(zoomLevel() / 1.2)} title="缩小">
          −
        </button>
        <button type="button" onClick={zoomFit} title="适屏">
          适屏
        </button>
        <span class="route-viewer-hint">右键节点可把文件加入 Agent</span>
      </div>
      <Show when={tooltip()}>
        {(t) => (
          <div class="route-tooltip" style={{ left: `${t().x}px`, top: `${t().y}px` }}>
            <div class="route-tooltip-title">{t().title}</div>
            <div class="route-tooltip-body">{t().body}</div>
          </div>
        )}
      </Show>
    </div>
  );
}
