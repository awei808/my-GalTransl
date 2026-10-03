/**
 * 剧情路线图渲染区的画布交互：滚轮缩放（以光标为锚点）+ 空白处左键拖拽平移。
 * 供校对页 PlotRoutePanel 与路线图工作台 RouteMapViewer 复用；返回的解绑函数负责清掉监听与光标状态。
 */

/* 不可作为拖拽起点的元素：节点与表单控件（工具栏按钮等）；边标签是装饰文本，允许起手拖拽 */
const PAN_BLOCK_SELECTOR = "button, a, input, select, textarea, .node";

/* 滚轮增量到缩放倍率的换算系数与单次上限（鼠标滚轮一格约 ±18%，触控板小增量平滑） */
const WHEEL_ZOOM_SENSITIVITY = 600;
const WHEEL_ZOOM_MAX_STEP = 1.25;

/* 拖拽平移期间挂在滚动容器上的类名（CSS 据此切换 grabbing 光标） */
export const PANNING_CLASS = "is-panning";

export interface PlotRouteViewportOptions {
  /** overflow:auto 的滚动容器本身（不是它的父容器，否则平移与锚点回算都作用于不可滚动元素） */
  viewer: HTMLElement;
  /** mermaid SVG 宿主；矩形回退/空态时返回 undefined，此时不接管滚轮与拖拽 */
  graph: () => HTMLElement | undefined;
  getZoom: () => number;
  /** 组件侧写入缩放：负责钳制范围并应用 SVG 尺寸 */
  setZoom: (zoom: number) => void;
}

/** 该滚动容器是否正处于拖拽平移中（节点 hover 等交互据此避让） */
export function isViewportPanning(viewer: HTMLElement | undefined): boolean {
  return !!viewer?.classList.contains(PANNING_CLASS);
}

/** 绑定滚轮缩放与空白处拖拽平移，返回解绑函数 */
export function attachPlotRouteViewport(opts: PlotRouteViewportOptions): () => void {
  const { viewer, graph, getZoom, setZoom } = opts;
  let panning = false;
  let startX = 0;
  let startY = 0;
  let startLeft = 0;
  let startTop = 0;
  let prevBodyCursor = "";

  /* 仅 mermaid 渲染成功、且 SVG 宿主仍在文档中时才接管交互：矩形回退/空态保留原生滚动 */
  const hasCanvas = (): boolean => {
    const host = graph();
    return !!host && host.isConnected && !!host.querySelector("svg");
  };

  function wheelFactor(e: WheelEvent): number {
    /* deltaMode：1 以行为单位（约 16px），2 以页为单位（约 100px），其余按像素 */
    const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 100 : 1;
    const raw = Math.exp((-e.deltaY * unit) / WHEEL_ZOOM_SENSITIVITY);
    return Math.min(WHEEL_ZOOM_MAX_STEP, Math.max(1 / WHEEL_ZOOM_MAX_STEP, raw));
  }

  function onWheel(e: WheelEvent): void {
    if (!hasCanvas()) return;
    e.preventDefault();
    const before = getZoom();
    const rect = viewer.getBoundingClientRect();
    const offsetX = e.clientX - rect.left - viewer.clientLeft;
    const offsetY = e.clientY - rect.top - viewer.clientTop;
    const anchorX = offsetX + viewer.scrollLeft;
    const anchorY = offsetY + viewer.scrollTop;
    setZoom(before * wheelFactor(e));
    /* setZoom 可能被范围钳制，按实际生效的倍率回算滚动，使光标下的内容保持不动 */
    const ratio = getZoom() / before;
    if (ratio !== 1) {
      viewer.scrollLeft = anchorX * ratio - offsetX;
      viewer.scrollTop = anchorY * ratio - offsetY;
    }
  }

  function onPointerDown(e: PointerEvent): void {
    if (e.button !== 0 || panning || !hasCanvas()) return;
    /* 触摸/手写笔不接管，保留原生滚动与手势 */
    if (e.pointerType && e.pointerType !== "mouse") return;
    const target = e.target as Element | null;
    if (target?.closest(PAN_BLOCK_SELECTOR)) return;
    e.preventDefault();
    panning = true;
    startX = e.clientX;
    startY = e.clientY;
    startLeft = viewer.scrollLeft;
    startTop = viewer.scrollTop;
    viewer.classList.add(PANNING_CLASS);
    prevBodyCursor = document.body.style.cursor;
    document.body.style.cursor = "grabbing";
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", endPan);
    window.addEventListener("pointercancel", endPan);
    /* 窗口失焦时收不到 pointerup，靠 blur 收尾 */
    window.addEventListener("blur", endPan);
  }

  function onPointerMove(e: PointerEvent): void {
    /* 左键已在窗口外松开时收不到 pointerup：用 buttons 兜底结束平移 */
    if ((e.buttons & 1) === 0) {
      endPan();
      return;
    }
    viewer.scrollLeft = startLeft - (e.clientX - startX);
    viewer.scrollTop = startTop - (e.clientY - startY);
  }

  function endPan(): void {
    if (!panning) return;
    panning = false;
    viewer.classList.remove(PANNING_CLASS);
    document.body.style.cursor = prevBodyCursor;
    window.removeEventListener("pointermove", onPointerMove);
    window.removeEventListener("pointerup", endPan);
    window.removeEventListener("pointercancel", endPan);
    window.removeEventListener("blur", endPan);
  }

  viewer.addEventListener("wheel", onWheel, { passive: false });
  viewer.addEventListener("pointerdown", onPointerDown);

  return () => {
    endPan();
    viewer.removeEventListener("wheel", onWheel);
    viewer.removeEventListener("pointerdown", onPointerDown);
  };
}
