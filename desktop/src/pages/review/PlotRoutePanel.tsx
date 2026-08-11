import { createSignal, createEffect, Show, onMount, onCleanup, on } from "solid-js";
import type { MetadataEntry } from "../../lib/api/types";
import { fetchPerFileMetadata } from "../../lib/api/project";
import { toast } from "../../stores/toastStore";

/* PlotRouteMap.json 数据模型（键与后端 ForPlotRouteMap 输出一致） */
interface PlotRouteMap {
  结构类型?: string;
  用户大纲?: string;
  mermaid?: string;
  文件归属?: Record<string, string>;
  节点剧情?: Record<string, string>;
}

/* mermaid 动态加载（单例，避免重复 import） */
let mermaidMod: Promise<typeof import("mermaid")> | null = null;
function getMermaid() {
  mermaidMod ??= import("mermaid");
  return mermaidMod;
}

const ROUTE_COLORS = ["#636e72", "#fdcb6e", "#e17055", "#00cec9", "#6c5ce7", "#00b894", "#e84393"];

/* 解析 mermaid 源码节点定义：alias -> 显示文本（兼容带/不带引号，跳过 subgraph 行） */
function parseNodes(src: string): Map<string, string> {
  const map = new Map<string, string>();
  const re = /^\s*([A-Za-z_][\w-]*)\s*\[\s*(?:"([^"]*)"|([^\]]*?))\s*\]/gm;
  let m: RegExpExecArray | null;
  while ((m = re.exec(src))) {
    if (m[1].toLowerCase() === "subgraph") continue;
    const label = (m[2] ?? m[3] ?? "").trim();
    if (label) map.set(m[1], label);
  }
  return map;
}

/* 从文件归属反查路线内节点：路线名 -> { color, aliases } */
function buildRoutes(nodes: Map<string, string>, fileRoutes: Record<string, string>) {
  const routes: Record<string, { color: string; aliases: string[] }> = {};
  const routeIdx: Record<string, number> = {};
  for (const label of Object.values(fileRoutes)) {
    if (label && !(label in routeIdx)) routeIdx[label] = Object.keys(routeIdx).length;
  }
  for (const [alias, label] of nodes) {
    const route = fileRoutes[label];
    if (!route) continue;
    if (!(route in routes)) {
      routes[route] = { color: ROUTE_COLORS[routeIdx[route] % ROUTE_COLORS.length], aliases: [] };
    }
    routes[route].aliases.push(alias);
  }
  return routes;
}

type PanelView = "split" | "source" | "preview";

