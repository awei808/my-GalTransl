/**
 * 剧情路线图可视化编辑：mermaid ↔ flow 转换层往返测试。
 *
 * 验收口径（0.5.0 批次 5）：mermaid → flow → mermaid 必须等价。
 * 输入样例取自后端 Prompts.py:758-805 的提示词约定与输出示例，
 * 确保解析器面向真实产物而非臆想格式。
 */
import { describe, it, expect } from "vitest";
import { mermaidToFlow, applyFileRoutes } from "../lib/plotRoute/mermaidToFlow";
import {
  flowToMermaid,
  graphsEquivalent,
  safeSubgraphId,
} from "../lib/plotRoute/flowToMermaid";
import { layoutGraph } from "../lib/plotRoute/routeLayout";
import type { RouteGraph } from "../lib/plotRoute/types";

/** 后端提示词给出的示例（Prompts.py:789） */
const SAMPLE_BACKEND = [
  "flowchart TD",
  '  subgraph prologue["序章"]',
  '    A["00_01_アバンタイトル.txt.json"]',
  '    B["00_02_導入.txt.json"]',
  "  end",
  "  A --> B",
].join("\n");

describe("mermaidToFlow 基础解析", () => {
  it("解析后端示例：2 节点 1 边，方向 TD", () => {
    const { graph, error } = mermaidToFlow(SAMPLE_BACKEND);
    expect(error).toBe("");
    expect(graph).not.toBeNull();
    expect(graph!.direction).toBe("TD");
    expect(graph!.nodes.map((n) => n.id).sort()).toEqual(["A", "B"]);
    expect(graph!.edges).toEqual([
      { id: "e0", source: "A", target: "B" },
    ]);
  });

  it("节点显示文本 = 文件名（引号剥离）", () => {
    const { graph } = mermaidToFlow(SAMPLE_BACKEND);
    const a = graph!.nodes.find((n) => n.id === "A")!;
    expect(a.label).toBe("00_01_アバンタイトル.txt.json");
  });

  it("兼容无引号与 graph 开头两种写法", () => {
    const src = ["graph LR", "  A[plain.txt]", "  A --> B", "  B[label.txt]"].join("\n");
    const { graph, error } = mermaidToFlow(src);
    expect(error).toBe("");
    expect(graph!.direction).toBe("LR");
    expect(graph!.nodes.find((n) => n.id === "A")!.label).toBe("plain.txt");
    expect(graph!.nodes.find((n) => n.id === "B")!.label).toBe("label.txt");
  });

  it("边引用未声明节点时自动补节点，避免连线悬空", () => {
    const src = ["flowchart TD", "  A --> B"].join("\n");
    const { graph } = mermaidToFlow(src);
    expect(graph!.nodes.map((n) => n.id).sort()).toEqual(["A", "B"]);
    expect(graph!.edges).toHaveLength(1);
  });

  it("忽略 %% 注释与 style/classDef 行", () => {
    const src = [
      "flowchart TD",
      "  %% 这是注释",
      '  A["a.txt"]',
      "  classDef big fill:#f00",
      "  A --> A",
    ].join("\n");
    const { graph, error } = mermaidToFlow(src);
    expect(error).toBe("");
    expect(graph!.nodes).toHaveLength(1);
    expect(graph!.edges).toHaveLength(1);
  });

  it("空源码与非法开头返回 error 而非抛异常", () => {
    expect(mermaidToFlow("").error).not.toBe("");
    expect(mermaidToFlow("   \n  ").error).not.toBe("");
    expect(mermaidToFlow("sequenceDiagram\n A->>B: hi").error).toContain("flowchart");
  });

  it("有 header 但无节点时返回 error", () => {
    expect(mermaidToFlow("flowchart TD").error).toContain("节点");
  });
});

describe("flowToMermaid 序列化", () => {
  const graph: RouteGraph = {
    direction: "TD",
    nodes: [
      { id: "A", label: "00_01.txt.json", route: "序章", x: 0, y: 0 },
      { id: "B", label: "00_02.txt.json", route: "序章", x: 0, y: 1 },
      { id: "C", label: "01_01.txt.json", route: "华恋线", x: 1, y: 0 },
    ],
    edges: [
      { id: "e0", source: "A", target: "B" },
      { id: "e1", source: "B", target: "C" },
    ],
  };

  it("首行是 flowchart + 方向", () => {
    expect(flowToMermaid(graph).split("\n")[0]).toBe("flowchart TD");
  });

  it("同路线节点包在同一个 subgraph 内", () => {
    const out = flowToMermaid(graph);
    expect(out).toContain('subgraph ');
    expect(out).toContain('["序章"]');
    expect(out).toContain('["华恋线"]');
    expect(out.match(/subgraph /g)).toHaveLength(2);
    expect(out.match(/end/g)).toHaveLength(2);
  });

  it("节点显示文本双引号包裹", () => {
    const out = flowToMermaid(graph);
    expect(out).toContain('A["00_01.txt.json"]');
    expect(out).toContain('C["01_01.txt.json"]');
  });

  it("无路线的节点不加 subgraph 包裹", () => {
    const g: RouteGraph = {
      direction: "TD",
      nodes: [{ id: "A", label: "a.txt", route: "", x: 0, y: 0 }],
      edges: [],
    };
    const out = flowToMermaid(g);
    expect(out).not.toContain("subgraph");
    expect(out).toContain('A["a.txt"]');
  });
});

