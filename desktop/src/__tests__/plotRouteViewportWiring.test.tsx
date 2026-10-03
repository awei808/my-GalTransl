/**
 * 两处路线图渲染器的交互接线单测：mermaid 渲染完成后，
 * 滚轮应只缩放一次（防旧 Ctrl+滚轮处理器残留叠加）、空白处左键拖拽应平移真正的滚动容器。
 * mermaid 用桩替身：jsdom 无布局，真实 mermaid 渲染拿不到 viewBox。
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@solidjs/testing-library";
import { createSignal } from "solid-js";

vi.mock("mermaid", () => ({
  default: {
    initialize: vi.fn(),
    render: vi.fn(async () => ({
      svg: '<svg viewBox="0 0 200 100"><g class="node default" id="flowchart-R1-1"><rect/></g></svg>',
      bindFunctions: undefined,
    })),
  },
}));

import { PlotRoutePanel } from "../pages/review/PlotRoutePanel";
import { RouteMapViewer } from "../pages/routeAgent/RouteMapViewer";

const ENTRY = { mermaid: 'R1["a.json"]', 文件归属: { "a.json": "共通线" } };

afterEach(() => {
  cleanup();
  document.body.style.cursor = "";
});

/* 渲染是异步的：等 mermaid 桩的 SVG 注入完成 */
async function waitForSvg(container: HTMLElement): Promise<SVGSVGElement> {
  await vi.waitFor(() => {
    expect(container.querySelector(".plotroute-graph svg, .route-graph svg")).not.toBeNull();
  });
  return container.querySelector(".plotroute-graph svg, .route-graph svg") as SVGSVGElement;
}

function svgWidth(svg: SVGSVGElement): number {
  return Number.parseFloat(svg.getAttribute("width") ?? "0");
}

function pointerEvent(type: string, init: MouseEventInit): MouseEvent {
  return new MouseEvent(type, { bubbles: true, cancelable: true, ...init });
}

/* 拖拽中的 pointermove 一定带 buttons=1 */
function moveEvent(init: MouseEventInit): MouseEvent {
  return pointerEvent("pointermove", { buttons: 1, ...init });
}

/* 断言单次滚轮只缩放一次：旧 Ctrl+滚轮处理器若仍在，同一事件会被叠加成约 1.4 倍 */
function expectSingleZoomStep(svg: SVGSVGElement, listener: () => void): void {
  const before = svgWidth(svg);
  listener();
  const ratio = svgWidth(svg) / before;
  expect(ratio).toBeGreaterThan(1.1);
  expect(ratio).toBeLessThan(1.3);
}

describe("RouteMapViewer 画布交互接线", () => {
  it("滚轮只缩放一次，空白处拖拽平移画布", async () => {
    const { container } = render(() => (
      <RouteMapViewer entry={ENTRY} selectedFiles={[]} onToggleSelect={() => {}} />
    ));
    const viewer = container.querySelector(".route-viewer") as HTMLElement;
    const svg = await waitForSvg(container);
    expect(svgWidth(svg)).toBe(200);

    expectSingleZoomStep(svg, () => fireEvent.wheel(viewer, { deltaY: -100, clientX: 100, clientY: 100 }));
    expectSingleZoomStep(svg, () => fireEvent.wheel(viewer, { deltaY: -100, ctrlKey: true, clientX: 100, clientY: 100 }));

    viewer.scrollLeft = 50;
    viewer.scrollTop = 40;
    svg.dispatchEvent(pointerEvent("pointerdown", { button: 0, clientX: 200, clientY: 200 }));
    expect(viewer.classList.contains("is-panning")).toBe(true);
    window.dispatchEvent(moveEvent({ clientX: 180, clientY: 220 }));
    expect(viewer.scrollLeft).toBe(70);
    expect(viewer.scrollTop).toBe(20);
    window.dispatchEvent(new MouseEvent("pointerup"));
    expect(viewer.classList.contains("is-panning")).toBe(false);
  });

  it("在节点上按下不触发拖拽", async () => {
    const { container } = render(() => (
      <RouteMapViewer entry={ENTRY} selectedFiles={[]} onToggleSelect={() => {}} />
    ));
    const viewer = container.querySelector(".route-viewer") as HTMLElement;
    const svg = await waitForSvg(container);
    const node = svg.querySelector(".node") as SVGGElement;
    viewer.scrollLeft = 50;
    node.dispatchEvent(pointerEvent("pointerdown", { button: 0, clientX: 10, clientY: 10 }));
    expect(viewer.classList.contains("is-panning")).toBe(false);
    window.dispatchEvent(moveEvent({ clientX: 400, clientY: 400 }));
    expect(viewer.scrollLeft).toBe(50);
  });

  it("渲染成功后切到矩形回退（SVG 宿主已脱离文档）不再劫持滚轮", async () => {
    const [entry, setEntry] = createSignal<{ mermaid: string; 文件归属: Record<string, string> }>(ENTRY);
    const { container } = render(() => (
      <RouteMapViewer entry={entry()} selectedFiles={[]} onToggleSelect={() => {}} />
    ));
    const viewer = container.querySelector(".route-viewer") as HTMLElement;
    await waitForSvg(container);
    setEntry({ mermaid: "", 文件归属: { "a.json": "共通线" } });
    await vi.waitFor(() => {
      expect(container.querySelector(".route-fallback")).not.toBeNull();
    });
    const ev = new WheelEvent("wheel", { deltaY: -100, cancelable: true, bubbles: true, clientX: 10, clientY: 10 });
    viewer.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(false);
    viewer.dispatchEvent(pointerEvent("pointerdown", { button: 0, clientX: 10, clientY: 10 }));
    expect(viewer.classList.contains("is-panning")).toBe(false);
  });
});

describe("PlotRoutePanel 画布交互接线", () => {
  it("滚轮只缩放一次，拖拽平移作用在 .plotroute-viewer 上", async () => {
    const { container } = render(() => (
      <PlotRoutePanel projectId="p1" entry={ENTRY} index={0} onContentChange={() => {}} onBlur={() => {}} />
    ));
    const viewer = container.querySelector(".plotroute-viewer") as HTMLElement;
    const preview = container.querySelector(".plotroute-panel-preview") as HTMLElement;
    const svg = await waitForSvg(container);

    expectSingleZoomStep(svg, () => fireEvent.wheel(viewer, { deltaY: -100, clientX: 100, clientY: 100 }));
    expectSingleZoomStep(svg, () => fireEvent.wheel(viewer, { deltaY: -100, ctrlKey: true, clientX: 100, clientY: 100 }));

    /* 外层是 overflow:hidden 的父容器，平移必须落在内层滚动容器上 */
    viewer.scrollLeft = 30;
    viewer.scrollTop = 20;
    svg.dispatchEvent(pointerEvent("pointerdown", { button: 0, clientX: 200, clientY: 200 }));
    expect(viewer.classList.contains("is-panning")).toBe(true);
    expect(preview.classList.contains("is-panning")).toBe(false);
    window.dispatchEvent(moveEvent({ clientX: 150, clientY: 190 }));
    expect(viewer.scrollLeft).toBe(80);
    expect(viewer.scrollTop).toBe(30);
    window.dispatchEvent(new MouseEvent("pointerup"));
    expect(viewer.classList.contains("is-panning")).toBe(false);
  });
});
