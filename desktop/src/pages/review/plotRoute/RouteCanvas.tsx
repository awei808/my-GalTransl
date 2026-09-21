/**
 * 剧情路线图可视化编辑画布。
 *
 * 用 @dschz/solid-flow（React Flow 的 SolidJS 移植）提供拖拽/连线/框选，
 * 编辑结果经 flowToMermaid 回写 mermaid 源码——mermaid 始终是权威产物，
 * 画布只是它的一个可编辑视图。
 *
 * 依赖锁定 0.2.6：1.0.0-next 系列要求 solid-js ^2.0.0-rc，与本仓 1.9.x 不兼容。
 */
import { Show } from "solid-js";
import {
  SolidFlow,
  SolidFlowProvider,
  Background,
  Controls,
  MiniMap,
  Panel,
  Handle,
  Position,
  MarkerType,
  useSolidFlow,
  type Node,
  type Edge,
  type NodeProps,
  type OnEdgeConnect,
  type NodeTypes,
} from "@dschz/solid-flow";
import "@dschz/solid-flow/styles";
import type { RouteGraph, RouteNode } from "../../../lib/plotRoute/types";

/** solid-flow 的节点 data 载荷 */
interface RouteNodeData extends Record<string, unknown> {
  label: string;
  route: string;
  color: string;
}

type RouteFlowNode = Node<RouteNodeData>;

const ROUTE_COLORS = [
  "#636e72", "#fdcb6e", "#e17055", "#00cec9",
  "#6c5ce7", "#00b894", "#e84393",
];

/** 按路线名稳定取色（同一路线在同一次会话内颜色一致） */
export function routeColor(route: string, allRoutes: string[]): string {
  if (!route) return ROUTE_COLORS[0];
  const idx = allRoutes.indexOf(route);
  return ROUTE_COLORS[(idx < 0 ? 0 : idx) % ROUTE_COLORS.length];
}

/** 自定义节点：显示文件名 + 路线色边条，左右各一个连接柄 */
function RouteNodeView(props: NodeProps<RouteNodeData>) {
  return (
    <div
      class="plotroute-node"
      style={{ "border-left-color": props.data.color }}
      title={props.data.label}
    >
      <Handle type="target" position={Position.Left} />
      <div class="plotroute-node-label">{props.data.label}</div>
      <Show when={props.data.route}>
        <div class="plotroute-node-route" style={{ color: props.data.color }}>
          {props.data.route}
        </div>
      </Show>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const nodeTypes: NodeTypes = { route: RouteNodeView as NodeTypes[string] };

/** RouteGraph → solid-flow 节点列表 */
export function toFlowNodes(
  graph: RouteGraph,
  allRoutes: string[],
): RouteFlowNode[] {
  return graph.nodes.map((n) => ({
    id: n.id,
    type: "route",
    position: { x: n.x, y: n.y },
    data: { label: n.label, route: n.route, color: routeColor(n.route, allRoutes) },
  }));
}

/** RouteGraph → solid-flow 边列表 */
export function toFlowEdges(graph: RouteGraph): Edge[] {
  return graph.edges.map((e) => ({
    id: e.id,
    source: e.source,
    target: e.target,
    type: "smoothstep",
    markerEnd: { type: MarkerType.ArrowClosed },
  }));
}

/**
 * solid-flow 当前状态 → RouteGraph。
 *
 * 坐标取画布实时位置（用户拖拽后即新布局）；label/route 以画布 data 为准，
 * 缺失时回落 fallback（首次挂载尚未测量时可能出现）。
 */
export function fromFlow(
  nodes: RouteFlowNode[],
  edges: Edge[],
  fallback: RouteGraph,
): RouteGraph {
  const meta = new Map(fallback.nodes.map((n) => [n.id, n]));
  const nextNodes: RouteNode[] = nodes.map((n) => {
    const prev = meta.get(n.id);
    const data = (n.data ?? {}) as Partial<RouteNodeData>;
    return {
      id: n.id,
      label: data.label || prev?.label || n.id,
      route: typeof data.route === "string" ? data.route : (prev?.route ?? ""),
      x: n.position.x,
      y: n.position.y,
    };
  });
  const nextEdges = edges.map((e, i) => ({
    id: e.id || `e${i}`,
    source: e.source,
    target: e.target,
  }));
  return { direction: fallback.direction, nodes: nextNodes, edges: nextEdges };
}

/** 画布内部：需要 useSolidFlow 故必须位于 Provider 之下 */
function Canvas(props: {
  graph: RouteGraph;
  allRoutes: string[];
  onChange: (next: RouteGraph) => void;
  onSelectNode: (nodeId: string) => void;
}) {
  const flow = useSolidFlow<RouteFlowNode, Edge>();

  /** 把当前画布状态回写为 RouteGraph（拖拽/连线/删除后调用） */
  const emit = () => {
    props.onChange(fromFlow(flow.getNodes(), flow.getEdges(), props.graph));
  };

  const handleConnect: OnEdgeConnect = (conn) => {
    if (!conn.source || !conn.target || conn.source === conn.target) return;
    flow.addEdges({
      id: `e_${conn.source}_${conn.target}_${flow.getEdges().length}`,
      source: conn.source,
      target: conn.target,
      type: "smoothstep",
      markerEnd: { type: MarkerType.ArrowClosed },
    });
    emit();
  };

  return (
    <SolidFlow<RouteFlowNode, Edge>
      nodes={toFlowNodes(props.graph, props.allRoutes)}
      edges={toFlowEdges(props.graph)}
      nodeTypes={nodeTypes}
      onConnect={handleConnect}
      onNodeDragStop={(e) => {
        if (e.nodes.length > 0) emit();
      }}
      onNodesDelete={() => emit()}
      onEdgesDelete={() => emit()}
      onNodeClick={(e) => props.onSelectNode(e.node.id)}
      fitView
      minZoom={0.1}
      maxZoom={2}
      nodesConnectable
      elementsSelectable
      proOptions={{ hideAttribution: true }}
    >
      <Background />
      <Controls />
      <MiniMap pannable zoomable />
      <Panel position="top-left">
        <span class="plotroute-canvas-hint">
          拖拽节点改布局 · 拖连接柄建连线 · 选中后 Delete 删除
        </span>
      </Panel>
    </SolidFlow>
  );
}

/** 对外画布组件：Provider + Canvas */
export function RouteCanvas(props: {
  graph: RouteGraph;
  allRoutes: string[];
  onChange: (next: RouteGraph) => void;
  onSelectNode: (nodeId: string) => void;
}) {
  return (
    <SolidFlowProvider>
      <Canvas {...props} />
    </SolidFlowProvider>
  );
}
