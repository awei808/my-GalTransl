/**
 * 剧情路线图可视化编辑的公共类型。
 *
 * 数据契约对齐后端 GalTransl/Backend/ForPlotRouteMap.py 产物 PlotRouteMap.json：
 *   { 结构类型, 用户大纲, mermaid, 文件归属: {文件名: 路线名}, 节点剧情: {路线名: 摘要} }
 *
 * `mermaid` 字段始终是**权威产物**（后端 context.py 注入翻译提示词时直接读它），
 * 可视化编辑仅在它之上做双向转换，不引入第二份真源。
 */

/** 图节点：一个节点 = 一个剧本文件（与后端提示词约定一致） */
export interface RouteNode {
  /** mermaid 节点 id（英文别名，如 R1 / K1） */
  id: string;
  /** 节点显示文本 = 剧本完整文件名（如 00_01_アバンタイトル.txt.json） */
  label: string;
  /** 所属路线名（来自 文件归属 反查，或 subgraph 归属） */
  route: string;
  /** 画布坐标 */
  x: number;
  y: number;
}

/** 图连线：A --> B（暂不支持带标签的边，后端提示词未产出） */
export interface RouteEdge {
  id: string;
  source: string;
  target: string;
}

/** 可视化图模型（mermaid 与画布之间的中间表示） */
export interface RouteGraph {
  /** 图方向：TD / LR 等；解析失败时回落 TD */
  direction: string;
  nodes: RouteNode[];
  edges: RouteEdge[];
}

/** 转换结果：失败时 error 非空，调用方应降级为纯文本模式 */
export interface ConvertResult {
  graph: RouteGraph | null;
  error: string;
}