describe("safeSubgraphId", () => {
  it("中文路线名转纯 ASCII 下划线 id", () => {
    const id = safeSubgraphId("序章");
    expect(id).toMatch(/^[A-Za-z_][\w-]*$/);
  });

  it("含 · 与空格的路线名不产生非法字符", () => {
    for (const name of ["序章 · 前篇", "Route A", "TRUE END"]) {
      expect(safeSubgraphId(name)).toMatch(/^[A-Za-z_][\w-]*$/);
    }
  });

  it("英文名保持不变", () => {
    expect(safeSubgraphId("prologue")).toBe("prologue");
  });
});

describe("往返等价（mermaid → flow → mermaid）", () => {
  const cases: Array<[string, string]> = [
    ["后端示例", SAMPLE_BACKEND],
    [
      "多路线多分支",
      [
        "flowchart TD",
        '  subgraph p["序章"]',
        '    A["00_01.txt.json"]',
        "  end",
        '  subgraph k["华恋线"]',
        '    B["01_01.txt.json"]',
        '    C["01_02.txt.json"]',
        "  end",
        "  A --> B",
        "  B --> C",
      ].join("\n"),
    ],
    [
      "含回流边（有向有环图）",
      [
        "flowchart TD",
        '  A["a.txt.json"]',
        '  B["b.txt.json"]',
        "  A --> B",
        "  B --> A",
      ].join("\n"),
    ],
    ["单节点无连线", ["flowchart LR", '  A["only.txt"]'].join("\n")],
  ];

  for (const [name, src] of cases) {
    it(`${name}：往返后拓扑等价`, () => {
      const first = mermaidToFlow(src);
      expect(first.error).toBe("");
      const round = mermaidToFlow(flowToMermaid(first.graph!));
      expect(round.error).toBe("");
      expect(graphsEquivalent(first.graph!, round.graph!)).toBe(true);
    });
  }

  it("二次往返稳定（幂等）", () => {
    const g1 = mermaidToFlow(SAMPLE_BACKEND).graph!;
    const g2 = mermaidToFlow(flowToMermaid(g1)).graph!;
    const g3 = mermaidToFlow(flowToMermaid(g2)).graph!;
    expect(graphsEquivalent(g2, g3)).toBe(true);
    expect(flowToMermaid(g2)).toBe(flowToMermaid(g3));
  });

  it("applyFileRoutes 回填路线名后往返仍等价", () => {
    const src = [
      "flowchart TD",
      '  A["a.txt.json"]',
      '  B["b.txt.json"]',
      "  A --> B",
    ].join("\n");
    const g = applyFileRoutes(mermaidToFlow(src).graph!, {
      "a.txt.json": "序章",
      "b.txt.json": "华恋线",
    });
    const round = mermaidToFlow(flowToMermaid(g)).graph!;
    const withRoutes = applyFileRoutes(round, {
      "a.txt.json": "序章",
      "b.txt.json": "华恋线",
    });
    expect(graphsEquivalent(g, withRoutes)).toBe(true);
  });
});

describe("layoutGraph 布局", () => {
  const build = (): RouteGraph => ({
    direction: "TD",
    nodes: [
      { id: "A", label: "a", route: "", x: 0, y: 0 },
      { id: "B", label: "b", route: "", x: 0, y: 0 },
      { id: "C", label: "c", route: "", x: 0, y: 0 },
    ],
    edges: [
      { id: "e0", source: "A", target: "C" },
      { id: "e1", source: "B", target: "C" },
    ],
  });

  it("TD：A/B 同层（y 相同），C 在下一层（y 更大）", () => {
    const g = layoutGraph(build(), "TD");
    const byId = new Map(g.nodes.map((n) => [n.id, n]));
    expect(byId.get("A")!.y).toBe(byId.get("B")!.y);
    expect(byId.get("C")!.y).toBeGreaterThan(byId.get("A")!.y);
  });

  it("TD：同层节点 x 不同（不重叠）", () => {
    const g = layoutGraph(build(), "TD");
    const byId = new Map(g.nodes.map((n) => [n.id, n]));
    expect(byId.get("A")!.x).not.toBe(byId.get("B")!.x);
  });

  it("LR：层沿 x 推进", () => {
    const g = layoutGraph(build(), "LR");
    const byId = new Map(g.nodes.map((n) => [n.id, n]));
    expect(byId.get("C")!.x).toBeGreaterThan(byId.get("A")!.x);
    expect(byId.get("A")!.x).toBe(byId.get("B")!.x);
  });

  it("输出确定：同输入两次布局坐标完全一致", () => {
    const a = layoutGraph(build(), "TD");
    const b = layoutGraph(build(), "TD");
    expect(a.nodes.map((n) => [n.x, n.y])).toEqual(b.nodes.map((n) => [n.x, n.y]));
  });

  it("含环时不死循环，环内节点仍有坐标", () => {
    const g: RouteGraph = {
      direction: "TD",
      nodes: [
        { id: "A", label: "a", route: "", x: 0, y: 0 },
        { id: "B", label: "b", route: "", x: 0, y: 0 },
      ],
      edges: [
        { id: "e0", source: "A", target: "B" },
        { id: "e1", source: "B", target: "A" },
      ],
    };
    const out = layoutGraph(g, "TD");
    for (const n of out.nodes) {
      expect(Number.isFinite(n.x)).toBe(true);
      expect(Number.isFinite(n.y)).toBe(true);
    }
  });

  it("自环不参与分层", () => {
    const g: RouteGraph = {
      direction: "TD",
      nodes: [{ id: "A", label: "a", route: "", x: 0, y: 0 }],
      edges: [{ id: "e0", source: "A", target: "A" }],
    };
    expect(() => layoutGraph(g, "TD")).not.toThrow();
  });
});
