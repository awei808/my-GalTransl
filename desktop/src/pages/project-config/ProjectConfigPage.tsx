import { createSignal, For, Show, Switch, Match, createEffect } from "solid-js";
import { appState, getActiveConfigFileName, navigateTo } from "../../stores/appStore";
import { toast } from "../../stores/toastStore";
import { getErrorMessage } from "../../lib/errors";
import { fetchProjectConfig, updateProjectConfig, fetchConfigSchema } from "../../lib/api/project";
import { fetchTranslationGuidelines, fetchPlugins } from "../../lib/api/general";

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
// 动态句数调整：仅暴露“是否启用”开关，句数由后端按 上限/4 自动管理，以下子项不再让用户手填
const DYNAMIC_NUM_KEY = "common.gpt.dynamicNumPerRequestTranslate";
const DYNAMIC_MAX_KEY = "common.gpt.dynamicNumPerRequestTranslate.max";
// 翻译规范文件：改为下拉选择，选项来自 translation_guidelines 目录文件名。
// 注意：后端 BaseTranslate 只读「gpt.translation_guideline」（见 GalTransl/Backend/BaseTranslate.py），
// 因此此处必须用无 common. 前缀的规范键，否则选择会被后端忽略。
const GUIDELINE_KEY = "gpt.translation_guideline";
const HIDDEN_CONFIG_KEYS = new Set<string>([
  "common.gpt.dynamicNumPerRequestTranslate.min",
  "common.gpt.dynamicNumPerRequestTranslate.max",
  // 改为专用多行「游戏外部信息」输入框（见页面顶部卡片），不再以单行英文框出现在通用列表
  "externals.gameInfo",
  // 旧版本误写到此处的翻译规范键（后端只读 gpt.translation_guideline），隐藏避免重复行
  "common.gpt.translation_guideline",
  // problemAnalyze 功能已在程序全局设置中有更完善的配置项，项目设置中不再暴露
  "problemAnalyze.problemList",
]);
// 暂不在前端暴露的键：contextNum 改用多轮对话完整保留上下文，无需切分，故移除前端入口
const REMOVED_CONFIG_KEYS = new Set<string>([
  "common.gpt.contextNum",
]);
// 「仅在无批次级元数据时启用」板块：这些项在有批次级元数据（ForBatchMetaData）时
// 被 force_static 等逻辑强制忽略，仅无元数据的退化分支生效
const CONDITIONAL_SECTION_KEYS = new Set<string>([
  "common.gpt.numPerRequestTranslate",
  "common.gpt.dynamicNumPerRequestTranslate",
  "common.splitFile",
  "common.splitFileNum",
  "common.splitFileCrossNum",
]);
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
    hint: "使用 ollama 等本地模型时需修改。仅 Sakura 引擎。",
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
  "common.gpt.dynamicNumPerRequestTranslate": {
    label: "是否启用动态句数调整",
    hint: "启用后初始每次请求句数 = 上限/4，并根据解析错误自动调节，无需手填。",
  },
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
  "gpt.translation_guideline": {
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
    hint: "0 表示不限制，用于避免上下文溢出。仅 Sakura 引擎。",
  },
  "common.loggingLevel": {
    label: "日志级别",
    hint: "debug：最详细；info：常规；warning：仅警告。",
  },
  "common.saveLog": { label: "日志写入文件" },
  "internals.pipeline.maxInputChars": {
    label: "全局分析最大字符数",
    hint: "压缩后发送给大模型的最大字符数，默认 0.95M（约 95 万字符）。",
  },
  "internals.pipeline.forceRegenDic": { label: "强制重新生成术语表" },
  "internals.pipeline.abortOnDicFailure": { label: "术语表失败即中止" },
  "internals.forglobalprompt.inject_guideline": {
    label: "全局分析参考翻译规范",
    hint: "开启后，生成游戏概况与角色档案时参考翻译规范，影响描写风格与措辞。",
  },
  "internals.forbatchmeta.inject_guideline": {
    label: "批次划分参考翻译规范",
    hint: "开启后，划分翻译区间批次时参考翻译规范，使各批次翻译风格一致。",
  },
  "internals.forfilemeta.inject_guideline": {
    label: "文件元数据参考翻译规范",
    hint: "开启后，各文件元数据生成参考翻译规范，保持多文件风格统一。",
  },
  "internals.forbatchmeta.max_batches": {
    label: "批次最大数量",
    hint: "翻译区间最大数量，超过将自动合并相邻区间。",
  },
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

