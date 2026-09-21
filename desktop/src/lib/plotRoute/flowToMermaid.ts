/**
 * RouteGraph → mermaid flowchart 源码。
 *
 * 与 mermaidToFlow 严格互逆，往返转换必须等价（round-trip 测试锁定）。
 * 输出形态对齐后端 _validate_mermaid 的校验口径：
 *   - 首行 `flowchart <方向>`
 *   - subgraph id 仅用字母/数字/下划线/连字符（中文路线名只作显示名）
 *   - 节点显示文本一律双引号包裹
 */
import type { RouteEdge, RouteGraph, RouteNode } from "./types";

/** 转义双引号，避免显示文本提前闭合（mermaid 无标准转义，用 HTML 实体） */
function quote(text: string): string {
  return `"${String(text ?? "").replace(/"/g, "&quot;")}"`;
}

/**
 * subgraph id 合法化：非 [A-Za-z0-9_-] 一律换成下划线，并保证以字母开头。
 *
 * 后端 _validate_mermaid 会拒绝含 `·`/空白等字符的 subgraph id，
 * 故此处必须产出安全 id；路线名本身只作显示名放方括号内。
 */
export function safeSubgraphId(routeName: string): string {
  const ascii = routeName.replace(/[^A-Za-z0-9_-]/g, "_");
  return /^[A-Za-z_]/.test(ascii) ? ascii : `r_${ascii}`;
}

/** 稳定排序：同路线节点聚在一起，路线按首次出现顺序，保证往返可比对 */
function groupByRoute(nodes: RouteNode[]): Array<[string, RouteNode[]]> {
  const order: string[] = [];
  const map = new Map<string, RouteNode[]>();
  for (const node of nodes) {
    const route = node.route || "";
    if (!map.has(route)) {
      map.set(route, []);
      order.push(route);
    }
    map.get(route)!.push(node);
  }
  return order.map((r) => [r, map.get(r)!]);
}

/**
 * 把图模型序列化为 mermaid flowchart 源码。
 *
 * Args:
 *   graph: 图模型（节点需含 id/label/route；x/y 不参与序列化）
 *
 * Returns:
 *   mermaid 源码（多行，末尾无换行）
 */
export function flowToMermaid(graph: RouteGraph): string {
  const direction = graph.direction || "TD";
  const lines: string[] = [`flowchart ${direction}`];

  for (const [route, nodes] of groupByRoute(graph.nodes)) {
    if (route) {
      lines.push(`  subgraph ${safeSubgraphId(route)}[${quote(route)}]`);
    }
    for (const node of nodes) {
      lines.push(`    ${node.id}[${quote(node.label)}]`);
    }
    if (route) lines.push("  end");
  }

  if (graph.edges.length > 0) {
    lines.push("");
    for (const edge of graph.edges) {
      lines.push(`  ${edge.source} --> ${edge.target}`);
    }
  }
  return lines.join("\n");
}

/**
 * 计算两图是否拓扑等价（节点集合 + 边集合相同，忽略坐标）。
 *
 * 往返测试用：mermaid → flow → mermaid 后重新解析，应与首轮解析结果等价。
 */
export function graphsEquivalent(a: RouteGraph, b: RouteGraph): boolean {
  const key = (g: RouteGraph): string => {
    const nodes = g.nodes
      .map((n) => `${n.id}\u0000${n.label}\u0000${n.route}`)
      .sort()
      .join("\u0001");
    const edges = g.edges
      .map((e) => `${e.source}\u0000${e.target}`)
      .sort()
      .join("\u0001");
    return `${nodes}\u0002${edges}`;
  };
  return key(a) === key(b);
}

export type { RouteEdge, RouteNode };
