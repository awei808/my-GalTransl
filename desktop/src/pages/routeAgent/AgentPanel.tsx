import { createEffect, createSignal, For, on, Show } from "solid-js";
import type { AgentChatStep } from "../../lib/api/types";
import { sendAgentChat } from "../../lib/api/project";
import { getErrorMessage } from "../../lib/errors";
import { toast } from "../../stores/toastStore";

interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  steps?: AgentChatStep[];
}

const TOOL_LABELS: Record<string, string> = {
  read_route_map: "读取路线图",
  write_route_map: "修改路线图",
  search_file_metadata: "查找文件元数据",
};

/**
 * 路线图工作台的简易 agent 对话面板：AI 只有 3 个工具（读/写路线图、查文件元数据），
 * 后端同步执行工具循环后一次性返回回复与工具步骤。
 */
export function AgentPanel(props: {
  projectId: string;
  onRouteMapChanged: () => void;
}) {
  const [messages, setMessages] = createSignal<ChatMessage[]>([]);
  const [input, setInput] = createSignal("");
  const [pending, setPending] = createSignal(false);

  // 切项目时清空聊天记录，避免上一个项目的对话残留显示
  createEffect(
    on(
      () => props.projectId,
      () => {
        setMessages([]);
        setPending(false);
      },
    ),
  );

  async function handleSend() {
    const text = input().trim();
    if (!text || pending() || !props.projectId) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text }]);
    setPending(true);
    try {
      const res = await sendAgentChat(props.projectId, { message: text });
      setMessages((m) => [...m, { role: "assistant", text: res.reply, steps: res.steps }]);
      // AI 可能改写了路线图：通知父组件刷新渲染
      if (res.steps.some((s) => s.tool === "write_route_map" && s.ok)) {
        props.onRouteMapChanged();
      }
    } catch (e) {
      toast.error(`Agent 调用失败: ${getErrorMessage(e)}`);
      setMessages((m) => [...m, { role: "assistant", text: "（调用失败，请重试或检查后端配置）" }]);
      // 失败分支也刷新一次：前端超时中断时后端 write_route_map 副作用仍可能已发生
      props.onRouteMapChanged();
    } finally {
      setPending(false);
    }
  }

  return (
    <div class="agent-panel">
      <div class="agent-messages">
        <Show when={messages().length === 0}>
          <div class="agent-empty">
            用自然语言让 Agent 查看或修改路线图，例如「把共通线拆成两条线」「哪些文件涉及琴美？」。
          </div>
        </Show>
        <For each={messages()}>
          {(msg) => (
            <div class={`agent-msg agent-msg--${msg.role}`}>
              <div class="agent-msg-text">{msg.text}</div>
              <Show when={msg.steps && msg.steps.length > 0}>
                <div class="agent-msg-steps">
                  <For each={msg.steps}>
                    {(step) => (
                      <span class="agent-step" classList={{ "agent-step--fail": !step.ok }}>
                        {TOOL_LABELS[step.tool] ?? step.tool}
                        {!step.ok ? " ✗" : ""}
                      </span>
                    )}
                  </For>
                </div>
              </Show>
            </div>
          )}
        </For>
        <Show when={pending()}>
          <div class="agent-msg agent-msg--assistant agent-msg--pending">Agent 思考中…</div>
        </Show>
      </div>
      <div class="agent-input-row">
        <textarea
          class="agent-input"
          placeholder="描述要如何修改路线图，或询问文件元数据…"
          value={input()}
          disabled={pending()}
          onInput={(e) => setInput(e.currentTarget.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
              e.preventDefault();
              void handleSend();
            }
          }}
        />
        <button type="button" class="agent-send" disabled={pending() || !input().trim()} onClick={() => void handleSend()}>
          发送
        </button>
      </div>
    </div>
  );
}
