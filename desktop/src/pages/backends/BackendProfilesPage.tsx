import { createSignal, For, Show, onMount } from "solid-js";
import { toast } from "../../stores/toastStore";
import { confirm } from "../../stores/confirmStore";
import {
  fetchBackendProfiles,
  createBackendProfile,
  updateBackendProfile,
  deleteBackendProfile,
  getDefaultBackendProfile,
  setDefaultBackendProfile,
} from "../../lib/api/preferences";
import { getErrorMessage } from "../../lib/errors";

interface TokenEntry {
  endpoint?: string;
  modelName?: string;
}
interface OpenAICompatConfig {
  tokens?: TokenEntry[];
}
interface SakuraConfig {
  endpoints?: string[];
}
interface ProfileEntry {
  name: string;
  config: Record<string, unknown>;
}

const EMPTY_CONFIG: Record<string, unknown> = {};

export function BackendProfilesPage() {
  const [profiles, setProfiles] = createSignal<ProfileEntry[]>([]);
  const [loading, setLoading] = createSignal(true);
  const [defaultName, setDefaultName] = createSignal(getDefaultBackendProfile());

  // 编辑器
  const [editName, setEditName] = createSignal("");
  const [editConfig, setEditConfig] = createSignal<Record<string, unknown>>(EMPTY_CONFIG);
  const [editMode, setEditMode] = createSignal(false);
  const [saving, setSaving] = createSignal(false);

  // 新建
  const [showNew, setShowNew] = createSignal(false);
  const [newName, setNewName] = createSignal("");
  const [newConfigText, setNewConfigText] = createSignal("");

  onMount(() => loadProfiles());

  async function loadProfiles() {
    setLoading(true);
    try {
      const data = await fetchBackendProfiles();
      const entries: ProfileEntry[] = Object.entries(data.profiles || {}).map(([name, config]) => ({
        name,
        config: config as Record<string, unknown>,
      }));
      setProfiles(entries);
    } catch {
      toast.error("加载后端配置失败");
    } finally {
      setLoading(false);
    }
  }

  function openEditor(name: string) {
    const p = profiles().find((x) => x.name === name);
    if (!p) return;
    setEditName(name);
    setEditConfig({ ...p.config });
    setEditMode(true);
  }

  async function handleSave() {
    setSaving(true);
    try {
      await updateBackendProfile(editName(), editConfig());
      toast.success("配置已更新");
      setEditMode(false);
      await loadProfiles();
    } catch (e) {
      toast.error(`保存失败: ${getErrorMessage(e)}`);
    } finally {
      setSaving(false);
    }
  }

  async function handleCreate() {
    const name = newName().trim();
    if (!name) return;
    setSaving(true);
    try {
      let config: Record<string, unknown>;
      try {
        config = newConfigText().trim() ? JSON.parse(newConfigText()) : {};
      } catch {
        toast.error("JSON 格式错误");
        setSaving(false);
        return;
      }
      await createBackendProfile(name, config);
      toast.success("配置已创建");
      setShowNew(false);
      setNewName("");
      setNewConfigText("");
      await loadProfiles();
    } catch (e) {
      toast.error(`创建失败: ${getErrorMessage(e)}`);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(name: string) {
    const result = await confirm.show({
      title: "删除配置",
      message: `确定删除后端配置「${name}」吗？`,
      tone: "danger",
    });
    if (!result.confirmed) return;
    try {
      await deleteBackendProfile(name);
      toast.success("配置已删除");
      if (defaultName() === name) {
        setDefaultBackendProfile("");
        setDefaultName("");
      }
      await loadProfiles();
    } catch (e) {
      toast.error(`删除失败: ${getErrorMessage(e)}`);
    }
  }

  function handleSetDefault(name: string) {
    setDefaultBackendProfile(name);
    setDefaultName(name);
    toast.success("已设为默认");
  }

  function configSummary(config: Record<string, unknown>): string {
    const openai = config["OpenAI-Compatible"] as OpenAICompatConfig | undefined;
    if (openai?.tokens && openai.tokens.length > 0) {
      const t = openai.tokens[0];
      return `${t.endpoint || "?"} / ${t.modelName || "?"}`;
    }
    const sakura = config["SakuraLLM"] as SakuraConfig | undefined;
    if (sakura?.endpoints && sakura.endpoints.length > 0) {
      return `Sakura: ${sakura.endpoints[0]}`;
    }
    return "未配置";
  }

  return (
    <div class="page page-backend-profiles">
      <h2 class="page-title">后端配置</h2>
      <p class="page-description">管理 API 地址、模型、代理等后端连接配置。</p>

      <div class="bp-toolbar">
        <button class="btn btn--sm" onClick={() => setShowNew(true)}>
          新建配置
        </button>
        <button class="btn btn--sm" onClick={loadProfiles} disabled={loading()}>
          {loading() ? "加载中…" : "刷新"}
        </button>
      </div>

      {/* 编辑面板 */}
      <Show when={editMode()}>
        <div class="bp-editor-panel">
          <div class="bp-editor-header">
            <h3>编辑: {editName()}</h3>
            <button class="btn btn--sm" onClick={() => setEditMode(false)}>
              关闭
            </button>
          </div>
          <div class="bp-editor-body">
            <p class="bp-json-hint">
              配置为 JSON 格式。OpenAI 兼容接口请在 <code>OpenAI-Compatible.tokens</code> 中填写
              <code>endpoint</code>（接口地址）、<code>modelName</code>（模型名）、
              <code>token</code>（密钥，注意字段名为 token 而非 apiKey）；Sakura 本地模型请在 <code>SakuraLLM.endpoints</code> 填写本地地址。
            </p>
            <div class="settings-field">
              <span class="settings-label">配置名称</span>
              <input class="field__input" value={editName()} disabled />
            </div>
            <div class="settings-field" style="align-items:flex-start; padding-top:8px">
              <span class="settings-label" style="margin-top:4px">
                配置 JSON
              </span>
              <textarea
                class="bp-json-editor"
                value={JSON.stringify(editConfig(), null, 2)}
                onInput={(e) => {
                  try {
                    setEditConfig(JSON.parse(e.currentTarget.value));
                  } catch {
                    /* invalid json, ignore */
                  }
                }}
                spellcheck={false}
              />
            </div>
          </div>
          <div class="bp-editor-footer">
            <button class="btn btn--sm btn--primary" onClick={handleSave} disabled={saving()}>
              {saving() ? "保存中…" : "保存"}
            </button>
          </div>
        </div>
      </Show>

      {/* 新建对话框 */}
      <Show when={showNew()}>
        <div class="bp-new-dialog">
          <div class="bp-new-header">
            <h3>新建配置</h3>
            <button class="btn btn--sm" onClick={() => setShowNew(false)}>
              取消
            </button>
          </div>
          <div class="bp-new-body">
            <p class="bp-json-hint">
              配置为 JSON 格式。OpenAI 兼容接口请在 <code>OpenAI-Compatible.tokens</code> 中填写
              <code>endpoint</code>（接口地址）、<code>modelName</code>（模型名）、
              <code>token</code>（密钥，注意字段名为 token 而非 apiKey）；Sakura 本地模型请在 <code>SakuraLLM.endpoints</code> 填写本地地址。
            </p>
            <div class="settings-field">
              <span class="settings-label">名称</span>
              <input
                class="field__input"
                placeholder="例如: GPT-4o"
                value={newName()}
                onInput={(e) => setNewName(e.currentTarget.value)}
              />
            </div>
            <div class="settings-field" style="align-items:flex-start; padding-top:8px">
              <span class="settings-label" style="margin-top:4px">
                JSON 配置
              </span>
              <textarea
                class="bp-json-editor"
                placeholder='{"OpenAI-Compatible":{"tokens":[{"endpoint":"https://...","modelName":"gpt-4o","token":"sk-..."}]}}'
                value={newConfigText()}
                onInput={(e) => setNewConfigText(e.currentTarget.value)}
                spellcheck={false}
              />
            </div>
          </div>
          <div class="bp-new-footer">
            <button
              class="btn btn--sm btn--primary"
              onClick={handleCreate}
              disabled={saving() || !newName().trim()}
            >
              {saving() ? "创建中…" : "创建"}
            </button>
          </div>
        </div>
      </Show>

      {/* 配置列表 */}
      <div class="bp-list">
        <Show when={!loading()} fallback={<p class="bp-empty">加载中…</p>}>
          <Show
            when={profiles().length > 0}
            fallback={<p class="bp-empty">暂无配置，请新建一个</p>}
          >
            <For each={profiles()}>
              {(p) => (
                <div class="bp-card">
                  <div class="bp-card-info">
                    <div class="bp-card-name">
                      {p.name}
                      {p.name === defaultName() && <span class="bp-default-badge">默认</span>}
                    </div>
                    <div class="bp-card-meta">{configSummary(p.config)}</div>
                  </div>
                  <div class="bp-card-actions">
                    <button class="btn btn--sm" onClick={() => openEditor(p.name)}>
                      编辑
                    </button>
                    <button
                      class="btn btn--sm"
                      onClick={() => handleSetDefault(p.name)}
                      disabled={p.name === defaultName()}
                    >
                      设为默认
                    </button>
                    <button class="btn btn--sm" onClick={() => handleDelete(p.name)}>
                      删除
                    </button>
                  </div>
                </div>
              )}
            </For>
          </Show>
        </Show>
      </div>
    </div>
  );
}
