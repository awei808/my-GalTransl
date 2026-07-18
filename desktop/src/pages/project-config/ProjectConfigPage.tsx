import { createSignal, createEffect, For, Show, onMount } from "solid-js";
import { appState, navigateTo } from "../../stores/appStore";
import { toast } from "../../stores/toastStore";
import { fetchProjectConfig, updateProjectConfig, fetchConfigSchema } from "../../lib/api/project";
import type { ProjectConfigResponse, ConfigSchemaResponse } from "../../lib/api/types";

export function ProjectConfigPage() {
  const [config, setConfig] = createSignal<Record<string, any>>({});
  const [schema, setSchema] = createSignal<ConfigSchemaResponse["schema"]>({});
  const [loading, setLoading] = createSignal(true);
  const [saving, setSaving] = createSignal(false);
  const [activeSection, setActiveSection] = createSignal<string | null>(null);

  const pid = () => appState.activeProjectId;

  onMount(() => loadData());

  async function loadData() {
    if (!pid()) return;
    setLoading(true);
    try {
      const [cfg, sch] = await Promise.all([
        fetchProjectConfig(pid()!),
        fetchConfigSchema(pid()!).catch(() => ({ schema: {} })),
      ]);
      setConfig(cfg.config || {});
      setSchema((sch as any)?.schema || {});
      // 自动选中第一个 section
      const keys = Object.keys((sch as any)?.schema || {});
      if (keys.length > 0) setActiveSection(keys[0]);
    } catch (e: any) {
      toast.error(`加载配置失败: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }

  function getSectionKeys(): string[] {
    return Object.keys(schema());
  }

  function getSectionFields(section: string): string[] {
    const sec = (schema() as any)[section];
    if (!sec || !sec.fields) return [];
    return Object.keys(sec.fields);
  }

  function getFieldMeta(section: string, field: string): any {
    const sec = (schema() as any)[section];
    return sec?.fields?.[field] || {};
  }

  function getFieldValue(section: string, field: string): any {
    const key = `${section}.${field}`;
    const val = (config() as any)[key];
    return val !== undefined ? val : "";
  }

  function setFieldValue(section: string, field: string, value: any) {
    setConfig((prev) => {
      const next = { ...prev };
      const key = `${section}.${field}`;
      next[key] = value;
      return next;
    });
  }

  async function handleSave() {
    if (!pid()) return;
    setSaving(true);
    try {
      await updateProjectConfig(pid()!, { config: config() });
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
          {saving() ? "保存中…" : "保存"}
        </button>
      </div>

      <Show when={!loading()} fallback={<p style="padding: var(--space-3);color: var(--color-text-tertiary)">加载中…</p>}>
        <Show when={pid() && getSectionKeys().length > 0} fallback={
          <p style="padding: var(--space-3);color: var(--color-text-tertiary)">
            {!pid() ? "请先打开一个项目" : "暂无可编辑的配置"}
          </p>
        }>
          <div class="pc-layout">
            {/* 侧边导航 */}
            <div class="pc-nav">
              <For each={getSectionKeys()}>
                {(s) => (
                  <div
                    class={`pc-nav-item ${activeSection() === s ? "active" : ""}`}
                    onClick={() => setActiveSection(s)}
                  >
                    {s}
                  </div>
                )}
              </For>
            </div>

            {/* 字段编辑区 */}
            <div class="pc-fields">
              <Show when={activeSection()}>
                {(section) => (
                  <>
                    <h3 class="pc-section-title">{section()}</h3>
                    <For each={getSectionFields(section())}>
                      {(field) => {
                        const meta = getFieldMeta(section(), field);
                        const val = getFieldValue(section(), field);
                        const desc = meta.description || "";
                        const type = meta.type || "string";
                        return (
                          <div class="pc-field">
                            <label class="pc-field-label">{field}</label>
                            <Show when={desc}>
                              <p class="pc-field-desc">{desc}</p>
                            </Show>
                            <Show
                              when={type === "boolean"}
                              fallback={
                                <input
                                  class="field__input pc-field-input"
                                  type={type === "number" ? "number" : "text"}
                                  value={String(val ?? "")}
                                  onInput={(e) => setFieldValue(section(), field, type === "number" ? Number(e.currentTarget.value) : e.currentTarget.value)}
                                />
                              }
                            >
                              <label class="settings-toggle">
                                <input
                                  type="checkbox"
                                  checked={!!val}
                                  onChange={(e) => setFieldValue(section(), field, e.currentTarget.checked)}
                                />
                                <span class="settings-toggle-knob" />
                              </label>
                            </Show>
                          </div>
                        );
                      }}
                    </For>
                  </>
                )}
              </Show>
            </div>
          </div>
        </Show>
      </Show>
    </div>
  );
}
