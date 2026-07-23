import { createSignal, For, Show, Index, onMount } from "solid-js";
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
import { fetchOpenAIModels } from "../../lib/api/general";
import { getErrorMessage } from "../../lib/errors";

interface TokenEntry {
  endpoint?: string;
  modelName?: string;
  token?: string;
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

type ProfileType = "OpenAI-Compatible" | "SakuraLLM";

function getProfileType(config: Record<string, unknown>): ProfileType {
  if (config["SakuraLLM"]) return "SakuraLLM";
  return "OpenAI-Compatible";
}

export function BackendProfilesPage() {
  const [profiles, setProfiles] = createSignal<ProfileEntry[]>([]);
  const [loading, setLoading] = createSignal(true);
  const [defaultName, setDefaultName] = createSignal(getDefaultBackendProfile());

  // 编辑器（新建/编辑共用）
  const [editorOpen, setEditorOpen] = createSignal(false);
  const [editorIsNew, setEditorIsNew] = createSignal(false);
  const [editName, setEditName] = createSignal("");
  const [editConfig, setEditConfig] = createSignal<Record<string, unknown>>({});
  const [editType, setEditType] = createSignal<ProfileType>("OpenAI-Compatible");
  const [saving, setSaving] = createSignal(false);

  // 按行缓存从接口拉取的模型列表（key = token 行下标）
  const [modelsByIndex, setModelsByIndex] = createSignal<Record<number, string[]>>({});
  const [fetchingIdx, setFetchingIdx] = createSignal<number | null>(null);
  // 已拉取模型名的自定义下拉（替代原生 datalist，确保拉取后实时刷新）
  const [openModelIdx, setOpenModelIdx] = createSignal<number | null>(null);

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

  // ---- 结构化读写辅助（基于 editConfig 响应式更新）----
  function getTokens(): TokenEntry[] {
    const oc = editConfig()["OpenAI-Compatible"] as OpenAICompatConfig | undefined;
    return oc?.tokens ?? [];
  }
  function setTokens(tokens: TokenEntry[]) {
    setEditConfig((prev) => {
      const next: Record<string, unknown> = { ...prev };
      next["OpenAI-Compatible"] = { tokens };
      delete next["SakuraLLM"];
      return next;
    });
  }
  function getEndpoints(): string[] {
    const sk = editConfig()["SakuraLLM"] as SakuraConfig | undefined;
    return sk?.endpoints ?? [];
  }
  function setEndpoints(endpoints: string[]) {
    setEditConfig((prev) => {
      const next: Record<string, unknown> = { ...prev };
      next["SakuraLLM"] = { endpoints };
      delete next["OpenAI-Compatible"];
      return next;
    });
  }

  function updateToken(idx: number, patch: Partial<TokenEntry>) {
    const tokens = getTokens().slice();
    tokens[idx] = { ...(tokens[idx] ?? {}), ...patch };
    setTokens(tokens);
  }
  function addToken() {
    setTokens([...getTokens(), { endpoint: "", modelName: "", token: "" }]);
  }
  function removeToken(idx: number) {
    const tokens = getTokens().slice();
    tokens.splice(idx, 1);
    setTokens(tokens);
  }
  function updateEndpoint(idx: number, val: string) {
    const ends = getEndpoints().slice();
    ends[idx] = val;
    setEndpoints(ends);
  }
  function addEndpoint() {
    setEndpoints([...getEndpoints(), ""]);
  }
  function removeEndpoint(idx: number) {
    const ends = getEndpoints().slice();
    ends.splice(idx, 1);
    setEndpoints(ends);
  }

  function rowModels(idx: number): string[] {
    return modelsByIndex()[idx] ?? [];
  }

  async function handleFetchModels(idx: number) {
    const t = getTokens()[idx];
    if (!t) return;
    setFetchingIdx(idx);
    try {
      const res = await fetchOpenAIModels({
        endpoint: t.endpoint || "",
        token: t.token || "",
      });
      if (res.models && res.models.length > 0) {
        setModelsByIndex((prev) => ({ ...prev, [idx]: res.models }));
        setOpenModelIdx(idx);
        toast.success(`已拉取 ${res.models.length} 个模型，可在模型名称处选择`);
      } else {
        toast.error("接口未返回模型列表（可能不支持 /models）");
      }
    } catch (e) {
      toast.error(`拉取模型失败: ${getErrorMessage(e)}`);
    } finally {
      setFetchingIdx(null);
    }
  }

  function changeType(t: ProfileType) {
    setEditType(t);
    if (t === "OpenAI-Compatible") {
      setEditConfig({ "OpenAI-Compatible": { tokens: [{ endpoint: "", modelName: "", token: "" }] } });
    } else {
      setEditConfig({ "SakuraLLM": { endpoints: [""] } });
    }
    setModelsByIndex({});
    setOpenModelIdx(null);
  }

  function openCreate() {
    setEditorIsNew(true);
    setEditName("");
    setEditType("OpenAI-Compatible");
    setEditConfig({ "OpenAI-Compatible": { tokens: [{ endpoint: "", modelName: "", token: "" }] } });
    setModelsByIndex({});
    setOpenModelIdx(null);
    setEditorOpen(true);
  }

  function openEditor(name: string) {
    const p = profiles().find((x) => x.name === name);
    if (!p) return;
    setEditorIsNew(false);
    setEditName(name);
    setEditType(getProfileType(p.config));
    setEditConfig({ ...p.config });
    setModelsByIndex({});
    setOpenModelIdx(null);
    setEditorOpen(true);
  }

  async function handleSubmit() {
    const name = editName().trim();
    if (!name) {
      toast.error("请填写配置名称");
      return;
    }
    setSaving(true);
    try {
      if (editorIsNew()) {
        await createBackendProfile(name, editConfig());
        toast.success("配置已创建");
      } else {
        await updateBackendProfile(name, editConfig());
        toast.success("配置已更新");
      }
      setOpenModelIdx(null);
      setEditorOpen(false);
      await loadProfiles();
    } catch (e) {
      toast.error(`保存失败: ${getErrorMessage(e)}`);
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
      return `${t.endpoint || "?"} / ${t.modelName || "?"}（${openai.tokens.length} 个密钥）`;
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
      <p class="page-description">
        管理 API 地址、模型、密钥等后端连接配置。OpenAI 兼容接口支持从接口拉取可用模型名。
      </p>

      <div class="bp-toolbar">
        <button class="btn btn--sm btn--primary" onClick={openCreate}>
          新建配置
        </button>
        <button class="btn btn--sm" onClick={loadProfiles} disabled={loading()}>
          {loading() ? "加载中…" : "刷新"}
        </button>
      </div>

      {/* 编辑 / 新建面板 */}
      <Show when={editorOpen()}>
        <div class="bp-editor-panel">
          <div class="bp-editor-header">
            <h3>{editorIsNew() ? "新建后端配置" : `编辑: ${editName()}`}</h3>
            <button class="btn btn--sm" onClick={() => setEditorOpen(false)}>
              关闭
            </button>
          </div>
          <div class="bp-editor-body">
            <Show when={openModelIdx() !== null}>
              <div class="bp-dropdown-backdrop" onClick={() => setOpenModelIdx(null)} />
            </Show>
            <div class="bp-field">
              <span class="bp-field__label">配置名称</span>
              <input
                class="field__input"
                value={editName()}
                disabled={!editorIsNew()}
                onInput={(e) => setEditName(e.currentTarget.value)}
                placeholder="例如: GPT-4o"
              />
            </div>

            <div class="bp-field bp-type-field">
              <span class="bp-field__label">后端类型</span>
              <select
                class="field__input pc-select"
                value={editType()}
                onChange={(e) => changeType(e.currentTarget.value as ProfileType)}
              >
                <option value="OpenAI-Compatible">OpenAI 兼容接口</option>
                <option value="SakuraLLM">Sakura 本地模型</option>
              </select>
            </div>

            {/* OpenAI 兼容接口：逐 token 结构化字段 */}
            <Show when={editType() === "OpenAI-Compatible"}>
              <p class="bp-json-hint">
                为每个接口填写 地址 / 模型 / 密钥；点击「拉取模型」可从接口获取可用模型名（需地址与密钥正确）。
                字段名固定为 <code>token</code>（非 apiKey）。
              </p>
              <div class="bp-tokens">
                <Index each={getTokens()}>
                  {(tokenSignal, i) => {
                    const t = () => tokenSignal();
                    return (
                      <div class="bp-token-row">
                        <div class="bp-token-fields">
                          <label class="bp-field">
                            <span class="bp-field__label">接口地址 (endpoint)</span>
                            <input
                              class="field__input"
                              value={t().endpoint ?? ""}
                              onInput={(e) => updateToken(i, { endpoint: e.currentTarget.value })}
                              placeholder="https://api.openai.com/v1"
                            />
                          </label>
                          <label class="bp-field">
                            <span class="bp-field__label">模型名称 (modelName)</span>
                            <div class="bp-model-field">
                              <div class="bp-model-input-wrap">
                                <input
                                  class="field__input"
                                  value={t().modelName ?? ""}
                                  onInput={(e) => updateToken(i, { modelName: e.currentTarget.value })}
                                  placeholder="例如 gpt-4o"
                                />
                                <button
                                  type="button"
                                  class="bp-model-toggle"
                                  disabled={rowModels(i).length === 0}
                                  onClick={() => setOpenModelIdx(openModelIdx() === i ? null : i)}
                                  title="选择已拉取的模型"
                                >
                                  ▾
                                </button>
                                <Show when={openModelIdx() === i && rowModels(i).length > 0}>
                                  <div class="bp-model-dropdown">
                                    <For each={rowModels(i)}>
                                      {(m) => (
                                        <div
                                          class="bp-model-option"
                                          onClick={() => {
                                            updateToken(i, { modelName: m });
                                            setOpenModelIdx(null);
                                          }}
                                        >
                                          {m}
                                        </div>
                                      )}
                                    </For>
                                  </div>
                                </Show>
                              </div>
                              <button
                                class="btn btn--sm"
                                onClick={() => handleFetchModels(i)}
                                disabled={fetchingIdx() === i}
                                title="从接口拉取可用模型列表"
                              >
                                {fetchingIdx() === i ? "拉取中…" : "拉取模型"}
                              </button>
                            </div>
                          </label>
                          <label class="bp-field">
                            <span class="bp-field__label">密钥 (token)</span>
                            <input
                              class="field__input"
                              type="password"
                              value={t().token ?? ""}
                              onInput={(e) => updateToken(i, { token: e.currentTarget.value })}
                              placeholder="sk-..."
                            />
                          </label>
                        </div>
                        <button
                          class="bp-row-del"
                          title="删除此 token"
                          onClick={() => removeToken(i)}
                        >
                          ×
                        </button>
                      </div>
                    );
                  }}
                </Index>
              </div>
              <button class="btn btn--sm bp-add-btn" onClick={addToken}>
                + 添加 token
              </button>
            </Show>

            {/* Sakura 本地模型：逐 endpoint 结构化字段 */}
            <Show when={editType() === "SakuraLLM"}>
              <p class="bp-json-hint">填写本地 Sakura 模型服务地址（可多个，轮流调用）。</p>
              <div class="bp-tokens">
                <Index each={getEndpoints()}>
                  {(epSignal, i) => (
                    <div class="bp-token-row">
                      <div class="bp-token-fields">
                        <label class="bp-field">
                          <span class="bp-field__label">本地地址 (endpoint)</span>
                          <input
                            class="field__input"
                            value={epSignal()}
                            onInput={(e) => updateEndpoint(i, e.currentTarget.value)}
                            placeholder="http://127.0.0.1:8080"
                          />
                        </label>
                      </div>
                      <button
                        class="bp-row-del"
                        title="删除此地址"
                        onClick={() => removeEndpoint(i)}
                      >
                        ×
                      </button>
                    </div>
                  )}
                </Index>
              </div>
              <button class="btn btn--sm bp-add-btn" onClick={addEndpoint}>
                + 添加地址
              </button>
            </Show>

            <details class="bp-raw">
              <summary>查看原始 JSON（只读）</summary>
              <textarea class="bp-json-editor" readOnly value={JSON.stringify(editConfig(), null, 2)} />
            </details>
          </div>
          <div class="bp-editor-footer">
            <button class="btn btn--sm btn--primary" onClick={handleSubmit} disabled={saving()}>
              {saving() ? "保存中…" : editorIsNew() ? "创建" : "保存"}
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
