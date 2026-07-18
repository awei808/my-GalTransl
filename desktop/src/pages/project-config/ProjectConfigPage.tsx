import { createSignal, createEffect, For, Show, onMount } from "solid-js";
import { appState } from "../../stores/appStore";
import { toast } from "../../stores/toastStore";
import { fetchProjectConfig, updateProjectConfig, fetchConfigSchema } from "../../lib/api/project";
import type { ProjectConfigResponse, ConfigSchemaResponse } from "../../lib/api/types";

export function ProjectConfigPage() {
  const [config, setConfig] = createSignal<Record<string, any>>({});
  const [schemaDesc, setSchemaDesc] = createSignal<Record<string, string>>({});
  const [loading, setLoading] = createSignal(true);
  const [saving, setSaving] = createSignal(false);

  const pid = () => appState.activeProjectId;

  onMount(() => loadData());

  async function loadData() {
    if (!pid()) { setLoading(false); return; }
    setLoading(true);
    try {
      const [cfg, sch] = await Promise.all([
        fetchProjectConfig(pid()!),
        fetchConfigSchema(pid()!).catch(() => ({ parameters: {} })),
      ]);
      setConfig({ ...cfg.config });
      setSchemaDesc((sch as any)?.parameters || {});
    } catch (e: any) {
      toast.error(`加载配置失败: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }

  /** 按 key 的前缀分组，如 "general.source_language" → 组 "general" */
  const groupedKeys = () => {
    const keys = Object.keys(config()).filter((k) => typeof config()[k] !== "object" || config()[k] === null);
    const groups = new Map<string, string[]>();
    for (const key of keys) {
      const dot = key.indexOf(".");
      const group = dot > 0 ? key.slice(0, dot) : "_root";
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group)!.push(key);
    }
    return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
  };

  function getFieldLabel(key: string): string {
    return key;
  }

  function getFieldDesc(key: string): string {
    return schemaDesc()[key] || "";
  }

  function getValue(key: string): any {
    const v = config()[key];
    return v !== undefined ? v : "";
  }

  function setValue(key: string, value: any) {
    setConfig((prev) => ({ ...prev, [key]: value }));
  }

  function inferType(key: string): "string" | "number" | "boolean" {
    const v = config()[key];
    if (typeof v === "boolean") return "boolean";
    if (typeof v === "number") return "number";
    return "string";
  }

  async function handleSave() {
    if (!pid()) return;
    setSaving(true);
    try {
      await updateProjectConfig(pid()!, { config: config(), config_file_name: "config.yaml" });
      toast.success("配置已保存");
    } catch (e: any) {
      toast.error(`保存失败: ${e.message}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div class="page page-project-config">
      <div class="pc-header">
        <div>
          <h2 class="page-title">项目设置</h2>
          <p class="page-description">编辑当前项目的 config.yaml 配置参数。</p>
        </div>
        <button class="btn btn--sm btn--primary" onClick={handleSave} disabled={saving() || loading()}>
          {saving() ? "保存中…" : "保存配置"}
        </button>
      </div>

      <Show when={!loading()} fallback={<p class="pc-status">加载中…</p>}>
        <Show when={pid() && groupedKeys().length > 0} fallback={
          <p class="pc-status">{!pid() ? "请先打开一个项目" : "暂无可编辑的配置参数"}</p>
        }>
          <div class="pc-field-list">
            <For each={groupedKeys()}>
              {([group, keys]) => (
                <div class="pc-group">
                  <h3 class="pc-group-title">{group}</h3>
                  <For each={keys}>
                    {(key) => {
                      const type = inferType(key);
                      const desc = getFieldDesc(key);
                      const val = getValue(key);
                      return (
                        <div class="pc-row">
                          <div class="pc-row-label">
                            <code class="pc-key">{getFieldLabel(key)}</code>
                            <Show when={desc}>
                              <p class="pc-desc">{desc}</p>
                            </Show>
                          </div>
                          <div class="pc-row-control">
                            <Show
                              when={type !== "boolean"}
                              fallback={
                                <label class="settings-toggle">
                                  <input
                                    type="checkbox"
                                    checked={!!val}
                                    onChange={(e) => setValue(key, e.currentTarget.checked)}
                                  />
                                  <span class="settings-toggle-knob" />
                                </label>
                              }
                            >
                              <input
                                class="field__input pc-input"
                                type={type === "number" ? "number" : "text"}
                                value={String(val ?? "")}
                                onInput={(e) => setValue(key, type === "number" ? Number(e.currentTarget.value) : e.currentTarget.value)}
                              />
                            </Show>
                          </div>
                        </div>
                      );
                    }}
                  </For>
                </div>
              )}
            </For>
          </div>
        </Show>
      </Show>
    </div>
  );
}