export function PlotRoutePanel(props: {
  projectId: string;
  entry: MetadataEntry;
  index: number;
  onContentChange: (text: string) => void;
  onBlur: () => void;
}) {
  const data = (): PlotRouteMap => props.entry as PlotRouteMap;
  const [source, setSource] = createSignal(data().mermaid ?? "");
  const [fileRoutes, setFileRoutes] = createSignal<Record<string, string>>(data().文件归属 ?? {});
  const [routePlots, setRoutePlots] = createSignal<Record<string, string>>(data().节点剧情 ?? {});
  const [view, setView] = createSignal<PanelView>("split");
  const [zoomLevel, setZoomLevel] = createSignal(1);
  const [renderError, setRenderError] = createSignal("");
  const [tooltip, setTooltip] = createSignal<{ title: string; body: string; x: number; y: number } | null>(null);
  const [editing, setEditing] = createSignal<{ alias: string; label: string; route: string } | null>(null);
  /* pass1 剧情缓存：文件名 -> 「剧情」字段（懒加载，hover 时按需获取并缓存） */
  const [filePlots, setFilePlots] = createSignal<Record<string, string>>({});
  /* 编辑脏标记：本地编辑一旦发生即置位，用于区分「编辑自身回写」与「切换文件/外部更新」 */
  const [dirty, setDirty] = createSignal(false);

  let graphRef: HTMLDivElement | undefined;
  let viewerRef: HTMLDivElement | undefined;
  let taRef: HTMLTextAreaElement | undefined;
  let svgOrigW = 0;
  let svgOrigH = 0;
  let renderTimer: ReturnType<typeof setTimeout> | undefined;

  /* props.entry 变化时的同步策略：
     1. 编辑自身回写（handleMetaContentChange 把本组件修改写回 entry）→ entry 的 mermaid
        与当前 source() 一致，应保持编辑态、跳过同步，避免覆盖正在输入的内容；
     2. 切换文件 / 外部更新 → entry 的 mermaid 与当前 source() 不同，应无条件加载新数据
        并复位 dirty，避免状态残留。 */
  createEffect(
    on(
      () => props.entry,
      (next) => {
        const nextMap = next as PlotRouteMap;
        if (dirty() && nextMap.mermaid === source()) return;
        setDirty(false);
        setSource(nextMap.mermaid ?? "");
        setFileRoutes(nextMap.文件归属 ?? {});
        setRoutePlots(nextMap.节点剧情 ?? {});
        setFilePlots({});
        setEditing(null);
        setTooltip(null);
        setRenderError("");
        setView("split");
        setZoomLevel(1);
        void render();
      },
    ),
  );

  function emitChange() {
    setDirty(true);
    const obj: PlotRouteMap = {
      结构类型: data().结构类型 ?? "",
      用户大纲: data().用户大纲 ?? "",
      mermaid: source(),
      文件归属: fileRoutes(),
      节点剧情: routePlots(),
    };
    props.onContentChange(JSON.stringify(obj, null, 2));
  }

  async function render() {
    if (!graphRef) return;
    setRenderError("");
    try {
      const mermaid = (await getMermaid()).default;
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "loose",
        theme: "default",
        fontFamily: '"Microsoft YaHei", sans-serif',
        flowchart: { htmlLabels: true, curve: "basis", padding: 12 },
      });
      const { svg, bindFunctions } = await mermaid.render("plotRouteGraph", source());
      graphRef.innerHTML = svg;
      bindFunctions?.(graphRef);
      setupSvg();
      bindInteractions();
    } catch (e) {
      const msg = String(e instanceof Error ? e.message : e).replace(/[<>&]/g, (c) =>
        ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c] as string),
      );
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

  /* 精确查找 mermaid 节点：id 形如 "plotRouteGraph-flowchart-R1-13"，取倒数第二段 */
  function findNodeEl(alias: string): HTMLElement | null {
    if (!graphRef) return null;
    return (
      [...graphRef.querySelectorAll<HTMLElement>(".node")].find((n) => {
        const parts = (n.id || "").split("-");
        return parts.length >= 3 && parts[parts.length - 2] === alias;
      }) ?? null
    );
  }

  function clearHighlights() {
    graphRef?.querySelectorAll<HTMLElement>(".plotroute-hl").forEach((n) => {
      n.classList.remove("plotroute-hl");
      n.style.filter = "";
    });
  }

  /* 程序读取 pass1 缓存中该文件 meta 的「剧情」字段；无 meta/无剧情时返回空串 */
  async function getFilePlot(filename: string): Promise<string> {
    const cached = filePlots()[filename];
    if (cached !== undefined) return cached;
    if (!props.projectId || !filename) return "";
    try {
      const res = await fetchPerFileMetadata(props.projectId, "filemeta", filename);
      const plot = res?.exists && res.entry ? String(res.entry["剧情"] ?? "") : "";
      setFilePlots((m) => ({ ...m, [filename]: plot }));
      return plot;
    } catch (e) {
      if (import.meta.env?.DEV) {
        console.debug(`[PlotRoutePanel] 读取文件剧情失败：${filename}`, e);
      }
      setFilePlots((m) => ({ ...m, [filename]: "" }));
      return "";
    }
  }

  function bindInteractions() {
    const nodes = parseNodes(source());
    const routes = buildRoutes(nodes, fileRoutes());
    for (const [alias, label] of nodes) {
      const el = findNodeEl(alias);
      if (!el) continue;
      const route = fileRoutes()[label] ?? Object.keys(routes).find((r) => routes[r].aliases.includes(alias));
      const routeObj = route ? routes[route] : undefined;
      el.style.cursor = "pointer";
      el.addEventListener("mouseenter", (e: MouseEvent) => {
        clearHighlights();
        if (routeObj) {
          for (const a of routeObj.aliases) {
            const n = findNodeEl(a);
            if (n) {
              n.classList.add("plotroute-hl");
              n.style.filter = `brightness(1.25) drop-shadow(0 0 4px ${routeObj.color})`;
            }
          }
        }
        // 悬停文本由程序读取 pass1 缓存中该文件 meta 的「剧情」字段（不借助 AI）
        setTooltip({ title: label, body: "加载中…", x: e.clientX + 14, y: e.clientY + 14 });
        void getFilePlot(label).then((plot) => {
          const body =
            plot || (route ? routePlots()[route] || "该文件暂无剧情摘要" : "该节点未关联路线");
          setTooltip((t) => (t && t.title === label ? { ...t, body } : t));
        });
      });
      el.addEventListener("mousemove", (e: MouseEvent) => {
        setTooltip((t) => (t ? { ...t, x: e.clientX + 14, y: e.clientY + 14 } : t));
      });
      el.addEventListener("mouseleave", () => {
        clearHighlights();
        setTooltip(null);
      });
      el.addEventListener("click", (e: Event) => {
        e.stopPropagation();
        setEditing({ alias, label, route: route ?? "" });
      });
    }
  }

  function handleSourceInput(v: string) {
    setSource(v);
    clearTimeout(renderTimer);
    renderTimer = setTimeout(() => void render(), 400);
    emitChange();
  }

  function saveNodeEdit() {
    const ed = editing();
    if (!ed) return;
    const labelInput = document.getElementById("plotroute-node-label") as HTMLInputElement | null;
    const routeSel = document.getElementById("plotroute-node-route") as HTMLSelectElement | null;
    const newLabel = labelInput?.value.trim() || ed.alias;
    const newRoute = routeSel?.value || "";
    const hasQuoted = source().split("\n").some((l) => l.includes(`${ed.alias}["`));
    const quoted = JSON.stringify(ed.label).slice(1, -1);
    const target = hasQuoted ? `${ed.alias}["${quoted}"]` : `${ed.alias}[${ed.label}]`;
    const replacement = hasQuoted ? `${ed.alias}["${newLabel}"]` : `${ed.alias}[${newLabel}]`;
    // 防护：节点定义行必须能在源码中找到，否则替换无效，避免 fileRoutes 键与节点文本脱节
    if (!source().includes(target)) {
      toast.warning("未能在源码中找到该节点，无法保存修改");
      return;
    }
    const nextSrc = source().split(target).join(replacement);
    const fr = { ...fileRoutes() };
    for (const f of Object.keys(fr)) {
      if (f === ed.label) delete fr[f];
    }
    if (newLabel && newRoute) fr[newLabel] = newRoute;
    const rp = { ...routePlots() };
    if (newRoute && !(newRoute in rp)) rp[newRoute] = "";
    setSource(nextSrc);
    setFileRoutes(fr);
    setRoutePlots(rp);
    setEditing(null);
    emitChange();
    props.onBlur(); // 编辑节点保存 = 提交落盘
    void render();
  }

  function deleteNodeEdit() {
    const ed = editing();
    if (!ed) return;
    const nextSrc = source().split("\n").filter((l) => !l.includes(`${ed.alias}[`)).join("\n");
    const fr = { ...fileRoutes() };
    for (const f of Object.keys(fr)) {
      if (f === ed.label) delete fr[f];
    }
    setSource(nextSrc);
    setFileRoutes(fr);
    setEditing(null);
    emitChange();
    props.onBlur(); // 删除节点保存 = 提交落盘
    void render();
  }

  function switchView(v: PanelView) {
    setView(v);
    if (v !== "source") requestAnimationFrame(() => zoomFit());
  }

  const routeOptions = (): string[] => {
    const s = new Set<string>([...Object.values(fileRoutes()), ...Object.keys(routePlots())].filter(Boolean));
    return [...s];
  };

  onMount(() => {
    void render();
    setTimeout(() => zoomFit(), 300);
  });

  onCleanup(() => {
    clearTimeout(renderTimer);
  });

  return (
    <div class={`plotroute-panel plotroute-view-${view()}`}>
      <div class="plotroute-toolbar">
        <div class="plotroute-tb-group">
          <span class="plotroute-tb-label">视图</span>
          <button class="plotroute-tb-btn" classList={{ active: view() === "split" }} onClick={() => switchView("split")}>双栏</button>
          <button class="plotroute-tb-btn" classList={{ active: view() === "source" }} onClick={() => switchView("source")}>仅源码</button>
          <button class="plotroute-tb-btn" classList={{ active: view() === "preview" }} onClick={() => switchView("preview")}>仅渲染</button>
        </div>
        <Show when={view() !== "source"}>
          <div class="plotroute-tb-group">
            <span class="plotroute-tb-label">缩放</span>
            <button class="plotroute-tb-btn" onClick={() => setZoom(zoomLevel() / 1.2)} title="缩小">−</button>
            <span class="plotroute-zoom-level">{Math.round(zoomLevel() * 100)}%</span>
            <button class="plotroute-tb-btn" onClick={() => setZoom(zoomLevel() * 1.2)} title="放大">＋</button>
            <button class="plotroute-tb-btn" onClick={zoomFit}>适屏</button>
            <button class="plotroute-tb-btn" onClick={() => setZoom(1)}>重置</button>
          </div>
        </Show>
      </div>

      <div class="plotroute-layout">
        <div class="plotroute-panel-src">
          <div class="plotroute-panel-title">
            mermaid 源码
            <span class="plotroute-hint">（直接编辑，右侧实时渲染）</span>
          </div>
          <div class="plotroute-editor">
            <textarea
              ref={taRef}
              class="plotroute-source"
              spellcheck={false}
              value={source()}
              onInput={(e) => handleSourceInput(e.currentTarget.value)}
              onBlur={() => props.onBlur()}
            />
          </div>
          <div class="plotroute-legend">
            {Object.entries(buildRoutes(parseNodes(source()), fileRoutes())).map(([name, r]) => (
              <span class="plotroute-legend-item">
                <span class="plotroute-legend-swatch" style={{ background: r.color }} />
                {name}
              </span>
            ))}
          </div>
        </div>

        <div
          class="plotroute-panel-preview"
          ref={viewerRef}
          onWheel={(e) => {
            if (!e.ctrlKey && !e.metaKey) return;
            e.preventDefault();
            if (e.deltaY < 0) setZoom(zoomLevel() * 1.2);
            else setZoom(zoomLevel() / 1.2);
          }}
        >
          <div class="plotroute-panel-title">
            渲染预览
            <span class="plotroute-hint">hover 高亮路线 · click 编辑节点 · Ctrl+滚轮缩放</span>
          </div>
          <div class="plotroute-viewer">
            <div class="plotroute-graph" ref={graphRef} />
            <Show when={renderError()}>
              <div class="plotroute-error">渲染失败：{renderError()}</div>
            </Show>
          </div>
        </div>
      </div>

      {/* hover tooltip */}
      <Show when={tooltip()}>
        <div class="plotroute-tooltip" style={{ left: `${tooltip()!.x}px`, top: `${tooltip()!.y}px` }}>
          <div class="plotroute-tooltip-file">{tooltip()!.title}</div>
          <div class="plotroute-tooltip-body">{tooltip()!.body}</div>
        </div>
      </Show>

      {/* 编辑节点弹窗 */}
      <Show when={editing()}>
        <div class="plotroute-modal-mask" onClick={() => setEditing(null)}>
          <div class="plotroute-modal" onClick={(e) => e.stopPropagation()}>
            <h3>编辑节点</h3>
            <label>节点 ID</label>
            <input value={editing()!.alias} disabled />
            <label>显示名称</label>
            <input id="plotroute-node-label" value={editing()!.label} placeholder="输入节点显示名" />
            <label>所属路线</label>
            <select id="plotroute-node-route">
              <Show when={routeOptions().length === 0}>
                <option value="">（未关联）</option>
              </Show>
              {routeOptions().map((r) => (
                <option value={r} selected={r === editing()!.route}>
                  {r}
                </option>
              ))}
            </select>
            <div class="plotroute-modal-btns">
              <button class="plotroute-btn plotroute-btn-danger" onClick={deleteNodeEdit}>删除节点</button>
              <button class="plotroute-btn" onClick={() => setEditing(null)}>取消</button>
              <button class="plotroute-btn plotroute-btn-primary" onClick={saveNodeEdit}>保存</button>
            </div>
          </div>
        </div>
      </Show>
    </div>
  );
}
