import { createSignal, For, Show, onMount } from "solid-js";
import { fetchAppSettings, updateAppSettings, fetchMcpTools } from "../../lib/api/general";
import type { AppSettings, McpToolInfo } from "../../lib/api/types";
import { getErrorMessage } from "../../lib/errors";

export function McpSettingsPage() {
  // ── 状态 ──（后端全局 app_settings.json；MCP 独立进程实时读同一份文件）
  const [mcpHGate, setMcpHGate] = createSignal(true);
  const [mcpDisabledTools, setMcpDisabledTools] = createSignal<string[]>([]);
  const [mcpTools, setMcpTools] = createSignal<McpToolInfo[]>([]);
  const [mcpToolsLoading, setMcpToolsLoading] = createSignal(false);
  const [mcpSaving, setMcpSaving] = createSignal(false);
  const [mcpError, setMcpError] = createSignal("");

  onMount(() => {
    fetchAppSettings()
      .then((s) => {
        setMcpHGate(s.mcpHGateEnabled ?? true);
        setMcpDisabledTools(s.mcpDisabledTools ?? []);
      })
      .catch(() => {});

    // 工具清单（后端为唯一真源），供工具开关列表渲染
    setMcpToolsLoading(true);
    fetchMcpTools()
      .then((tools) => setMcpTools(tools))
      .catch(() => {})
      .finally(() => setMcpToolsLoading(false));
  });

  // ── 保存：本地先行（乐观更新）+ 串行化排队写后端 ──
  // 连续快速切换开关时，read-modify-write 若并发会互相覆盖，故排队逐次提交；
  // 每次提交携带点击时刻的完整状态，最终一次 PUT 总是收敛到最新值。
  let saveChain: Promise<unknown> = Promise.resolve();
  let savePending = 0;

  function queueSave(patch: Partial<AppSettings>) {
    savePending += 1;
    setMcpSaving(true);
    saveChain = saveChain
      .then(async () => {
        const cur = await fetchAppSettings();
        await updateAppSettings({ ...cur, ...patch });
        setMcpError("");
      })
      .catch((e: Error) => {
        setMcpError(`保存 MCP 设置失败：${getErrorMessage(e)}`);
        // 仅最后一笔失败时回读服务端纠偏，避免回弹用户在失败后刚点的状态
        if (savePending === 1) {
          return fetchAppSettings()
            .then((s) => {
              setMcpHGate(s.mcpHGateEnabled ?? true);
              setMcpDisabledTools(s.mcpDisabledTools ?? []);
            })
            .catch(() => {});
        }
      })
      .finally(() => {
        savePending -= 1;
        if (savePending <= 0) setMcpSaving(false);
      });
  }

  function applyMcpHGate(enabled: boolean) {
    setMcpHGate(enabled);
    queueSave({ mcpHGateEnabled: enabled });
  }

  function toggleMcpTool(name: string, enabled: boolean) {
    const next = enabled
      ? mcpDisabledTools().filter((n) => n !== name)
      : [...mcpDisabledTools(), name];
    setMcpDisabledTools(next);
    queueSave({ mcpDisabledTools: next });
  }

  function enableAllMcpTools() {
    setMcpDisabledTools([]);
    queueSave({ mcpDisabledTools: [] });
  }

  return (
    <div class="page page-mcp-settings">
      <h2 class="page-title">MCP 服务与门禁</h2>
      <p class="page-description">
        控制外部 agent（dsh 等 MCP 客户端）可用的工具与 H 门禁。设置即时生效，无需重启后端。
      </p>

      <div class="settings-content">
        {/* ── H 门禁 ── */}
        <section class="settings-section">
          <div class="settings-section-header">
            <h3>H 门禁</h3>
            <p>MCP 写工具在落盘前对 H 内容的硬拦截开关。</p>
          </div>

          <div class="settings-field settings-field--column" style="border-bottom:none">
            <span class="settings-label">
              MCP H 门禁
              <span class="settings-hint-inline">（拦截外部 agent 经 MCP 写入 H 内容）</span>
            </span>
            <label class="settings-toggle">
              <input
                type="checkbox"
                checked={mcpHGate()}
                disabled={mcpSaving()}
                onChange={(e) => applyMcpHGate(e.currentTarget.checked)}
              />
              <span class="settings-toggle-knob" />
            </label>
            <p class="settings-hint">
              关闭后，外部 agent 经 MCP 写入路线图/元数据时不再拦截 H 内容；不影响翻译流程自身的 H 词过滤。
              服务说明文本在 MCP 会话建立时下发，切换后建议重开 agent 会话。
            </p>
          </div>
        </section>

        {/* ── 工具开关 ── */}
        <section class="settings-section">
          <div class="settings-section-header">
            <h3>MCP 工具开关</h3>
            <p>逐个启用/禁用外部 agent 可调用的工具；保存后下一次 MCP 调用即生效。</p>
          </div>

          <div class="settings-field settings-field--column" style="border-bottom:none">
            <Show when={mcpError()}>
              <div class="settings-error">{mcpError()}</div>
            </Show>
            <Show
              when={mcpTools().length > 0}
              fallback={
                <p class="settings-hint">
                  {mcpToolsLoading() ? "工具清单加载中…" : "工具清单加载失败，请确认后端已运行。"}
                </p>
              }
            >
              <div class="mcp-tool-toolbar">
                <button
                  class="btn btn--sm"
                  onClick={enableAllMcpTools}
                  disabled={mcpSaving() || mcpDisabledTools().length === 0}
                >
                  全部启用
                </button>
                <span class="settings-hint">
                  已启用 {mcpTools().filter((t) => !mcpDisabledTools().includes(t.name)).length}/
                  {mcpTools().length}
                </span>
              </div>
              <For each={[{ kind: "read" as const, title: "只读检索" }, { kind: "write" as const, title: "写入" }]}>
                {(group) => (
                  <div class="mcp-tool-group">
                    <div class="mcp-tool-group-title">
                      {group.title}（{mcpTools().filter((t) => t.kind === group.kind).length} 个）
                    </div>
                    <For each={mcpTools().filter((t) => t.kind === group.kind)}>
                      {(tool) => (
                        <div class="mcp-tool-row">
                          <div class="mcp-tool-text">
                            <span class="mcp-tool-name">{tool.name}</span>
                            <span class="mcp-tool-desc" title={tool.description}>
                              {tool.description}
                            </span>
                          </div>
                          <label class="settings-toggle">
                            <input
                              type="checkbox"
                              checked={!mcpDisabledTools().includes(tool.name)}
                              disabled={mcpSaving()}
                              onChange={(e) => toggleMcpTool(tool.name, e.currentTarget.checked)}
                            />
                            <span class="settings-toggle-knob" />
                          </label>
                        </div>
                      )}
                    </For>
                  </div>
                )}
              </For>
              <p class="settings-hint">
                禁用后的工具不会出现在 agent 的工具清单中，直接调用会被拒绝；agent 会话的工具清单与说明文本在会话建立时缓存，建议重开 agent 会话。
              </p>
            </Show>
          </div>
        </section>
      </div>
    </div>
  );
}
