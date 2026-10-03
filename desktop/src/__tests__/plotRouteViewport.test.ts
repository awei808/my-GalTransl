/**
 * 剧情路线图画布交互单测：滚轮以光标为锚点缩放、空白处左键拖拽平移。
 * 组件侧只负责接线，逻辑集中在 lib/plotRouteViewport，故此处直接测该纯函数。
 */
import { afterEach, describe, expect, it } from "vitest";
import { attachPlotRouteViewport } from "../lib/plotRouteViewport";

/* 与组件侧同款写入口径：钳制 0.1~3；用闭包变量模拟 zoom signal */
function makeZoom(): { getZoom: () => number; setZoom: (zoom: number) => void; level: () => number } {
  let level = 1;
  return {
    level: () => level,
    getZoom: () => level,
    setZoom: (zoom: number) => {
      level = Math.min(3, Math.max(0.1, zoom));
    },
  };
}

interface TestViewer {
  viewer: HTMLDivElement;
  graph: HTMLDivElement | undefined;
  svg: SVGSVGElement | null;
  node: SVGGElement | null;
}

/* 已挂载进文档的假容器：真实场景画布必在文档中，模块用 isConnected 判定是否仍可交互 */
const mounted: HTMLElement[] = [];

/* 800x600 的假画布容器；withSvg=false 模拟 mermaid 渲染失败后的矩形回退态 */
function makeViewer(withSvg: boolean): TestViewer {
  const viewer = document.createElement("div");
  viewer.className = "plotroute-viewer";
  viewer.getBoundingClientRect = () =>
    ({ left: 0, top: 0, right: 800, bottom: 600, width: 800, height: 600, x: 0, y: 0 }) as DOMRect;
  document.body.appendChild(viewer);
  mounted.push(viewer);
  if (!withSvg) return { viewer, graph: undefined, svg: null, node: null };
  const graph = document.createElement("div");
  graph.className = "plotroute-graph";
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const node = document.createElementNS("http://www.w3.org/2000/svg", "g");
  node.setAttribute("class", "node default");
  svg.appendChild(node);
  graph.appendChild(svg);
  viewer.appendChild(graph);
  return { viewer, graph, svg, node };
}

function wheel(init: WheelEventInit): WheelEvent {
  return new WheelEvent("wheel", { bubbles: true, cancelable: true, ...init });
}

function pointerDown(init: MouseEventInit): MouseEvent {
  return new MouseEvent("pointerdown", { bubbles: true, cancelable: true, button: 0, ...init });
}

/* 真实拖拽中的 pointermove 一定带 buttons=1（模块用 buttons 兜底判断左键是否已松开） */
function pointerMove(init: MouseEventInit): MouseEvent {
  return new MouseEvent("pointermove", { bubbles: true, buttons: 1, ...init });
}

