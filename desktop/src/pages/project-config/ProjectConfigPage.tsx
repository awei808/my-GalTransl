import { createSignal, For, Show, Switch, Match, createEffect } from "solid-js";
import { appState, getActiveConfigFileName, navigateTo } from "../../stores/appStore";
import { toast } from "../../stores/toastStore";
import { getErrorMessage } from "../../lib/errors";
import { fetchProjectConfig, updateProjectConfig, fetchConfigSchema } from "../../lib/api/project";

/** 配置文件中任意 JSON 值（递归类型，代替 any） */
type ConfigValue =
  string | number | boolean | null | ConfigValue[] | { [key: string]: ConfigValue };

/**
 * 人工审校的字段UI映射：给零基础用户看的短标签 + 人话提示。
 * 后端 YAML 注释是给开发者看的，不能直接抄到前端当说明。
 * 未在此表内的字段退回「清洗后的注释首句」兜底。
 */
interface FieldUI {
  label: string;
  hint?: string;
}

/**
 * 已由程序全局「后端配置」统一管理的配置前缀：这些字段（含 AI 令牌）不再在项目设置中维护，
 * 检测 / 调用统一走全局后端配置，避免与项目自身 config.yaml 的 tokens 产生歧义。
 */
const MANAGED_GLOBAL_PREFIX = "backendSpecific.OpenAI-Compatible.";
const FIELD_UI: Record<string, FieldUI> = {
  "backendSpecific.OpenAI-Compatible.tokenStrategy": {
    label: "令牌轮询策略",
    hint: "random：随机轮询多个令牌；fallback：优先用第一个，出错时自动切换下一个。",
  },
  "backendSpecific.OpenAI-Compatible.checkAvailable": { label: "翻译前检查接口可用性" },
  "backendSpecific.OpenAI-Compatible.checkAvailableConcurrency": {
    label: "可用性检测并发数",
    hint: "启动时并发检测的数量，避免瞬间打满请求。",
  },
  "backendSpecific.OpenAI-Compatible.globalRequestRPM": {
    label: "全局请求限速（次/分钟）",
    hint: "跨任务的总请求频率上限，0 表示不限制。",
  },
  "backendSpecific.OpenAI-Compatible.stream": { label: "流式输出" },
  "backendSpecific.OpenAI-Compatible.apiTimeout": {
    label: "请求超时（秒）",
  },
  "backendSpecific.OpenAI-Compatible.apiErrorWait": {
    label: "API 错误重试等待",
    hint: "可选 auto（自动适应频率），或填写固定等待秒数 0–120。",
  },
  "backendSpecific.SakuraLLM.rewriteModelName": {
    label: "自定义模型名（Sakura）",
    hint: "使用 ollama 等本地模型时需修改。",
  },
  "plugin.filePlugin": {
    label: "文件格式插件",
    hint: "字幕用 file_subtitle_srt_lrc_vtt；小说用 file_epub_epub / file_plaintext_txt。",
  },
  "plugin.file_galtransl_json.output_with_src": { label: "输出保留原文" },
  "common.gpt.numPerRequestTranslate": {
    label: "每次请求句数",
    hint: "单次发送给模型的句子数，建议不超过 16。",
  },
  "common.gpt.dynamicNumPerRequestTranslate": { label: "动态句数调整" },
  "common.gpt.dynamicNumPerRequestTranslate.min": { label: "动态句数下限" },
  "common.gpt.dynamicNumPerRequestTranslate.max": { label: "动态句数上限" },
  "common.workersPerProject": {
    label: "项目并行文件数",
    hint: "同时翻译的文件数；单文件内并行需配合下方分片设置。",
  },
  "common.autoAdjustWorkers": { label: "自动调节并发" },
  "common.sortBy": {
    label: "文件调度顺序",
    hint: "name：按文件名；size：优先大文件（并行时通常更快）。",
  },
  "common.language": {
    label: "目标语言",
    hint: "译文输出的目标语言。",
  },
  "common.splitFile": {
    label: "单文件分片模式",
    hint: "no：不分片；Num：每 n 句切一片；Equal：每文件均分 n 片。",
  },
  "common.splitFileNum": {
    label: "分片参数",
    hint: "Num 模式表示每片句数；Equal 模式表示分片总数。",
  },
  "common.splitFileCrossNum": {
    label: "分片重叠句数",
    hint: "片段间的上下文缓冲句数，可提升衔接质量，推荐 0 或 10。",
  },
  "common.save_steps": {
    label: "自动保存间隔（批次）",
    hint: "每处理 n 个批次保存一次缓存，值越大保存越少、可能越快。",
  },
  "common.start_time": {
    label: "定时启动时间",
    hint: "留空表示立即启动，格式如 00:30（24 小时制）。",
  },
  "common.linebreakSymbol": {
    label: "换行符类型",
    hint: "JSON 内换行符种类，供问题检测/自动修复使用，不改变翻译语义。",
  },
  "common.skipH": { label: "跳过敏感句" },
  "common.smartRetry": { label: "智能重试" },
  "common.retranslFail": { label: "重启时重翻失败句" },
  "common.gpt.contextNum": {
    label: "前文句数",
    hint: "每次请求附带的前文句数；越大上下文越强、成本越高（常用 8）。",
  },
  "common.gpt.translation_guideline": {
    label: "翻译规范文件",
    hint: "位于 translation_guidelines 目录，影响文风与措辞。",
  },
  "common.gpt.enhance_jailbreak": { label: "抗拒答增强" },
  "common.gpt.change_prompt": {
    label: "提示词修改模式",
    hint: "no：不改；AdditionalPrompt：追加；OverwritePrompt：覆盖默认提示词。",
  },
  "common.gpt.prompt_content": { label: "提示词自定义内容" },
  "common.gpt.token_limit": {
    label: "单轮 Token 上限（Sakura）",
    hint: "0 表示不限制，用于避免上下文溢出。",
  },
  "common.loggingLevel": {
    label: "日志级别",
    hint: "debug：最详细；info：常规；warning：仅警告。",
  },
  "common.saveLog": { label: "日志写入文件" },
  "internals.pipeline.maxInputChars": {
    label: "全局分析最大字符数",
    hint: "压缩后发送给大模型的最大字符数。",
  },
  "internals.pipeline.forceRegenDic": { label: "强制重新生成术语表" },
  "internals.pipeline.abortOnDicFailure": { label: "术语表失败即中止" },
  "internals.forglobalprompt.inject_guideline": { label: "注入翻译规范（全局分析）" },
  "internals.forbatchmeta.max_batches": {
    label: "批次最大数量",
    hint: "翻译区间最大数量，超过将自动合并相邻区间。",
  },
  "internals.forbatchmeta.inject_guideline": { label: "注入翻译规范（批次划分）" },
  "internals.forfilemeta.inject_guideline": { label: "注入翻译规范（文件元数据）" },
  "proxy.enableProxy": { label: "启用代理" },
  "dictionary.defaultDictFolder": {
    label: "通用字典文件夹",
    hint: "相对于程序目录，也可填绝对路径。",
  },
  "dictionary.usePreDictInName": { label: "译前字典用于 name 字段" },
  "dictionary.usePostDictInName": { label: "译后字典用于 name 字段" },
  "dictionary.useGPTDictInName": { label: "GPT 字典用于 name 字段" },
  "dictionary.sortDict": { label: "字典按词长排序" },
};

