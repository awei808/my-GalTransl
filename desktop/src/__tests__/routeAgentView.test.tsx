/**
 * 路线图工作台 RouteMapViewer 的回退渲染与选中交互单测。
 *
 * 回归背景：0.5.4 新增 route-agent 视图，要求 mermaid 渲染失败 / 缺失时
 * 退化为按路线分组的有序矩形列表，且节点可右键（或左键）切换选中。
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, cleanup, fireEvent } from "@solidjs/testing-library";
import { RouteMapViewer } from "../pages/routeAgent/RouteMapViewer";

afterEach(() => cleanup());

describe("RouteMapViewer 矩形回退", () => {
  it("无 mermaid 时按文件归属分组渲染矩形", () => {
    const { container } = render(() => (
      <RouteMapViewer
        entry={{
          mermaid: "",
          文件归属: { "01_a.json": "共通线", "02_b.json": "共通线", "03_c.json": "琴美线" },
        }}
        selectedFiles={[]}
        onToggleSelect={() => {}}
      />
    ));
    const rects = container.querySelectorAll(".route-rect");
    expect(rects.length).toBe(3);
    const routes = [...container.querySelectorAll(".route-fallback-route")].map((el) => el.textContent);
    expect(routes).toEqual(["共通线", "琴美线"]);
  });

  it("完全无数据时显示空态提示", () => {
    const { container } = render(() => (
      <RouteMapViewer entry={{}} selectedFiles={[]} onToggleSelect={() => {}} />
    ));
    expect(container.querySelector(".route-viewer-empty")).not.toBeNull();
  });

  it("点击/右键矩形触发 onToggleSelect", () => {
    const onToggle = vi.fn();
    const { container } = render(() => (
      <RouteMapViewer
        entry={{ mermaid: "", 文件归属: { "01_a.json": "共通线" } }}
        selectedFiles={[]}
        onToggleSelect={onToggle}
      />
    ));
    const rect = container.querySelector(".route-rect")!;
    fireEvent.click(rect);
    fireEvent.contextMenu(rect);
    expect(onToggle).toHaveBeenCalledTimes(2);
    expect(onToggle).toHaveBeenCalledWith("01_a.json");
  });

  it("选中态渲染为 route-rect--selected", () => {
    const { container } = render(() => (
      <RouteMapViewer
        entry={{ mermaid: "", 文件归属: { "01_a.json": "共通线", "02_b.json": "共通线" } }}
        selectedFiles={["01_a.json"]}
        onToggleSelect={() => {}}
      />
    ));
    expect(container.querySelectorAll(".route-rect--selected").length).toBe(1);
  });
});