/**
 * 这些键不进入「通用配置列表」（在展平阶段直接跳过），改为专用卡片、隐藏行或条件板块：
 * - 后端全局管理前缀：交全局后端配置页维护
 * - HIDDEN_CONFIG_KEYS：旧版本残留/由专用卡片接管的键
 * - REMOVED_CONFIG_KEYS：暂不在前端暴露的键（如 contextNum）
 * - GUIDELINE_KEY：改为顶部固定「翻译规范文件」卡片渲染
 * - CONDITIONAL_SECTION_KEYS：移入「仅在无批次级元数据时启用」板块（见页面）
 * 注意：DYNAMIC_NUM_KEY 不在通用列表，而在「仅在无批次级元数据时启用」板块由专用开关渲染。
 */
function isOmittedFromList(key: string): boolean {
  if (key.startsWith(MANAGED_GLOBAL_PREFIX)) return true;
  if (HIDDEN_CONFIG_KEYS.has(key)) return true;
  if (REMOVED_CONFIG_KEYS.has(key)) return true;
  if (key === GUIDELINE_KEY) return true;
  return false;
}

/**
 * 旧版曾把翻译规范误写到 common.gpt.translation_guideline，而后端只读 gpt.translation_guideline。
 * 该旧键会被 HIDDEN_CONFIG_KEYS 隐藏，新键又可能不存在 → 配置项整行消失。
 * 加载时把旧键值归一化到新键（仅在内存，保存时由 handleSave 清理旧键），保证下拉框始终能显示当前选择。
 */
function migrateGuidelineKey(obj: Record<string, ConfigValue>): Record<string, ConfigValue> {
  const common = obj.common as Record<string, ConfigValue> | undefined;
  const oldVal =
    common && typeof common === "object" ? common["gpt.translation_guideline"] : undefined;
  const gpt = obj.gpt as Record<string, ConfigValue> | undefined;
  const hasNew = !!gpt && typeof gpt === "object" && "translation_guideline" in gpt;
  if (oldVal === undefined || oldVal === null || hasNew) return obj;
  const next: Record<string, ConfigValue> = { ...obj };
  next.gpt = {
    ...(gpt as Record<string, ConfigValue> | {}),
    translation_guideline: oldVal,
  } as Record<string, ConfigValue>;
  const commonCopy = { ...(common as Record<string, ConfigValue>) };
  delete commonCopy["gpt.translation_guideline"];
  next.common = commonCopy;
  return next;
}

/** 枚举关键字的友好显示（仅用于混合/枚举控件的选项文案） */
const KEYWORD_LABELS: Record<string, string> = {
  auto: "自动适应 (auto)",
};

/** 分组标题英→中映射 */
const GROUP_LABELS: Record<string, string> = {
  backendSpecific: "后端配置",
  plugin: "插件",
  common: "通用设置",
  gpt: "GPT 参数",
  proxy: "代理",
  dictionary: "字典",
  internals: "内部参数",
  _root: "其他",
  externals: "外部信息",
};