/** 枚举关键字的友好显示（仅用于混合/枚举控件的选项文案） */
const KEYWORD_LABELS: Record<string, string> = {
  auto: "自动适应 (auto)",
};

export function ProjectConfigPage() {
  const [config, setConfig] = createSignal<Record<string, ConfigValue>>({});
  const [schemaDesc, setSchemaDesc] = createSignal<Record<string, string>>({});
  const [loading, setLoading] = createSignal(true);
  const [saving, setSaving] = createSignal(false);

  const pid = () => appState.activeProjectId;

  // 等真实配置名探测完成后再加载，避免用回退名 config.yaml 提前请求导致 404
  createEffect(() => {
    if (pid() && !appState.configNameDetecting) loadData();
  });

  async function loadData() {
    if (!pid()) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const [cfg, sch] = await Promise.all([
        fetchProjectConfig(pid()!, getActiveConfigFileName()),
        fetchConfigSchema(pid()!).catch(() => ({ parameters: {} })),
      ]);
      setConfig({ ...(cfg.config as Record<string, ConfigValue>) });
      setSchemaDesc(sch?.parameters || {});
    } catch (e) {
      toast.error(`加载配置失败: ${getErrorMessage(e)}`);
    } finally {
      setLoading(false);
    }
  }

  /** 将嵌套配置展平为点分键（如 "common.workersPerProject"），按前缀分组 */
  // 结构签名缓存：编辑字段「值」不应触发列表重建。
  // 仅当「键集合 / 分组」变化时返回新数组；否则复用同一引用，
  // 否则 <For> 会在每次 setValue（按键）时按引用判定为「全新项」而重建所有 <input>，
  // 导致输入框失焦、输入法（IME）中断。元组内携带的 value 实际未被 JSX 使用
  // （值始终由响应式 getValue(key) 读取），故缓存过期值亦无副作用。
  let _groupedSig = "";
  let _groupedCache: [string, [string, ConfigValue, string][]][] | null = null;

  /** 仅收集键路径生成签名（不含值），编辑值不会改变签名 */
  function keySignature(obj: Record<string, ConfigValue>, prefix = ""): string {
    const parts: string[] = [];
    for (const k of Object.keys(obj).sort()) {
      const key = prefix ? `${prefix}.${k}` : k;
      const v = obj[k];
      if (v !== null && typeof v === "object" && !Array.isArray(v)) {
        parts.push(keySignature(v as Record<string, ConfigValue>, key));
      } else {
        parts.push(key);
      }
    }
    return parts.join("|");
  }

  const groupedKeys = () => {
    const c = config();
    const sig = keySignature(c);
    if (_groupedSig === sig && _groupedCache) return _groupedCache;

    const flat: [string, ConfigValue, string][] = []; // [key, value, displayType]

    function walk(obj: Record<string, ConfigValue>, prefix: string) {
      for (const [k, v] of Object.entries(obj)) {
        const key = prefix ? `${prefix}.${k}` : k;
        if (v !== null && typeof v === "object" && !Array.isArray(v)) {
          walk(v as Record<string, ConfigValue>, key);
        } else {
          // 区分值类型以便渲染时选择展示方式
          let dtype = "scalar"; // string | number | boolean
          if (Array.isArray(v)) {
            dtype =
              v.length > 0 && typeof v[0] === "object" && v[0] !== null
                ? "object-array" // 如 tokens: [{token,endpoint,...}]
                : "array"; // 如 [1,2,3] 或 ["a","b"]
          }
          flat.push([key, v, dtype]);
        }
      }
    }
    walk(c, "");

    const groups = new Map<string, [string, ConfigValue, string][]>();
    for (const item of flat) {
      const key = item[0];
      const dot = key.indexOf(".");
      const group = dot > 0 ? key.slice(0, dot) : "_root";
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group)!.push(item);
    }
    _groupedCache = [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
    _groupedSig = sig;
    return _groupedCache;
  };

  /** 将后端 schema 注释拆分为「标签 + 可选值」：注释形如 "说明文字。[a/b/c]" */
  function parseComment(raw?: string): { label: string; allowed?: string } {
    if (!raw) return { label: "" };
    const m = raw.match(/^(.*?)\[(.*)\]\s*$/s);
    if (m && m[2].trim()) return { label: m[1].trim(), allowed: m[2].trim() };
    return { label: raw.trim() };
  }

  /** 无中文注释时的兜底：把 workersPerProject 这类 key 人话化（仍是英文，仅兜底用） */
  function humanizeKey(key: string): string {
    const last = key.split(".").pop() ?? key;
    return last
      .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
      .replace(/[_-]/g, " ")
      .replace(/\b\w/g, (c) => c.toUpperCase());
  }

  /** 字段标签：优先用人工审校的短标签，兜底用清洗后的注释首句 */
  function getFieldLabel(key: string): string {
    const ui = FIELD_UI[key];
    if (ui?.label) return ui.label;
    const { label } = parseComment(schemaDesc()[key]);
    return label || humanizeKey(key);
  }

  /** 字段提示：人工审校的人话说明；无则回退注释首句 */
  function getFieldHint(key: string): string {
    const ui = FIELD_UI[key];
    if (ui?.hint) return ui.hint;
    const { label } = parseComment(schemaDesc()[key]);
    return label;
  }

  /** 可选值原文（如 auto/0-120 / [name/size]），仅用于解析控件类型 */
  function getFieldAllowed(key: string): string {
    return parseComment(schemaDesc()[key]).allowed ?? "";
  }

  /** 关键字友好名（枚举/混合控件选项文案） */
  function keywordLabel(kw: string): string {
    return KEYWORD_LABELS[kw] ?? kw;
  }

  /** 纯离散枚举：所有 / 分段都是离散 token（无数值范围、无"推荐"、非 True/False） */
  function isEnumField(key: string): boolean {
    const allowed = getFieldAllowed(key);
    if (!allowed) return false;
    if (allowed === "True/False") return false;
    if (allowed.includes("推荐")) return false; // 只是建议，非封闭选项
    const segs = allowed.split("/").map((s) => s.trim());
    return segs.every((s) => !/^\d+-\d+$/.test(s)) && segs.length >= 1;
  }

  /** 解析纯数值范围（如 1-32） */
  function getRange(key: string): { min: number; max: number } | null {
    const m = getFieldAllowed(key).match(/(\d+)-(\d+)/);
    if (!m) return null;
    return { min: Number(m[1]), max: Number(m[2]) };
  }

  /**
   * 混合字段：关键字 + 数值范围，如 apiErrorWait 的 [auto/0-120]
   * 语义：要么选 auto，要么填 0–120 的数字。不能当成两选项枚举。
   */
  function getHybrid(key: string): { keywords: string[]; min: number; max: number } | null {
    const allowed = getFieldAllowed(key);
    if (!allowed) return null;
    const segs = allowed.split("/").map((s) => s.trim());
    const range = segs.find((s) => /^\d+-\d+$/.test(s));
    if (!range) return null;
    const keywords = segs.filter((s) => s !== range);
    if (keywords.length === 0) return null;
    const [minS, maxS] = range.split("-");
    return { keywords, min: Number(minS), max: Number(maxS) };
  }

  /** 混合字段当前是否处于「自定义数值」模式（值不是任一关键字） */
  function isHybridCustom(key: string, val: ConfigValue | ""): boolean {
    const h = getHybrid(key);
    if (!h) return false;
    const s = String(val ?? "");
    if (s === "") return false; // 空值按关键字处理（select 默认选中首个关键字）
    return !h.keywords.includes(s);
  }

  /** 控件类型：决定该字段如何渲染 */
  type ControlType = "enum" | "hybrid" | "range" | "time" | "number" | "text";
  function getControlType(key: string): ControlType {
    const type = inferType(key);
    if (type === "boolean") return "text"; // 布尔由外层 checkbox 处理
    const allowed = getFieldAllowed(key);
    if (allowed) {
      if (getHybrid(key)) return "hybrid";
      if (isEnumField(key)) return "enum";
      if (/^\d+-\d+$/.test(allowed)) return "range";
      if (/^\d{2}:\d{2}-\d{2}:\d{2}$/.test(allowed)) return "time";
    }
    return type === "number" ? "number" : "text";
  }

  /** 解析纯枚举可选值为 { value, label } 列表（仅纯枚举用） */
  function getEnumOptions(key: string): { value: string; label: string }[] {
    const allowed = getFieldAllowed(key);
    if (!allowed) return [];
    return allowed.split("/").map((v) => {
      const t = v.trim();
      return { value: t, label: keywordLabel(t) };
    });
  }

  function getValue(key: string): ConfigValue | "" {
    const parts = key.split(".");
    let v: Record<string, ConfigValue> = config();
    for (const p of parts) {
      if (v == null || typeof v !== "object" || Array.isArray(v)) return "";
      v = v[p] as Record<string, ConfigValue>;
    }
    return v !== undefined && v !== null ? v : "";
  }

  function setValue(key: string, value: ConfigValue) {
    const parts = key.split(".");
    setConfig((prev) => {
      const next = { ...prev };
      let cur: Record<string, ConfigValue> = next;
      for (let i = 0; i < parts.length - 1; i++) {
        const k = parts[i];
        if (cur[k] == null || typeof cur[k] !== "object" || Array.isArray(cur[k])) {
          cur[k] = {};
        }
        cur[k] = { ...(cur[k] as Record<string, ConfigValue>) };
        cur = cur[k] as Record<string, ConfigValue>;
      }
      cur[parts[parts.length - 1]] = value;
      return next;
    });
  }

  function inferType(key: string): "string" | "number" | "boolean" {
    const v = getValue(key);
    if (typeof v === "boolean") return "boolean";
    if (typeof v === "number") return "number";
    return "string";
  }

  /** 格式化非标量值用于显示 */
  function formatNonScalarValue(v: ConfigValue, dtype: string): string {
    if (dtype === "object-array") {
      return `${Array.isArray(v) ? v.length : 0} 项（对象数组，请在编辑提示词或文件中修改）`;
    }
    if (dtype === "array") {
      return Array.isArray(v) ? v.join(", ") : "";
    }
    return String(v ?? "");
  }

  async function handleSave() {
    if (!pid()) return;
    setSaving(true);
    try {
      await updateProjectConfig(pid()!, {
        config: config(),
        config_file_name: getActiveConfigFileName(),
      });
      toast.success("配置已保存");
    } catch (e) {
      toast.error(`保存失败: ${getErrorMessage(e)}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div class="page page-project-config">
      <div class="pc-header">
        <div>
          <h2 class="page-title">项目设置</h2>
          <p class="page-description">编辑当前项目的 {getActiveConfigFileName()} 配置参数。</p>
        </div>
        <button
          class="btn btn--sm btn--primary"
          onClick={handleSave}
          disabled={saving() || loading()}
        >
          {saving() ? "保存中…" : "保存配置"}
        </button>
      </div>

      <Show when={!loading()} fallback={<p class="pc-status">加载中…</p>}>
        <Show
          when={pid() && groupedKeys().length > 0}
          fallback={<p class="pc-status">{!pid() ? "请先打开一个项目" : "暂无可编辑的配置参数"}</p>}
        >
          <div class="pc-field-list">
            <For each={groupedKeys()}>
              {([group, items]) => (
                <div class="pc-group">
                  <h3 class="pc-group-title">{group}</h3>
                  <Show when={group === "backendSpecific"}>
                    <div class="pc-global-banner">
                      <div class="pc-global-banner__text">
                        <strong>OpenAI 兼容接口</strong> 的 API 令牌与连接参数已由程序全局「后端配置」统一管理，不再在项目设置中维护。
                      </div>
                      <button
                        class="btn btn--sm btn--primary"
                        onClick={() => navigateTo("backend-profiles")}
                      >
                        去后端配置 →
                      </button>
                    </div>
                  </Show>
                  <For each={items}>
                    {(item) => {
                      const [key, , dtype] = item;
                      // 该前缀下的字段（含 AI 令牌）交由全局后端配置管理，不在项目设置渲染
                      if (key.startsWith(MANAGED_GLOBAL_PREFIX)) return <></>;
                      const type = inferType(key);
                      const val = getValue(key);
                      const isNonScalar = dtype === "object-array" || dtype === "array";
                      return (
                        <div class="pc-row">
                          <div class="pc-row-label">
                            <span class="pc-label">{getFieldLabel(key)}</span>
                            <div class="pc-key-hint">
                              <code class="pc-key">{key}</code>
                            </div>
                            <Show when={getFieldHint(key)}>
                              <p class="pc-desc">{getFieldHint(key)}</p>
                            </Show>
                          </div>
                          <div class="pc-row-control">
                            <Show
                              when={!isNonScalar && type !== "boolean"}
                              fallback={
                                isNonScalar ? (
                                  <input
                                    class="field__input pc-input pc-input--readonly"
                                    type="text"
                                    value={formatNonScalarValue(val, dtype)}
                                    readOnly
                                    title="此字段为数组/对象，请在编辑提示词或直接编辑配置文件修改"
                                  />
                                ) : (
                                  <label class="settings-toggle">
                                    <input
                                      type="checkbox"
                                      checked={!!val}
                                      onChange={(e) => setValue(key, e.currentTarget.checked)}
                                    />
                                    <span class="settings-toggle-knob" />
                                  </label>
                                )
                              }
                            >
                              <Switch>
                                {/* 纯离散枚举 → 下拉选择 */}
                                <Match when={getControlType(key) === "enum"}>
                                  <select
                                    class="field__input pc-input pc-select"
                                    value={String(val ?? "")}
                                    onChange={(e) => setValue(key, e.currentTarget.value)}
                                  >
                                    {getEnumOptions(key).map((opt) => (
                                      <option value={opt.value}>{opt.label}</option>
                                    ))}
                                  </select>
                                </Match>
                                {/* 关键字 + 数值范围混合：如 apiErrorWait [auto/0-120] → 选 auto 或填 0–120 */}
                                <Match when={getControlType(key) === "hybrid"}>
                                  <div class="pc-hybrid">
                                    <select
                                      class="field__input pc-input pc-select pc-select--sm"
                                      value={
                                        isHybridCustom(key, val)
                                          ? "__custom__"
                                          : String(val ?? "")
                                      }
                                      onChange={(e) => {
                                        const v = e.currentTarget.value;
                                        if (v === "__custom__") {
                                          setValue(key, getHybrid(key)?.min ?? 0);
                                        } else {
                                          setValue(key, v);
                                        }
                                      }}
                                    >
                                      {getHybrid(key)?.keywords.map((kw) => (
                                        <option value={kw}>{keywordLabel(kw)}</option>
                                      ))}
                                      <option value="__custom__">
                                        自定义（{getHybrid(key)?.min ?? 0}–
                                        {getHybrid(key)?.max ?? 0}）
                                      </option>
                                    </select>
                                    <Show when={isHybridCustom(key, val)}>
                                      <input
                                        class="field__input pc-input pc-input--num"
                                        type="number"
                                        min={getHybrid(key)?.min ?? 0}
                                        max={getHybrid(key)?.max ?? 0}
                                        value={Number(val ?? 0)}
                                        onInput={(e) =>
                                          setValue(
                                            key,
                                            e.currentTarget.value === ""
                                              ? 0
                                              : Number(e.currentTarget.value),
                                          )
                                        }
                                      />
                                    </Show>
                                  </div>
                                </Match>
                                {/* 纯数值范围 / 数值 → 带上下限的数字框 */}
                                <Match
                                  when={
                                    getControlType(key) === "range" ||
                                    getControlType(key) === "number"
                                  }
                                >
                                  <input
                                    class="field__input pc-input"
                                    type="number"
                                    min={getRange(key)?.min}
                                    max={getRange(key)?.max}
                                    value={String(val ?? "")}
                                    onInput={(e) =>
                                      setValue(
                                        key,
                                        e.currentTarget.value === ""
                                          ? ""
                                          : Number(e.currentTarget.value),
                                      )
                                    }
                                  />
                                </Match>
                                {/* 时间范围 → 时间选择器 */}
                                <Match when={getControlType(key) === "time"}>
                                  <input
                                    class="field__input pc-input"
                                    type="time"
                                    value={String(val ?? "")}
                                    onInput={(e) => setValue(key, e.currentTarget.value)}
                                  />
                                </Match>
                                {/* 其余文本 */}
                                <Match when={getControlType(key) === "text"}>
                                  <input
                                    class="field__input pc-input"
                                    type="text"
                                    value={String(val ?? "")}
                                    onInput={(e) => setValue(key, e.currentTarget.value)}
                                  />
                                </Match>
                              </Switch>
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