describe("attachPlotRouteViewport", () => {
  let detach: (() => void) | null = null;

  afterEach(() => {
    detach?.();
    detach = null;
    document.body.style.cursor = "";
    mounted.splice(0).forEach((el) => el.remove());
  });

  it("滚轮向上放大并阻止容器滚动，光标下的内容保持不动", () => {
    const { viewer, graph, svg } = makeViewer(true);
    const zoom = makeZoom();
    detach = attachPlotRouteViewport({ viewer, graph: () => graph, getZoom: zoom.getZoom, setZoom: zoom.setZoom });
    viewer.scrollLeft = 100;
    viewer.scrollTop = 50;
    const ev = wheel({ deltaY: -100, clientX: 300, clientY: 200 });
    svg!.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(true);
    expect(zoom.level()).toBeGreaterThan(1);
    /* 锚点内容坐标 = 光标偏移 + 滚动量：(300+100, 200+50)；缩放后滚动量按实际倍率回算 */
    const ratio = zoom.level();
    expect(viewer.scrollLeft).toBeCloseTo(400 * ratio - 300, 6);
    expect(viewer.scrollTop).toBeCloseTo(250 * ratio - 200, 6);
  });

  it("滚轮向下缩小，到达缩放下限后不再修正滚动", () => {
    const { viewer, graph } = makeViewer(true);
    const zoom = makeZoom();
    detach = attachPlotRouteViewport({ viewer, graph: () => graph, getZoom: zoom.getZoom, setZoom: zoom.setZoom });
    viewer.dispatchEvent(wheel({ deltaY: 100, clientX: 300, clientY: 200 }));
    expect(zoom.level()).toBeLessThan(1);
    zoom.setZoom(0.1);
    viewer.scrollLeft = 100;
    viewer.scrollTop = 50;
    viewer.dispatchEvent(wheel({ deltaY: 100, clientX: 300, clientY: 200 }));
    expect(zoom.level()).toBe(0.1);
    expect(viewer.scrollLeft).toBe(100);
    expect(viewer.scrollTop).toBe(50);
  });

  it("无 SVG（矩形回退）时不接管滚轮，保留原生滚动", () => {
    const { viewer, graph } = makeViewer(false);
    const zoom = makeZoom();
    detach = attachPlotRouteViewport({ viewer, graph: () => graph, getZoom: zoom.getZoom, setZoom: zoom.setZoom });
    const ev = wheel({ deltaY: -100, clientX: 300, clientY: 200 });
    viewer.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(false);
    expect(zoom.level()).toBe(1);
  });

  it("SVG 宿主已脱离文档（切到矩形回退）时同样不接管滚轮与拖拽", () => {
    const { viewer, graph, svg } = makeViewer(true);
    const zoom = makeZoom();
    detach = attachPlotRouteViewport({ viewer, graph: () => graph, getZoom: zoom.getZoom, setZoom: zoom.setZoom });
    graph!.remove();
    expect(svg!.isConnected).toBe(false);
    const ev = wheel({ deltaY: -100, clientX: 300, clientY: 200 });
    viewer.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(false);
    expect(zoom.level()).toBe(1);
    viewer.dispatchEvent(pointerDown({ clientX: 10, clientY: 10 }));
    expect(viewer.classList.contains("is-panning")).toBe(false);
  });

  it("空白处左键拖拽平移画布，拖动中挂 is-panning 并锁闭手光标", () => {
    const { viewer, graph, svg } = makeViewer(true);
    const zoom = makeZoom();
    detach = attachPlotRouteViewport({ viewer, graph: () => graph, getZoom: zoom.getZoom, setZoom: zoom.setZoom });
    viewer.scrollLeft = 100;
    viewer.scrollTop = 100;
    const down = pointerDown({ clientX: 200, clientY: 200 });
    svg!.dispatchEvent(down);
    expect(down.defaultPrevented).toBe(true);
    expect(viewer.classList.contains("is-panning")).toBe(true);
    expect(document.body.style.cursor).toBe("grabbing");
    window.dispatchEvent(pointerMove({ clientX: 160, clientY: 230 }));
    expect(viewer.scrollLeft).toBe(140);
    expect(viewer.scrollTop).toBe(70);
    window.dispatchEvent(new MouseEvent("pointerup"));
    expect(viewer.classList.contains("is-panning")).toBe(false);
    expect(document.body.style.cursor).toBe("");
    /* 抬起后不再跟随指针 */
    window.dispatchEvent(pointerMove({ clientX: 0, clientY: 0 }));
    expect(viewer.scrollLeft).toBe(140);
  });

  it("左键在窗口外松开（buttons 无左键）或窗口失焦时自动结束平移", () => {
    const { viewer, graph, svg } = makeViewer(true);
    const zoom = makeZoom();
    detach = attachPlotRouteViewport({ viewer, graph: () => graph, getZoom: zoom.getZoom, setZoom: zoom.setZoom });
    viewer.scrollLeft = 100;
    viewer.scrollTop = 100;
    svg!.dispatchEvent(pointerDown({ clientX: 200, clientY: 200 }));
    window.dispatchEvent(pointerMove({ clientX: 200, clientY: 200, buttons: 0 }));
    expect(viewer.classList.contains("is-panning")).toBe(false);
    expect(document.body.style.cursor).toBe("");
    expect(viewer.scrollLeft).toBe(100);
    window.dispatchEvent(pointerMove({ clientX: 20, clientY: 20 }));
    expect(viewer.scrollLeft).toBe(100);
    /* blur 兜底 */
    svg!.dispatchEvent(pointerDown({ clientX: 200, clientY: 200 }));
    expect(viewer.classList.contains("is-panning")).toBe(true);
    window.dispatchEvent(new Event("blur"));
    expect(viewer.classList.contains("is-panning")).toBe(false);
  });

  it("节点/按钮上按下不拖拽，触摸与右键指针也不接管", () => {
    const { viewer, graph, node } = makeViewer(true);
    const zoom = makeZoom();
    detach = attachPlotRouteViewport({ viewer, graph: () => graph, getZoom: zoom.getZoom, setZoom: zoom.setZoom });
    viewer.scrollLeft = 10;
    viewer.scrollTop = 10;
    node!.dispatchEvent(pointerDown({ clientX: 5, clientY: 5 }));
    expect(viewer.classList.contains("is-panning")).toBe(false);
    window.dispatchEvent(pointerMove({ clientX: 500, clientY: 500 }));
    expect(viewer.scrollLeft).toBe(10);
    const button = document.createElement("button");
    graph!.appendChild(button);
    button.dispatchEvent(pointerDown({ clientX: 5, clientY: 5 }));
    expect(viewer.classList.contains("is-panning")).toBe(false);
    const touch = pointerDown({ clientX: 5, clientY: 5 });
    Object.defineProperty(touch, "pointerType", { value: "touch" });
    graph!.dispatchEvent(touch);
    expect(viewer.classList.contains("is-panning")).toBe(false);
    graph!.dispatchEvent(pointerDown({ button: 2, clientX: 5, clientY: 5 }));
    expect(viewer.classList.contains("is-panning")).toBe(false);
  });

  it("边标签是装饰文本，在它上面按下可以起手拖拽", () => {
    const { viewer, graph, svg } = makeViewer(true);
    const zoom = makeZoom();
    detach = attachPlotRouteViewport({ viewer, graph: () => graph, getZoom: zoom.getZoom, setZoom: zoom.setZoom });
    const label = document.createElementNS("http://www.w3.org/2000/svg", "g");
    label.setAttribute("class", "edgeLabel");
    svg!.appendChild(label);
    label.dispatchEvent(pointerDown({ clientX: 40, clientY: 40 }));
    expect(viewer.classList.contains("is-panning")).toBe(true);
  });

  it("解绑后滚轮与拖拽都不再响应", () => {
    const { viewer, graph, svg } = makeViewer(true);
    const zoom = makeZoom();
    detach = attachPlotRouteViewport({ viewer, graph: () => graph, getZoom: zoom.getZoom, setZoom: zoom.setZoom });
    detach();
    detach = null;
    const ev = wheel({ deltaY: -100, clientX: 300, clientY: 200 });
    viewer.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(false);
    expect(zoom.level()).toBe(1);
    viewer.scrollLeft = 10;
    svg!.dispatchEvent(pointerDown({ clientX: 5, clientY: 5 }));
    expect(viewer.classList.contains("is-panning")).toBe(false);
    window.dispatchEvent(pointerMove({ clientX: 500, clientY: 500 }));
    expect(viewer.scrollLeft).toBe(10);
  });
});
