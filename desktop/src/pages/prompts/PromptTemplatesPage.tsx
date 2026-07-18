import { createSignal, createEffect, For, Show, onMount } from "solid-js";
import { toast } from "../../stores/toastStore";
import { fetchPromptTemplates } from "../../lib/api/general";
import type { PromptTemplateInfo } from "../../lib/api/types";
import {
  getPromptTemplateOverride,
  setPromptTemplateOverride,
  deletePromptTemplateOverride,
} from "../../lib/api/preferences";

export function PromptTemplatesPage() {
  const [templates, setTemplates] = createSignal<PromptTemplateInfo[]>([]);
  const [loading, setLoading] = createSignal(true);
  const [selected, setSelected] = createSignal<PromptTemplateInfo | null>(null);
  const [systemPrompt, setSystemPrompt] = createSignal("");
  const [userPrompt, setUserPrompt] = createSignal("");
  const [overridden, setOverridden] = createSignal(false);
  const [saving, setSaving] = createSignal(false);

  onMount(() => loadTemplates());

  async function loadTemplates() {
    setLoading(true);
    try {
      const res = await fetchPromptTemplates();
      setTemplates(res.templates ?? []);
      if (res.templates?.length > 0) {
        selectTemplate(res.templates[0]);
      }
    } catch {
      toast.error("加载提示词模板失败");
    } finally {
      setLoading(false);
    }
  }

  function selectTemplate(t: PromptTemplateInfo) {
    setSelected(t);
    const ov = getPromptTemplateOverride(t.name);
    setSystemPrompt(ov?.system_prompt ?? t.system_prompt);
    setUserPrompt(ov?.user_prompt ?? t.user_prompt);
    setOverridden(t.overridden || !!ov);
  }

  async function handleSave() {
    const t = selected();
    if (!t) return;
    setSaving(true);
    try {
      setPromptTemplateOverride(t.name, {
        system_prompt: systemPrompt(),
        user_prompt: userPrompt(),
      });
      setOverridden(true);
      toast.success("提示词已保存");
    } catch (e: any) {
      toast.error(`保存失败: ${e.message}`);
    } finally {
      setSaving(false);
    }
  }

  function handleReset() {
    const t = selected();
    if (!t) return;
    deletePromptTemplateOverride(t.name);
    setSystemPrompt(t.system_prompt);
    setUserPrompt(t.user_prompt);
    setOverridden(false);
    toast.success("已重置为默认值");
  }

  return (
    <div class="page page-prompt-templates">
      <h2 class="page-title">提示词模板</h2>
      <p class="page-description">管理各翻译模板的默认提示词，可分别编辑并一键重置为内置值。</p>

      <Show when={!loading()} fallback={<p>加载中…</p>}>
        <div class="pt-layout">
          {/* 左侧模板列表 */}
          <div class="pt-sidebar">
            <div class="pt-sidebar-title">翻译模板</div>
            <For each={templates()}>
              {(t) => (
                <div
                  class={`pt-sidebar-item ${selected()?.name === t.name ? "active" : ""}`}
                  onClick={() => selectTemplate(t)}
                >
                  <div class="pt-sidebar-name">{t.name}</div>
                  <div class="pt-sidebar-desc">{t.description}</div>
                  <Show when={t.overridden}>
                    <span class="pt-override-badge">已修改</span>
                  </Show>
                </div>
              )}
            </For>
          </div>

          {/* 右侧编辑器 */}
          <div class="pt-editor">
            <Show when={selected()} fallback={<p class="pt-empty">选择一个模板</p>}>
              <div class="pt-editor-header">
                <span class="pt-editor-title">{selected()!.name}</span>
                <div class="pt-editor-actions">
                  <Show when={overridden()}>
                    <span class="pt-modified-indicator">● 已修改</span>
                  </Show>
                  <button class="btn btn--sm" onClick={handleReset} disabled={!overridden()}>
                    重置为默认
                  </button>
                  <button
                    class="btn btn--sm btn--primary"
                    onClick={handleSave}
                    disabled={saving()}
                  >
                    {saving() ? "保存中…" : "保存"}
                  </button>
                </div>
              </div>

              <div class="pt-field">
                <label class="pt-label">System Prompt（系统提示词）</label>
                <textarea
                  class="pt-textarea"
                  value={systemPrompt()}
                  onInput={(e) => setSystemPrompt(e.currentTarget.value)}
                  spellcheck={false}
                />
              </div>
              <div class="pt-field">
                <label class="pt-label">User Prompt（用户提示词）</label>
                <textarea
                  class="pt-textarea"
                  value={userPrompt()}
                  onInput={(e) => setUserPrompt(e.currentTarget.value)}
                  spellcheck={false}
                />
              </div>
            </Show>
          </div>
        </div>
      </Show>
    </div>
  );
}
