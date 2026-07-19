import { createSignal, For, Show, onMount } from "solid-js";
import { toast } from "../../stores/toastStore";
import { fetchPlugins } from "../../lib/api/general";
import type { PluginInfo } from "../../lib/api/types";

type FilterType = "all" | "file" | "text";

export function PluginsPage() {
  const [plugins, setPlugins] = createSignal<PluginInfo[]>([]);
  const [loading, setLoading] = createSignal(true);
  const [filter, setFilter] = createSignal<FilterType>("all");
  const [selectedPlugin, setSelectedPlugin] = createSignal<PluginInfo | null>(null);

  onMount(() => loadData());

  async function loadData() {
    setLoading(true);
    try {
      const list = await fetchPlugins();
      setPlugins(list);
    } catch {
      toast.error("加载插件列表失败");
    } finally {
      setLoading(false);
    }
  }

  const filtered = () => {
    const list = plugins();
    const f = filter();
    if (f === "all") return list;
    return list.filter((p) => p.type === f);
  };

  return (
    <div class="page page-plugins">
      <h2 class="page-title">插件管理</h2>
      <p class="page-description">查看已安装的翻译插件及其基本信息。</p>

      <div class="plugin-toolbar">
        <div class="plugin-filters">
          {(["all", "file", "text"] as const).map((f) => (
            <button
              class={`btn btn--sm ${filter() === f ? "btn--primary" : ""}`}
              onClick={() => setFilter(f)}
            >
              {f === "all" ? "全部" : f === "file" ? "文件级" : "文本级"}
            </button>
          ))}
        </div>
        <button class="btn btn--sm" onClick={loadData} disabled={loading()}>
          {loading() ? "刷新中…" : "刷新"}
        </button>
      </div>

      {/* 详情面板 */}
      <Show when={selectedPlugin()}>
        {(p) => (
          <div class="plugin-detail">
            <div class="plugin-detail-header">
              <h3>{p().display_name || p().name}</h3>
              <button class="btn btn--sm" onClick={() => setSelectedPlugin(null)}>关闭</button>
            </div>
            <div class="plugin-detail-body">
              <div class="plugin-detail-row"><span>名称</span><span>{p().name}</span></div>
              <div class="plugin-detail-row"><span>版本</span><span>{p().version}</span></div>
              <div class="plugin-detail-row"><span>作者</span><span>{p().author}</span></div>
              <div class="plugin-detail-row"><span>类型</span><span>{p().type}</span></div>
              <div class="plugin-detail-row"><span>模块</span><span style="font-family:var(--font-mono)">{p().module}</span></div>
              <div class="plugin-detail-desc">{p().description}</div>
            </div>
          </div>
        )}
      </Show>

      {/* 插件列表 */}
      <div class="plugin-grid">
        <Show when={!loading()} fallback={<p>加载中…</p>}>
          <Show when={filtered().length > 0} fallback={<p class="plugin-empty">无匹配插件</p>}>
            <For each={filtered()}>
              {(p) => (
                <div
                  class="plugin-card"
                  onClick={() => setSelectedPlugin(p)}
                >
                  <div class="plugin-card-name">{p.display_name || p.name}</div>
                  <div class="plugin-card-version">v{p.version}</div>
                  <div class="plugin-card-author">{p.author}</div>
                  <div class="plugin-card-desc">{p.description?.slice(0, 60)}</div>
                  <div class="plugin-card-type">{p.type === "file" ? "文件级" : "文本级"}</div>
                </div>
              )}
            </For>
          </Show>
        </Show>
      </div>
    </div>
  );
}