export function ProjectConfigPage() {
  const [config, setConfig] = createSignal<Record<string, ConfigValue>>({});
  const [schemaDesc, setSchemaDesc] = createSignal<Record<string, string>>({});
  const [loading, setLoading] = createSignal(true);
  const [saving, setSaving] = createSignal(false);
  // 翻译规范文件下拉选项（translation_guidelines 目录下的文件名）
  const [guidelines, setGuidelines] = createSignal<string[]>([]);
  // 文件格式插件下拉选项（plugins 目录下 type 为 file 的插件名）
  const [filePlugins, setFilePlugins] = createSignal<string[]>([]);
  // 当前翻译规范值（响应式读取，供固定卡片展示）
  const guidelineCurrent = () => String(getValue(GUIDELINE_KEY) ?? "");

  const pid = () => appState.activeProjectId;

  // 等真实配置名探测完成后再加载，避免用回退名 config.yaml 提前请求导致 404
  createEffect(() => {
    if (!pid()) {
      // 无打开项目时不发请求，且需主动取消 loading，否则页面会永久卡在“加载中…”
      setLoading(false);
      return;
    }
    if (!appState.configNameDetecting) loadData();
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
      setConfig({ ...migrateGuidelineKey(cfg.config as Record<string, ConfigValue>) });
      setSchemaDesc(sch?.parameters || {});
      // 翻译规范文件下拉选项（与配置加载并行，失败不阻塞主配置）
      fetchTranslationGuidelines()
        .then((list) => setGuidelines(list))
        .catch(() => setGuidelines([]));
      // 文件格式插件下拉选项（与配置加载并行，失败不阻塞主配置）
      fetchPlugins()
        .then((plugins) =>
          setFilePlugins(
            plugins
              .filter((p) => p.type === "file")
              .map((p) => p.name)
              .sort(),
          ),
        )
        .catch(() => setFilePlugins([]));
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
        // 这些键不进入通用列表（专用卡片/隐藏行/「仅在无批次级元数据时启用」板块），直接跳过
        if (isOmittedFromList(key)) continue;
        // 移入「仅在无批次级元数据时启用」板块，不出现在通用分组
        if (CONDITIONAL_SECTION_KEYS.has(key)) continue;
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
    // 丢弃没有任何可见项的空分组（如 gpt 组在规范键移出后可能只剩被隐藏项）
    _groupedCache = [...groups.entries()]
      .filter(([, items]) => items.length > 0)
      .sort(([a], [b]) => a.localeCompare(b));
    _groupedSig = sig;
    return _groupedCache;
  };

  /**
   * 「仅在无批次级元数据时启用」板块的项：始终列出全部 CONDITIONAL_SECTION_KEYS，
   * 不再因“后端 config 未含该键”而跳过。这些参数属于「退化分支才生效」的可配置项，
   * 理应始终可被编辑；若 config 未显式声明（依赖默认值/精简配置），getValue 返回空，
   * 输入框留空、用户填写后由 setValue 写回即可。原先的 v==="" 跳过会令板块在缺键配置下
   * 永久隐藏、无法设置，属于设计缺陷。
   */
  const conditionalItems = (): [string, ConfigValue, string][] => {
    const result: [string, ConfigValue, string][] = [];
    for (const key of CONDITIONAL_SECTION_KEYS) {
      const v = getValue(key);
      let dtype = "scalar";
      if (Array.isArray(v)) {
        dtype =
          v.length > 0 && typeof v[0] === "object" && v[0] !== null
            ? "object-array"
            : "array";
      }
      result.push([key, v, dtype]);
    }
    return result;
  };

  /** 单个配置项的渲染（通用列表与「仅在无批次级元数据时启用」板块共用） */
  function renderFieldRow(item: [string, ConfigValue, string]) {
    const [key, , dtype] = item;
    // 该前缀下的字段（含 AI 令牌）交由全局后端配置管理，不在项目设置渲染
    if (key.startsWith(MANAGED_GLOBAL_PREFIX)) return <></>;
    // 动态句数调整的下限/上限不再手填，由“是否启用”开关统一管理
    if (HIDDEN_CONFIG_KEYS.has(key)) return <></>;
    // 动态句数调整：仅暴露“是否启用”开关（开→存 上限/4，关→存 0；后端 _coerce_bool 照常识别启用/禁用）
    if (key === DYNAMIC_NUM_KEY) {
      const enabled = !!getValue(DYNAMIC_NUM_KEY);
      const maxVal = Number(getValue(DYNAMIC_MAX_KEY)) || 64;
      const defaultPerRequest = Math.floor(maxVal / 4);
      return (
        <div class="pc-row">
          <div class="pc-row-label">
            <span class="pc-label">是否启用动态句数调整</span>
            <div class="pc-key-hint">
              <code class="pc-key">{key}</code>
            </div>
            <p class="pc-desc">
              启用后初始每次请求句数 = 上限/4（当前 {defaultPerRequest}
              ），并根据解析错误自动调节，无需手填。
            </p>
          </div>
          <div class="pc-row-control">
            <label class="settings-toggle">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) =>
                  setValue(
                    DYNAMIC_NUM_KEY,
                    e.currentTarget.checked ? defaultPerRequest : 0,
                  )
                }
              />
              <span class="settings-toggle-knob" />
            </label>
          </div>
        </div>
      );
    }
    // 文件格式插件：从 plugins 目录读取文件名作为下拉选项
    if (key === "plugin.filePlugin") {
      const currentVal = String(getValue(key) ?? "");
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
            <select
              class="field__input pc-input pc-select"
              value={currentVal}
              onChange={(e) => setValue(key, e.currentTarget.value)}
            >
              <option value="">（无）</option>
              <For each={filePlugins()}>
                {(name) => <option value={name}>{name}</option>}
              </For>
            </select>
          </div>
        </div>
      );
    }
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
  }

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

  /**
   * 读取点分键对应的值。config 中 common / dictionary 等段实际使用「扁平点分键」
   * （如 common["gpt.numPerRequestTranslate"]），而非嵌套对象。遍历时若当前段不是
   * 精确子键，则尝试把剩余路径拼成扁平点分键命中（如 common.gpt.numPerRequestTranslate
   * → common["gpt.numPerRequestTranslate"]），从而兼容两类写法。
   */
  function getValue(key: string): ConfigValue | "" {
    const parts = key.split(".");
    let v: ConfigValue = config();
    for (let i = 0; i < parts.length; i++) {
      if (v == null || typeof v !== "object" || Array.isArray(v)) return "";
      const node = v as Record<string, ConfigValue>;
      const seg = parts[i];
      if (seg in node) {
        v = node[seg];
      } else {
        // 兼容扁平点分键：剩余路径拼成单个键（如 gpt.numPerRequestTranslate）
        const flat = parts.slice(i).join(".");
        if (flat in node) {
          v = node[flat];
          break;
        }
        return "";
      }
    }
    return v !== undefined && v !== null ? v : "";
  }

  /**
   * 写入点分键。兼容扁平点分键：遍历到某段时，若剩余路径拼成的扁平键已存在于当前层，
   * 则直接写入该扁平键（如 common.gpt.numPerRequestTranslate → common["gpt.numPerRequestTranslate"]），
   * 避免误建嵌套对象导致原扁平键失联、值写不到正确位置。
   */
  function setValue(key: string, value: ConfigValue) {
    const parts = key.split(".");
    setConfig((prev) => {
      const next = { ...prev };
      let cur: Record<string, ConfigValue> = next;
      for (let i = 0; i < parts.length - 1; i++) {
        const seg = parts[i];
        const flat = parts.slice(i).join(".");
        if (cur[seg] != null && typeof cur[seg] === "object" && !Array.isArray(cur[seg])) {
          cur[seg] = { ...(cur[seg] as Record<string, ConfigValue>) };
          cur = cur[seg] as Record<string, ConfigValue>;
        } else if (flat in cur) {
          // 命中扁平点分键，直接整体写入剩余路径对应的键
          cur[flat] = value;
          return next;
        } else {
          cur[seg] = {};
          cur = cur[seg] as Record<string, ConfigValue>;
        }
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
      // 清理旧版本误写到 common.gpt.translation_guideline 的残留键：
      // 后端只读 gpt.translation_guideline，残留键会在通用配置列表里显示成多余文本框。
      const cleaned = { ...config() } as Record<string, ConfigValue>;
      const commonObj = cleaned.common as Record<string, ConfigValue> | undefined;
      if (commonObj && typeof commonObj === "object" && "gpt.translation_guideline" in commonObj) {
        const commonClean = { ...commonObj };
        delete commonClean["gpt.translation_guideline"];
        cleaned.common = commonClean;
        setConfig(cleaned);
      }
      await updateProjectConfig(pid()!, {
        config: cleaned,
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
          {/* 游戏外部信息（ForGlobalPrompt 外部信息接口）：多行文本，绑定 externals.gameInfo，
              由页面底部「保存配置」统一写回 config.yaml，流水线/独立后端均会读取 */}
          <div class="pc-external-info">
            <div class="pc-row-label">
              <span class="pc-label">游戏外部信息</span>
              <div class="pc-key-hint">
                <code class="pc-key">externals.gameInfo</code>
              </div>
              <p class="pc-desc">
                提供给「全局分析（ForGlobalPrompt）」的外部背景资料——游戏名称、简介、制作公司、世界观、已有角色等自由文本。
                生成全局提示词时会注入 [ExternalInfo] 占位符，帮助模型产出更准确的游戏概况与角色档案。
                留空则提示词显示「（未提供外部信息）」。
              </p>
            </div>
            <textarea
              class="pc-external-info__textarea"
              value={String(getValue("externals.gameInfo") ?? "")}
              onInput={(e) => setValue("externals.gameInfo", e.currentTarget.value)}
              placeholder={"例如：\n游戏名：星之轨迹\n类型：科幻 ADV\n制作：某社\n简介：……"}
              spellcheck={false}
            />
          </div>

          {/* 翻译规范文件：顶部固定卡片，无条件渲染（不再依赖扁平键列表里是否存在该键），
              修复「旧键 common.gpt.translation_guideline 被隐藏、新键 gpt.translation_guideline 不存在
              → 配置项整行消失」的 bug。选项来自 translation_guidelines 目录文件名。 */}
          <div class="pc-external-info">
            <div class="pc-row-label">
              <span class="pc-label">翻译规范文件</span>
              <div class="pc-key-hint">
                <code class="pc-key">{GUIDELINE_KEY}</code>
              </div>
              <p class="pc-desc">
                位于 translation_guidelines 目录，影响文风与措辞。运行对应后端或完整流水线时，
                该文件内容会被注入到翻译提示词中。
              </p>
            </div>
            <div class="pc-row-control" style="margin-top: 4px">
              <select
                class="field__input pc-input pc-select"
                value={guidelineCurrent()}
                onChange={(e) => setValue(GUIDELINE_KEY, e.currentTarget.value)}
              >
                <Show when={guidelines().length === 0 && !guidelineCurrent()}>
                  <option value="">（未找到翻译规范文件）</option>
                </Show>
                {/* 当前值不在目录下（如历史遗留/自定义），保留一个可选项避免丢失 */}
                <Show when={guidelineCurrent() && !guidelines().includes(guidelineCurrent())}>
                  <option value={guidelineCurrent()}>{guidelineCurrent()}</option>
                </Show>
                <For each={guidelines()}>
                  {(g) => <option value={g}>{g}</option>}
                </For>
              </select>
            </div>
          </div>

          {/* 「仅在无批次级元数据时启用」：这些项在有批次级元数据（ForBatchMetaData）时
              被 force_static 等逻辑强制忽略，仅无元数据的退化分支生效 */}
          <Show when={conditionalItems().length > 0}>
            <div class="pc-group">
              <h3 class="pc-group-title">仅在无批次级元数据时启用</h3>
              <p class="pc-desc">
                以下参数仅在「无批次级元数据」的退化分支生效；使用批次级元数据（ForBatchMetaData）时将被强制忽略，由批次划分逻辑接管。
              </p>
              <For each={conditionalItems()}>
                {(item) => renderFieldRow(item)}
              </For>
            </div>
          </Show>

          <div class="pc-field-list">
            <For each={groupedKeys()}>
              {([group, items]) => (
                <div class="pc-group">
                  <h3 class="pc-group-title">{GROUP_LABELS[group] || group}</h3>
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
                    {(item) => renderFieldRow(item)}
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
