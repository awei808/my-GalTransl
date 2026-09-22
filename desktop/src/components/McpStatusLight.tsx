/**
 * MCP 连接指示灯 — 绿 = 当前有外部 agent 连着；灰 = 无。
 *
 * 判定依据是后端读到的 MCP 心跳新鲜度（GET /api/mcp-status）：
 * MCP 服务由客户端按需拉起，前端只能靠心跳间接感知其存在。
 */
import { createSignal, onMount, onCleanup } from "solid-js";
import { fetchMcpStatus } from "../lib/api/mcp";

/** 心跳更新间隔 30s、过期阈值 90s，故 10s 轮询足以反映状态变化 */
const POLL_INTERVAL_MS = 10000;

export function McpStatusLight() {
  const [available, setAvailable] = createSignal(false);
  const [tools, setTools] = createSignal(0);

  async function refresh(): Promise<void> {
    try {
      const status = await fetchMcpStatus();
      setAvailable(Boolean(status.available));
      setTools(Number(status.tools) || 0);
    } catch {
      // 后端不可达或接口异常一律按「未连接」处理，不弹提示打扰用户
      setAvailable(false);
      setTools(0);
    }
  }

  let timer: number | undefined;

  onMount(() => {
    void refresh();
    timer = window.setInterval(() => void refresh(), POLL_INTERVAL_MS);
  });

  onCleanup(() => {
    if (timer !== undefined) window.clearInterval(timer);
  });

  const label = () =>
    available()
      ? `外部 Agent 已连接（MCP，${tools()} 个工具）`
      : "外部 Agent 未连接（MCP）";

  return (
    <div
      class={`mcp-status-light ${available() ? "online" : "offline"}`}
      title={label()}
      aria-label={label()}
      role="status"
    >
      <span class="mcp-status-dot" />
      <span class="mcp-status-text">MCP</span>
    </div>
  );
}
