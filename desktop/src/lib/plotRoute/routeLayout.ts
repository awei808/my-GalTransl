/**
 * RouteGraph 的自动布局。
 *
 * 用分层（拓扑序）布局而非力导向：后端产出的图是「一个文件一个节点」的
 * 剧情流程图，层次结构明确，分层布局更能反映剧情推进方向，且结果确定、
 * 可重复（同一输入必然得到同一坐标，便于测试与撤销）。
 *
 * 不做真正的图形布局算法（避免引入 dagre 等依赖）：
 * 按拓扑层给 y，按层内序号给 x，循环边不参与分层计算。
 */
import type { RouteEdge, RouteGraph, RouteNode } from "./types";

export const NODE_W = 220;
export const NODE_H = 52;
export const GAP_X = 40;
export const GAP_Y = 60;

/** Kahn 拓扑分层；含环时把剩余节点作为同一尾层附在后面 */
function topoLayers(nodes: RouteNode[], edges: RouteEdge[]): string[][] {
  const ids = nodes.map((n) => n.id);
  const indeg = new Map<string, number>(ids.map((id) => [id, 0]));
  const outgoing = new Map<string, string[]>(ids.map((id) => [id, []]));

  for (const e of edges) {
    // 自环与指向未知节点的边不参与分层
    if (e.source === e.target) continue;
    if (!indeg.has(e.source) || !indeg.has(e.target)) continue;
    indeg.set(e.target, (indeg.get(e.target) ?? 0) + 1);
    outgoing.get(e.source)!.push(e.target);
  }

  const layers: string[][] = [];
  const placed = new Set<string>();
  let frontier = ids.filter((id) => (indeg.get(id) ?? 0) === 0);

  while (frontier.length > 0) {
    layers.push(frontier);
    for (const id of frontier) placed.add(id);
    const next: string[] = [];
    for (const id of frontier) {
      for (const tgt of outgoing.get(id) ?? []) {
        indeg.set(tgt, (indeg.get(tgt) ?? 0) - 1);
        if ((indeg.get(tgt) ?? 0) <= 0 && !placed.has(tgt) && !next.includes(tgt)) {
          next.push(tgt);
        }
      }
    }
    frontier = next;
  }

  // 环中节点无法归层：整体作为最后一层（保持原顺序，避免随机）
  const leftover = ids.filter((id) => !placed.has(id));
  if (leftover.length > 0) layers.push(leftover);
  return layers;
}

/**
 * 就地写入节点坐标。
 *
 * Args:
 *   graph: 图模型（各节点 x/y 将被覆盖）
 *   direction: "TD"（默认，逐层向下）或 "LR"（逐层向右）
 *
 * Returns:
 *   同一 graph 引用
 */
export function layoutGraph(graph: RouteGraph, direction?: string): RouteGraph {
  const dir = (direction ?? graph.direction ?? "TD").toUpperCase();
  const horizontal = dir.startsWith("L") || dir.startsWith("R");
  const layers = topoLayers(graph.nodes, graph.edges);
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));

  const stepMain = horizontal ? NODE_W + GAP_X : NODE_H + GAP_Y; // 层间距方向
  const stepCross = horizontal ? NODE_H + GAP_Y : NODE_W + GAP_X; // 层内排列方向

  layers.forEach((layer, li) => {
    layer.forEach((id, ci) => {
      const node = byId.get(id);
      if (!node) return;
      if (horizontal) {
        node.x = li * stepMain;
        node.y = ci * stepCross;
      } else {
        node.x = ci * stepCross;
        node.y = li * stepMain;
      }
    });
  });
  return graph;
}
