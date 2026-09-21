/**
 * 文件归属（用户自划路线）逻辑测试。
 *
 * 验收入口：0.5.0「剧情路线图可由用户自己划分」——
 * 用户勾选若干文件 → 归入一条路线 → 该归属写入 文件归属 并驱动节点配色。
 *
 * 只测纯逻辑（归属写入、候选集推导、路线计数），不测 DOM，
 * 因为面板组件已由 reviewPage 相关测试覆盖渲染路径。
 */
import { describe, it, expect } from "vitest";
import { applyFileRoutes, mermaidToFlow } from "../lib/plotRoute/mermaidToFlow";
import { flowToMermaid, graphsEquivalent } from "../lib/plotRoute/flowToMermaid";

/** 与 PlotRoutePanel.handleBatchAssign 同口径的归属写入逻辑（纯函数版） */
function assignFiles(
  fileRoutes: Record<string, string>,
  routePlots: Record<string, string>,
  files: string[],
  route: string,
): { fileRoutes: Record<string, string>; routePlots: Record<string, string> } {
  const fr = { ...fileRoutes };
  for (const f of files) fr[f] = route;
  const rp = { ...routePlots };
  if (route && !(route in rp)) rp[route] = "";
  return { fileRoutes: fr, routePlots: rp };
}

/** 与 PlotRoutePanel.assignableFiles 同口径的候选集推导 */
function assignableFiles(labels: string[], fileRoutes: Record<string, string>): string[] {
  const s = new Set<string>();
  for (const l of labels) if (l) s.add(l);
  for (const f of Object.keys(fileRoutes)) if (f) s.add(f);
  return [...s];
}

describe("用户自划路线：归属写入", () => {
  it("把多个文件归入同一路线", () => {
    const { fileRoutes } = assignFiles({}, {}, ["a.json", "b.json"], "华恋线");
    expect(fileRoutes["a.json"]).toBe("华恋线");
    expect(fileRoutes["b.json"]).toBe("华恋线");
  });

  it("覆盖已有归属（改线）", () => {
    const { fileRoutes } = assignFiles(
      { "a.json": "序章" },
      { 序章: "", 华恋线: "" },
      ["a.json"],
      "华恋线",
    );
    expect(fileRoutes["a.json"]).toBe("华恋线");
  });

  it("新路线自动补空摘要占位", () => {
    const { routePlots } = assignFiles({}, { 序章: "旧" }, ["a.json"], "新线");
    expect(routePlots["新线"]).toBe("");
    expect(routePlots["序章"]).toBe("旧");
  });

  it("已有路线不覆盖其摘要", () => {
    const { routePlots } = assignFiles({}, { 华恋线: "原有摘要" }, ["a.json"], "华恋线");
    expect(routePlots["华恋线"]).toBe("原有摘要");
  });

  it("空路线名不新增占位（由 UI 层拦截）", () => {
    const { routePlots } = assignFiles({}, {}, ["a.json"], "");
    expect(Object.keys(routePlots)).toHaveLength(0);
  });

  it("不修改传入的原对象（纯函数）", () => {
    const fr = { "a.json": "序章" };
    const rp = { 序章: "x" };
    assignFiles(fr, rp, ["a.json"], "新线");
    expect(fr["a.json"]).toBe("序章");
    expect(rp["序章"]).toBe("x");
  });
});

describe("候选文件集推导", () => {
  it("图内节点 label 与已有归属键合并去重", () => {
    const files = assignableFiles(["a.json", "b.json", "a.json"], { "b.json": "序章", "c.json": "序章" });
    expect(files.sort()).toEqual(["a.json", "b.json", "c.json"]);
  });

  it("空 label 被跳过", () => {
    expect(assignableFiles(["", "a.json"], {})).toEqual(["a.json"]);
  });
});

describe("归属变更后的往返一致性", () => {
  const SRC = [
    "flowchart TD",
    '  A["a.json"]',
    '  B["b.json"]',
    '  C["c.json"]',
    "  A --> B",
    "  B --> C",
  ].join("\n");

  it("改线后往返仍等价（拓扑不变，仅归属变）", () => {
    const g1 = applyFileRoutes(mermaidToFlow(SRC).graph!, {
      "a.json": "序章",
      "b.json": "华恋线",
      "c.json": "华恋线",
    });
    const g2 = applyFileRoutes(mermaidToFlow(flowToMermaid(g1)).graph!, {
      "a.json": "序章",
      "b.json": "华恋线",
      "c.json": "华恋线",
    });
    expect(graphsEquivalent(g1, g2)).toBe(true);
  });

  it("重新归属后 subgraph 分组随路线变化", () => {
    const before = applyFileRoutes(mermaidToFlow(SRC).graph!, {
      "a.json": "序章",
      "b.json": "序章",
      "c.json": "序章",
    });
    // 单一路线 → 1 个 subgraph
    expect(flowToMermaid(before).match(/subgraph /g)).toHaveLength(1);

    const after = applyFileRoutes(mermaidToFlow(SRC).graph!, {
      "a.json": "序章",
      "b.json": "华恋线",
      "c.json": "华恋线",
    });
    // 两条路线 → 2 个 subgraph
    expect(flowToMermaid(after).match(/subgraph /g)).toHaveLength(2);
  });
});
