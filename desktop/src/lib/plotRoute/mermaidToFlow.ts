/**
 * mermaid flowchart 源码 → RouteGraph。
 *
 * 只解析后端 ForPlotRouteMap 提示词约定的子集（grammar 见 Prompts.py:758-805）：
 *   flowchart TD
 *     subgraph prologue["序章"]
 *       A["00_01_アバンタイトル.txt.json"]
 *       B["00_02_導入.txt.json"]
 *     end
 *     A --> B
 *
 * 刻意不做完整 mermaid 解析：后端只生成这一种形态，宽进（接受多种写法）会比
 * 严出（拒绝一切非预期输入）更容易在往返转换中丢信息。
 * 解析失败一律返回 error 非空，由调用方降级为纯文本模式，不抛异常。
 */
import type { ConvertResult, RouteEdge, RouteGraph, RouteNode } from "./types";

const NODE_SHAPE_RE = /^([A-Za-z_][\w-]*)\s*\[\s*(?:"([^"]*)"|([^\]]*?))\s*\]/;
const EDGE_RE = /^([A-Za-z_][\w-]*)(?:\s*\[\s*(?:"([^"]*)"|([^\]]*?))\s*\])?\s*-{1,3}>\s*([A-Za-z_][\w-]*)/;
const SUBGRAPH_RE = /^subgraph\s+([^\s\[]+)\s*(?:\[\s*(?:"([^"]*)"|([^\]]*?))\s*\])?/;
const END_RE = /^end\b/;

/** 去掉行尾注释与首尾空白；返回空串表示该行可跳过 */
function cleanLine(raw: string): string {
  const noComment = raw.replace(/%%.*$/, "");
  return noComment.trim();
}

/** 解析节点 id 与显示文本；label 为空时回落为 id */
function parseNodeShape(text: string): { id: string; label: string } | null {
  const m = NODE_SHAPE_RE.exec(text);
  if (!m) return null;
  if (m[1].toLowerCase() === "subgraph") return null;
  const label = (m[2] ?? m[3] ?? "").trim();
  return { id: m[1], label: label || m[1] };
}

/**
 * 解析 mermaid flowchart 源码为 RouteGraph（不含路线归属）。
 *
 * 路线归属由「文件归属」映射单独反查（见 applyFileRoutes），
 * 因为 subgraph 显示名与 文件归属 的值在后端产物里同源，但前者可能缺失。
 *
 * Args:
 *   source: mermaid 源码（可能为多行；末尾换行可省略）
 *
 * Returns:
 *   ConvertResult；graph 为 null 时 error 说明原因
 */
export function mermaidToFlow(source: string): ConvertResult {
  const text = (source ?? "").replace(/\r\n?/g, "\n");
  if (!text.trim()) return { graph: null, error: "mermaid 源码为空" };

  const lines = text.split("\n").map(cleanLine);
  const headerIdx = lines.findIndex((l) => l.length > 0);

  // 首行必须是 flowchart / graph（与后端 _validate_mermaid 同口径）
  const head = (lines[headerIdx] ?? "").toLowerCase();
  if (!head.startsWith("flowchart") && !head.startsWith("graph")) {
    return { graph: null, error: "源码未以 flowchart/graph 开头" };
  }
  const dirMatch = /^(?:flowchart|graph)\s+([A-Za-z]{2})/.exec(lines[headerIdx]);
  const direction = dirMatch ? dirMatch[1].toUpperCase() : "TD";

  const nodes = new Map<string, RouteNode>();
  const edges: RouteEdge[] = [];
  let edgeSeq = 0;

  /** 按需建节点（边引用未声明的节点时也要补上，避免连线悬空） */
  const ensureNode = (id: string, label?: string): RouteNode => {
    let node = nodes.get(id);
    if (!node) {
      node = { id, label: label ?? id, route: "", x: 0, y: 0 };
      nodes.set(id, node);
    } else if (label && node.label === id) {
      node.label = label;
    }
    return node;
  };

  for (let i = headerIdx + 1; i < lines.length; i++) {
    const line = lines[i];
    if (!line) continue;

    if (END_RE.test(line)) continue;
    if (SUBGRAPH_RE.test(line)) continue;

    // 边：允许左右两侧内联节点定义（A["x"] --> B["y"]）
    const em = EDGE_RE.exec(line);
    if (em) {
      const leftLabel = (em[2] ?? em[3] ?? "").trim();
      ensureNode(em[1], leftLabel || undefined);
      // 右端剩余文本（含可能的 ["label"]）单独解析，避免依赖匹配长度计算
      const arrowEnd = line.indexOf(">", em.index);
      const rightText = arrowEnd >= 0 ? line.slice(arrowEnd + 1) : line;
      ensureNode(em[4], parseNodeShape(rightText)?.label);
      edges.push({ id: `e${edgeSeq++}`, source: em[1], target: em[4] });
      continue;
    }

    // 游离节点定义
    const shape = parseNodeShape(line);
    if (shape) ensureNode(shape.id, shape.label);
    // 其余行（classDef / style / linkStyle 等）刻意忽略：不影响拓扑
  }

  if (nodes.size === 0) {
    return { graph: null, error: "未从源码中解析出任何节点" };
  }
  return {
    graph: { direction, nodes: [...nodes.values()], edges },
    error: "",
  };
}

/**
 * 用「文件归属」映射回填节点的路线名（label → 路线名）。
 *
 * 之所以不靠 subgraph 结构推断：后端产物中「文件归属」是权威映射，
 * 而 subgraph 分组可能因模型输出偏差与实际归属不一致。
 *
 * Args:
 *   graph: 已解析的图（就地修改各节点 route 字段）
 *   fileRoutes: 文件名 → 路线名
 *
 * Returns:
 *   同一 graph 引用，便于链式调用
 */
export function applyFileRoutes(
  graph: RouteGraph,
  fileRoutes: Record<string, string>,
): RouteGraph {
  for (const node of graph.nodes) {
    const route = fileRoutes[node.label];
    if (route) node.route = route;
  }
  return graph;
}

export { parseNodeShape as _parseNodeShape, cleanLine as _cleanLine };
