/**
 * 真实产物回归：解析器必须能吃下项目实际生成的 PlotRouteMap.json。
 *
 * 样例取自测试项目 transl_cache/pass0_cache/PlotRouteMap.json（小粥3-全量）。
 * 真实产物比提示词示例更"脏"，暴露出三类示例未覆盖的写法：
 *   1. subgraph 无引号且 id 为中文（`subgraph 序章[序章]`）——与后端
 *      _validate_mermaid 的约束不符，说明历史产物未经该校验；
 *   2. subgraph 块与边交错出现（`end` 后紧跟边，再开新 subgraph）；
 *   3. 节点 id 为「字母+数字」多字符（KS1/KG1），label 含 `…`/`？`。
 * 解析器必须全兼容，否则用户打开旧产物会直接降级为纯文本。
 */
import { describe, it, expect } from "vitest";
import { mermaidToFlow, applyFileRoutes } from "../lib/plotRoute/mermaidToFlow";
import { flowToMermaid, graphsEquivalent } from "../lib/plotRoute/flowToMermaid";
import { layoutGraph } from "../lib/plotRoute/routeLayout";

/** 真实产物摘要（保留全部脏写法，节选自 小粥3-全量） */
const REAL_PRODUCT = [
  "flowchart TD",
  " subgraph 序章[序章]",
  ' A["00_01_アバンタイトル.txt.json"]',
  ' B["00_02_導入.txt.json"]',
  " end",
  " A --> B",
  "",
  " subgraph 共通线[共通线]",
  ' F["01_01_理想の二人.txt.json"]',
  ' M["01_08_ロールプレイ…？.txt.json"]',
  ' N["01_09_おかわり！.txt.json"]',
  " end",
  " B --> F",
  " F --> M",
  " M --> N",
  "",
  ' subgraph kar_succ["华恋·魅魔支线"]',
  ' KS1["01_pg11_kar_succ_01.txt.json"]',
  ' KS2["02_kar_succ01.txt.json"]',
  " end",
  " KS1 --> KS2",
  "",
  ' subgraph kar_god["华恋·女神支线"]',
  ' KG1["01_pg12_kar_god_02.txt.json"]',
  ' KG2["02_kar_god01.txt.json"]',
  " end",
  " KG1 --> KG2",
].join("\n");

const REAL_FILE_ROUTES: Record<string, string> = {
  "00_01_アバンタイトル.txt.json": "序章",
  "00_02_導入.txt.json": "序章",
  "01_01_理想の二人.txt.json": "共通线",
  "01_08_ロールプレイ…？.txt.json": "共通线",
  "01_09_おかわり！.txt.json": "共通线",
  "01_pg11_kar_succ_01.txt.json": "华恋·魅魔支线",
  "02_kar_succ01.txt.json": "华恋·魅魔支线",
  "01_pg12_kar_god_02.txt.json": "华恋·女神支线",
  "02_kar_god01.txt.json": "华恋·女神支线",
};

describe("真实产物解析（小粥3-全量 PlotRouteMap.json）", () => {
  it("无引号中文 subgraph id 不导致解析失败", () => {
    const { graph, error } = mermaidToFlow(REAL_PRODUCT);
    expect(error).toBe("");
    expect(graph).not.toBeNull();
    expect(graph!.nodes).toHaveLength(9);
    // A→B、B→F、F→M、M→N、KS1→KS2、KG1→KG2 共 6 条
    expect(graph!.edges).toHaveLength(6);
  });

  it("subgraph 与边交错出现仍正确归属节点", () => {
    const { graph } = mermaidToFlow(REAL_PRODUCT);
    const ids = graph!.nodes.map((n) => n.id).sort();
    expect(ids).toEqual([
      "A", "B", "F", "KG1", "KG2", "KS1", "KS2", "M", "N",
    ]);
  });

  it("多字符节点 id（KS1/KG1）正确解析", () => {
    const { graph } = mermaidToFlow(REAL_PRODUCT);
    const ks1 = graph!.nodes.find((n) => n.id === "KS1")!;
    expect(ks1.label).toBe("01_pg11_kar_succ_01.txt.json");
  });

  it("label 含 … 与 ？ 时不被截断", () => {
    const { graph } = mermaidToFlow(REAL_PRODUCT);
    expect(graph!.nodes.find((n) => n.id === "M")!.label)
      .toBe("01_08_ロールプレイ…？.txt.json");
    expect(graph!.nodes.find((n) => n.id === "N")!.label)
      .toBe("01_09_おかわり！.txt.json");
  });

  it("applyFileRoutes 回填后往返等价", () => {
    const g1 = applyFileRoutes(mermaidToFlow(REAL_PRODUCT).graph!, REAL_FILE_ROUTES);
    const g2 = applyFileRoutes(mermaidToFlow(flowToMermaid(g1)).graph!, REAL_FILE_ROUTES);
    expect(graphsEquivalent(g1, g2)).toBe(true);
  });

  it("回填后所有节点都有路线名", () => {
    const g = applyFileRoutes(mermaidToFlow(REAL_PRODUCT).graph!, REAL_FILE_ROUTES);
    for (const n of g.nodes) {
      expect(n.route).not.toBe("");
    }
  });

  it("布局不抛异常且坐标有限", () => {
    const g = layoutGraph(
      applyFileRoutes(mermaidToFlow(REAL_PRODUCT).graph!, REAL_FILE_ROUTES),
      "TD",
    );
    for (const n of g.nodes) {
      expect(Number.isFinite(n.x) && Number.isFinite(n.y)).toBe(true);
    }
  });

  it("重新序列化后 subgraph id 全部合法（修复脏产物）", () => {
    const g = applyFileRoutes(mermaidToFlow(REAL_PRODUCT).graph!, REAL_FILE_ROUTES);
    const out = flowToMermaid(g);
    for (const line of out.split("\n")) {
      const m = /^\s*subgraph\s+([^\s\[]+)/.exec(line);
      if (m) {
        expect(m[1]).toMatch(/^[A-Za-z_][\w-]*$/);
      }
    }
  });
});
