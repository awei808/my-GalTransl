import { createSignal, For, Show, Switch, Match, createEffect, onCleanup } from "solid-js";
import { appState, setAppState, getActiveConfigFileName, navigateTo } from "../../stores/appStore";
import { toast } from "../../stores/toastStore";
import { getErrorMessage } from "../../lib/errors";
import { runPageAutosave } from "../../lib/usePageAutosave";
import { fetchProjectConfig, updateProjectConfig, fetchConfigSchema } from "../../lib/api/project";
import { fetchTranslationGuidelines, fetchPlugins, fetchProblemTypes } from "../../lib/api/general";
import type { ProblemTypeInfo } from "../../lib/api/types";
import { classifyKeys } from "../../lib/settings-taxonomy";
import type { FixedCardKind } from "../../lib/settings-taxonomy";
import { parseAfterTranslation, validateAfterTranslation } from "../../lib/afterTranslation";
import { AfterTranslationOrderEditor } from "../../components/AfterTranslationOrderEditor";

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
// 注意：后端 CProjectConfig.getKey 只从 common 段读扁平键（见 GalTransl/ConfigHelper.py），
// BaseTranslate 用 getKey("gpt.translation_guideline") 加载规范（见 GalTransl/Backend/BaseTranslate.py），
// 因此此处必须用带 common. 前缀的扁平键，否则选择会被后端忽略。
const GUIDELINE_KEY = "common.gpt.translation_guideline";
const HIDDEN_CONFIG_KEYS = new Set<string>([
  "common.gpt.dynamicNumPerRequestTranslate.min",
  "common.gpt.dynamicNumPerRequestTranslate.max",
  // 改为专用多行「游戏外部信息」输入框（见页面「翻译后端-全局提示词」卡片），不再以单行英文框出现在通用列表
  "externals.gameInfo",
  // 以下为「仅在无批次级元数据时启用」退化分支：已规划弃用，前端一律不显示且不可修改
  "common.gpt.numPerRequestTranslate",
  "common.gpt.dynamicNumPerRequestTranslate",
  "common.splitFile",
  "common.splitFileNum",
  "common.splitFileCrossNum",
  // 以下为程序设置页「日志」板块专属（随项目 config.common 保存，但仅在程序设置页暴露）
  "common.loggingLevel",
  "common.saveLog",
  // gpt.enableBetterTranslation 已废弃，由 gpt.afterTranslation 取代；
  // 旧值经 migrateBetterTranslationKey 迁移（true→afterTranslation=improve）后隐藏，避免遗留项误导用户
  "common.gpt.enableBetterTranslation",
  // 翻译后处理后端：改为「翻译后端-完整流水线」区的数字框专用卡片（有序数组），不入通用列表
  "common.gpt.afterTranslation",
]);
// problemAnalyze 已移回项目设置「问题检测」专用卡片渲染（随保存配置按钮写回 YAML）。
// 此处仅让该键不出现在通用列表里（避免与专用卡片重复），不再整体隐藏。
const LIST_OMITTED_KEYS = new Set<string>([
  "problemAnalyze.problemList",
  // 阈值由「问题检测」固定卡片内专属输入框渲染，避免与通用列表重复显示
  "problemAnalyze.avgSentenceLengthThreshold",
  "problemAnalyze.avgSentenceLengthThresholdH",
]);
// 暂不在前端暴露的键：contextNum 改用多轮对话完整保留上下文，无需切分，故移除前端入口；
// gpt.semCheck.* 已随 ForSemCheck 跟随主翻译 profile 而废弃（旧配置残留键一并隐藏）
const REMOVED_CONFIG_KEYS = new Set<string>([
  "common.gpt.contextNum",
  "common.gpt.semCheck.enabled",
  "common.gpt.semCheck.endpoint",
  "common.gpt.semCheck.modelName",
  "common.gpt.semCheck.apiKey",
  "common.gpt.semCheck.apiTimeout",
  "common.gpt.semCheck.stream",
  "common.gpt.semCheck.provider",
]);
// 注：「仅在无批次级元数据时启用」的退化分支键（numPerRequestTranslate / splitFile* /
// dynamicNumPerRequestTranslate*）已整体弃用，移入 HIDDEN_CONFIG_KEYS 不再显示；
// 故 CONDITIONAL_SECTION_KEYS 与 conditionalItems 板块已移除。
const FIELD_UI: Record<string, FieldUI> = {
  "common.gpt.swapFixToCurrent": {
    label: "修复轮结果自动交换当前译文",
    hint: "开启后，修复轮（brfix/jpfix）生成的备选译文会与当前译文交换属性：修复结果直接覆盖当前译文（校对结果优先，否则初译），原译文存入备选译文可在校对页回退。关闭时修复结果仅作为备选译文，需手动交换。",
  },
  "common.gpt.numPerRequestSemCheck": {
    label: "语义检测每批句数",
    hint: "单次语义判定请求发送的句子数，越小越稳但越慢，避免一次发送过多导致质量下降。",
  },
  "internals.pipeline.enableValidate": {
    label: "开启阶段 0：输入数据校验",
    hint: "关闭后跳过输入文件校验，直接进入下一阶段（不建议关闭）。",
  },
  "internals.pipeline.enableCompress": {
    label: "开启阶段 1：文本无损压缩",
    hint: "压缩全文供全局分析使用；关闭后阶段 2（全局分析）因无压缩文本将自动跳过。",
  },
  "internals.pipeline.enableGlobalPrompt": {
    label: "开启阶段 2：全局游戏分析",
    hint: "生成世界观与角色档案；关闭后文件级/批次级元数据与翻译将缺少全局上下文（提示词块为空，仍可继续）。",
  },
  "internals.pipeline.enableGenDic": {
    label: "开启阶段 3：术语表构建",
    hint: "关闭后不生成/不更新 GPT 字典，翻译时无项目术语表。",
  },
  "internals.pipeline.enableFileMeta": {
    label: "开启阶段 4：文件级元数据",
    hint: "关闭后不生成/不更新 FileMetaData.json，翻译时无文件级剧情背景。",
  },
  "internals.pipeline.enableBatchMeta": {
    label: "开启阶段 5：批次级元数据",
    hint: "关闭后不划分翻译区间，翻译按每次请求句数直接分块进行。",
  },
  "internals.pipeline.enableTranslate": {
    label: "开启阶段 6：翻译执行",
    hint: "关闭后流水线只执行前置分析阶段，不进行翻译。",
  },
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
  "backendSpecific.OpenAI-Compatible.apiMaxErrorRate": {
    label: "错误率上限（终止阈值）",
    hint: "累计失败请求占比超过该值即终止整个翻译流程。0 或不填表示不限制。",
  },
  "backendSpecific.OpenAI-Compatible.apiMinIntervalSec": {
    label: "请求最小间隔（秒）",
    hint: "两次 API 请求之间的最小间隔，用于节流。0 或不填表示不限制。",
  },
  "backendSpecific.OpenAI-Compatible.apiMaxRequests": {
    label: "请求次数上限",
    hint: "累计 API 请求次数（含重试）达到上限即终止整个翻译流程。0 或不填表示不限制。",
  },
  "backendSpecific.SakuraLLM.rewriteModelName": {
    label: "自定义模型名（Sakura）",
    hint: "使用 ollama 等本地模型时需修改。仅 Sakura 引擎。",
  },
  "backendSpecific.SakuraLLM.apiMaxErrorRate": {
    label: "错误率上限（终止阈值）",
    hint: "累计失败请求占比超过该值即终止整个翻译流程。0 或不填表示不限制。",
  },
  "backendSpecific.SakuraLLM.apiMinIntervalSec": {
    label: "请求最小间隔（秒）",
    hint: "两次 API 请求之间的最小间隔，用于节流。0 或不填表示不限制。",
  },
  "backendSpecific.SakuraLLM.apiMaxRequests": {
    label: "请求次数上限",
    hint: "累计 API 请求次数（含重试）达到上限即终止整个翻译流程。0 或不填表示不限制。",
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
    hint: "0 表示不限制，用于避免上下文溢出。仅 Sakura 引擎。",
  },
  "internals.pipeline.maxInputChars": {
    label: "全局分析最大字符数（软阈值）",
    hint: "压缩后文本超过该值时仅打印告警、不做截断（无损原则，绝不删行），仅作提示。0 表示不检查。默认 95 万字符。",
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
  "internals.forbatchmeta.min_batch_size": {
    label: "单批最小区间长度",
    hint: "行数小于此值的区间会尽量与相邻区间合并；文件总行数不足时可整文件一批。",
  },
  "internals.forbatchmeta.max_batch_size": {
    label: "单批最大区间长度",
    hint: "行数超过此值的区间会被自动切分为多个批次，避免单次请求过大（建议对齐单次请求句子数上限）。",
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
 * 数组型配置键（值缺失时也按数组渲染为只读，避免误判为枚举下拉）：
 * GenDic 术语表白名单/黑名单为日文词列表，通过直接编辑 config.yaml 维护。
 */
const ARRAY_LIST_KEYS = new Set([
  "internals.gendic.han_allowlist",
  "internals.gendic.ban_words",
]);

/**
 * 这些键不进入「通用配置列表」（在展平阶段直接跳过），改为专用卡片、隐藏行或条件板块：
 * - 后端全局管理前缀：交全局后端配置页维护
 * - HIDDEN_CONFIG_KEYS：旧版本残留/由专用卡片接管的键
 * - REMOVED_CONFIG_KEYS：暂不在前端暴露的键（如 contextNum）
 * - GUIDELINE_KEY：改为「翻译规范文件」固定卡片渲染
 * - 弃用键（numPerRequestTranslate / splitFile* / dynamicNumPerRequestTranslate*）：移入 HIDDEN_CONFIG_KEYS
 */
function isOmittedFromList(key: string): boolean {
  if (key.startsWith(MANAGED_GLOBAL_PREFIX)) return true;
  if (HIDDEN_CONFIG_KEYS.has(key)) return true;
  if (REMOVED_CONFIG_KEYS.has(key)) return true;
  if (LIST_OMITTED_KEYS.has(key)) return true;
  if (key === GUIDELINE_KEY) return true;
  return false;
}

/**
 * 翻译规范键统一为 common 段扁平键 gpt.translation_guideline（后端 CProjectConfig.getKey
 * 只读该位置，见 GalTransl/ConfigHelper.py）。旧版本曾误写顶层 gpt.translation_guideline，
 * 早期前端还会把 common 扁平键迁到顶层——这里统一归一化：取值优先级为
 * common 扁平键 > common.gpt 嵌套 > 顶层 gpt.translation_guideline，
 * 写入 common 扁平键并清理其余位置的残留，保证下拉框与保存结果一致。
 */
function migrateGuidelineKey(obj: Record<string, ConfigValue>): Record<string, ConfigValue> {
  const next: Record<string, ConfigValue> = { ...obj };
  const common = next.common as Record<string, ConfigValue> | undefined;
  const commonNested =
    common &&
    typeof common.gpt === "object" &&
    common.gpt !== null &&
    !Array.isArray(common.gpt)
      ? (common.gpt as Record<string, ConfigValue>)
      : undefined;
  const gpt = next.gpt as Record<string, ConfigValue> | undefined;

  // 取值：common 扁平键优先，其次 common.gpt 嵌套，最后顶层 gpt 段
  let val: ConfigValue | undefined = common?.["gpt.translation_guideline"];
  if (val === undefined || val === null) val = commonNested?.translation_guideline;
  if (val === undefined || val === null) val = gpt?.translation_guideline;
  if (val === undefined || val === null) return next; // 三种位置都无值，无需迁移

  // 写入规范位置：common 段扁平键
  if (common && typeof common === "object") {
    const commonCopy = { ...common };
    commonCopy["gpt.translation_guideline"] = val;
    // 清理 common.gpt 嵌套残留（嵌套只剩 translation_guideline 时删除嵌套对象）
    if (commonNested) {
      const nestedCopy = { ...commonNested };
      delete nestedCopy.translation_guideline;
      if (Object.keys(nestedCopy).length === 0) delete commonCopy.gpt;
      else commonCopy.gpt = nestedCopy;
    }
    next.common = commonCopy;
  }

  // 清理顶层 gpt 段残留（仅删 translation_guideline，其余键保留；段空则整体删除）
  if (gpt && typeof gpt === "object") {
    if ("translation_guideline" in gpt) {
      const gptCopy = { ...gpt };
      delete gptCopy.translation_guideline;
      if (Object.keys(gptCopy).length === 0) delete next.gpt;
      else next.gpt = gptCopy;
    }
  }
  return next;
}

/**
 * gpt.enableBetterTranslation 已废弃，由 gpt.afterTranslation 取代。
 * 加载/保存时把旧值迁移：enableBetterTranslation=true 且无 afterTranslation 时，写入
 * common 段扁平键 gpt.afterTranslation=["improve"]（与后端 _resolve_after_translation_order
 * 兼容回退一致，值为有序数组），并清理三处旧键残留（common 扁平键 / common.gpt 嵌套 / 顶层 gpt），
 * 避免遗留项误导用户。
 */
function migrateBetterTranslationKey(obj: Record<string, ConfigValue>): Record<string, ConfigValue> {
  const next: Record<string, ConfigValue> = { ...obj };
  const common = next.common as Record<string, ConfigValue> | undefined;
  const commonNested =
    common &&
    typeof common.gpt === "object" &&
    common.gpt !== null &&
    !Array.isArray(common.gpt)
      ? (common.gpt as Record<string, ConfigValue>)
      : undefined;
  const gpt = next.gpt as Record<string, ConfigValue> | undefined;

  // 取值优先级：common 扁平键 > common.gpt 嵌套 > 顶层 gpt（与后端 getKey 一致）
  let oldVal: ConfigValue | undefined = common?.["gpt.enableBetterTranslation"];
  if (oldVal === undefined || oldVal === null) oldVal = commonNested?.enableBetterTranslation;
  if (oldVal === undefined || oldVal === null) oldVal = gpt?.enableBetterTranslation;
  // 旧值非真（false / 缺省 / 其它），无需迁移
  const enabled = oldVal === true || oldVal === "true" || oldVal === 1 || oldVal === "1";

  // afterTranslation 是否已设置（任意位置有值即视为已显式配置，勿覆盖）
  const hasNew =
    (common && common["gpt.afterTranslation"] !== undefined && common["gpt.afterTranslation"] !== null) ||
    (commonNested && commonNested.afterTranslation !== undefined) ||
    (gpt && gpt.afterTranslation !== undefined);

  if (!enabled) {
    // 旧键残留清理（即使为 false，避免遗留废弃项）
    cleanupBetterTranslation(next, common, commonNested, gpt, false);
    // 补全默认值：老项目若从未设置过 afterTranslation，按出厂默认补空数组，
    // 否则该配置项不会出现在专用卡片（walk 只遍历 config 中存在的键）。
    if (!hasNew && common && typeof common === "object") {
      const commonCopy = { ...common };
      commonCopy["gpt.afterTranslation"] = [];
      next.common = commonCopy;
    }
    return next;
  }

  // enableBetterTranslation=true 且无 afterTranslation：按兼容语义迁移为 [improve]
  if (!hasNew && common && typeof common === "object") {
    const commonCopy = { ...common };
    commonCopy["gpt.afterTranslation"] = ["improve"];
    next.common = commonCopy;
  }
  cleanupBetterTranslation(next, common, commonNested, gpt, true);
  return next;
}

/** 删除三处 enableBetterTranslation 残留键 */
function cleanupBetterTranslation(
  next: Record<string, ConfigValue>,
  common: Record<string, ConfigValue> | undefined,
  commonNested: Record<string, ConfigValue> | undefined,
  gpt: Record<string, ConfigValue> | undefined,
  force: boolean,
) {
  if (!force && next.common && typeof next.common === "object") {
    const commonCopy = { ...(next.common as Record<string, ConfigValue>) };
    delete commonCopy["gpt.enableBetterTranslation"];
    next.common = commonCopy;
  }
  if (force) {
    if (common && typeof common === "object") {
      const commonCopy = { ...common };
      delete commonCopy["gpt.enableBetterTranslation"];
      if (commonNested) {
        const nestedCopy = { ...commonNested };
        delete nestedCopy.enableBetterTranslation;
        if (Object.keys(nestedCopy).length === 0) delete commonCopy.gpt;
        else commonCopy.gpt = nestedCopy;
      }
      next.common = commonCopy;
    }
    if (gpt && typeof gpt === "object") {
      const gptCopy = { ...gpt };
      delete gptCopy.enableBetterTranslation;
      if (Object.keys(gptCopy).length === 0) delete next.gpt;
      else next.gpt = gptCopy;
    }
  }
}

/** 枚举关键字的友好显示（仅用于混合/枚举控件的选项文案） */
const KEYWORD_LABELS: Record<string, string> = {
  auto: "自动适应 (auto)",
  none: "无（不追加）",
  improve: "改进轮",
  brfix: "换行修复",
  jpfix: "残留日文修复",
  semcheck: "语义差异检测",
  "improve+brfix": "改进轮 + 换行修复（按顺序）",
  "improve+jpfix": "改进轮 + 残留日文修复（按顺序）",
  "brfix+jpfix": "换行修复 + 残留日文修复（按顺序）",
  "improve+brfix+jpfix": "改进轮 + 换行修复 + 残留日文修复（按顺序）",
  "improve+semcheck": "改进轮 + 语义差异检测（按顺序）",
  "improve+brfix+semcheck": "改进轮 + 换行修复 + 语义差异检测（按顺序）",
  "improve+jpfix+semcheck": "改进轮 + 残留日文修复 + 语义差异检测（按顺序）",
  "improve+brfix+jpfix+semcheck": "改进轮 + 换行修复 + 残留日文修复 + 语义差异检测（按顺序）",
  // 剧情路线图结构类型（与向导 PLOT_STRUCTURE_TYPES 的 value 一致）
  线性: "线性（链）",
  树: "树（树状分支）",
  "有向无环图": "有向无环图（DAG）",
  "有向有环图": "有向有环图（含循环）",
  混合: "混合",
};

// 待实现/待验证的配置项：设置页渲染 TODO 徽标并禁用编辑（防止误改），
// 功能落地并验证通过后移除对应条目。
const TODO_CONFIG_KEYS: Record<string, string> = {};

export function ProjectConfigPage() {
  const [config, setConfig] = createSignal<Record<string, ConfigValue>>({});
  const [schemaDesc, setSchemaDesc] = createSignal<Record<string, string>>({});
  const [loading, setLoading] = createSignal(true);
  const [saving, setSaving] = createSignal(false);
  // 是否有未保存的配置改动（setValue 时置 true；手动保存成功后置 false）
  const [dirty, setDirty] = createSignal(false);
  // 编辑版本号：setValue 时递增；保存成功仅当期间无新编辑才清 dirty，
  // 防止「保存请求飞行中新增的编辑」被误判为已保存而静默丢失
  let editVersion = 0;
  // 翻译规范文件下拉选项（translation_guidelines 目录下的文件名）
  const [guidelines, setGuidelines] = createSignal<string[]>([]);
  // 翻译规范列表是否已加载完成（加载中/失败均置 true；用于避免下拉在 options 未就绪前渲染，
  // 从而防止 <select> 因 value 匹配不到异步 options 而回退显示列表第一项 Basic.md）
  const [guidelinesLoaded, setGuidelinesLoaded] = createSignal(false);
  // 文件格式插件下拉选项（plugins 目录下 type 为 file 的插件名）
  const [filePlugins, setFilePlugins] = createSignal<string[]>([]);
  // 当前翻译规范值（响应式读取，供固定卡片展示）
  const guidelineCurrent = () => String(getValue(GUIDELINE_KEY) ?? "");

  // ── 大分组折叠状态（localStorage 持久化，版本化 key 便于后续增删分区自动失效） ──
  const PC_COLLAPSE_STORAGE_KEY = "galtransl.project-config.collapse.v1";

  const [collapsedGroups, setCollapsedGroups] = createSignal<Set<string>>(new Set<string>());

  function toggleGroup(title: string) {
    const next = new Set(collapsedGroups());
    if (next.has(title)) next.delete(title);
    else next.add(title);
    setCollapsedGroups(next);
    try {
      localStorage.setItem(PC_COLLAPSE_STORAGE_KEY, JSON.stringify([...next]));
    } catch {
      console.warn("[ProjectConfig] 保存折叠状态失败");
    }
  }

  function expandGroup(title: string) {
    if (!collapsedGroups().has(title)) return;
    const next = new Set(collapsedGroups());
    next.delete(title);
    setCollapsedGroups(next);
    try {
      localStorage.setItem(PC_COLLAPSE_STORAGE_KEY, JSON.stringify([...next]));
    } catch {
      /* ignore */
    }
  }

  // 从 localStorage 加载折叠状态（版本化 key）
  try {
    const pcRaw = localStorage.getItem(PC_COLLAPSE_STORAGE_KEY);
    if (pcRaw) {
      const pcParsed = JSON.parse(pcRaw);
      if (Array.isArray(pcParsed)) {
        const titles = pcParsed.filter((v): v is string => typeof v === "string");
        setCollapsedGroups(new Set<string>(titles));
      }
    }
  } catch {
    console.warn("[ProjectConfig] 读取折叠状态失败，重置为全展开");
  }

  // ── 问题检测（problemAnalyze）：已移回项目设置，随「保存配置」按钮统一写回 YAML ──
  const [problemTypes, setProblemTypes] = createSignal<ProblemTypeInfo[]>([]);
  const [enabledProblemTypes, setEnabledProblemTypes] = createSignal<string[]>([]);
  const [avgThreshold, setAvgThreshold] = createSignal<number>(17);
  const [hAvgThreshold, setHAvgThreshold] = createSignal<number>(24);
  const [attrMaxLen, setAttrMaxLen] = createSignal<number>(10);
  const [advMaxLen, setAdvMaxLen] = createSignal<number>(12);
  const [problemTypesLoading, setProblemTypesLoading] = createSignal(false);

  function toggleProblemType(name: string) {
    const current = enabledProblemTypes();
    const next = current.includes(name)
      ? current.filter((n) => n !== name)
      : [...current, name];
    setEnabledProblemTypes(next);
    setValue("problemAnalyze.problemList", next);
  }

  function setAvgThresholdValue(v: number) {
    setAvgThreshold(v);
    setValue("problemAnalyze.avgSentenceLengthThreshold", v);
  }

  function setHAvgThresholdValue(v: number) {
    setHAvgThreshold(v);
    setValue("problemAnalyze.avgSentenceLengthThresholdH", v);
  }

  function setAttrMaxLenValue(v: number) {
    setAttrMaxLen(v);
    setValue("problemAnalyze.attributiveMaxLength", v);
  }

  function setAdvMaxLenValue(v: number) {
    setAdvMaxLen(v);
    setValue("problemAnalyze.adverbialMaxLength", v);
  }

  // 当前项目/配置名切换后，从后端拉取候选列表并同步当前勾选与阈值
  createEffect(() => {
    if (!pid()) {
      setProblemTypes([]);
      return;
    }
    if (!appState.configNameDetecting) void loadProblemTypes(pid()!, getActiveConfigFileName());
  });

  async function loadProblemTypes(projectId: string, configFileName: string) {
    setProblemTypesLoading(true);
    try {
      const [types, cfg] = await Promise.all([
        fetchProblemTypes(),
        fetchProjectConfig(projectId, configFileName),
      ]);
      setProblemTypes(types);
      const problemAnalyze =
        ((cfg.config ?? {}) as Record<string, unknown>)["problemAnalyze"] as
          | Record<string, unknown>
          | undefined;
      const section = problemAnalyze ?? {};
      const rawList = section["problemList"];
      let list = Array.isArray(rawList)
        ? rawList.map((x) => String(x)).filter((x) => x)
        : [];
      if (list.length === 0 && !("problemList" in section)) {
        const rawGpt35 = section["GPT35"];
        if (Array.isArray(rawGpt35)) {
          list = rawGpt35.map((x) => String(x)).filter((x) => x);
        }
      }
      // 旧项目 config 可能仍写「h场景用词不当」，映射到新类型名「用词不当」
      list = list.map((x) => (x === "h场景用词不当" ? "用词不当" : x));
      setEnabledProblemTypes(list);
      const rawThreshold = section["avgSentenceLengthThreshold"];
      setAvgThreshold(
        typeof rawThreshold === "number" && Number.isFinite(rawThreshold) ? rawThreshold : 17
      );
      const rawHThreshold = section["avgSentenceLengthThresholdH"];
      setHAvgThreshold(
        typeof rawHThreshold === "number" && Number.isFinite(rawHThreshold) ? rawHThreshold : 24
      );
      const rawAttr = section["attributiveMaxLength"];
      setAttrMaxLen(
        typeof rawAttr === "number" && Number.isFinite(rawAttr) ? rawAttr : 10
      );
      const rawAdv = section["adverbialMaxLength"];
      setAdvMaxLen(
        typeof rawAdv === "number" && Number.isFinite(rawAdv) ? rawAdv : 12
      );
    } catch {
      setProblemTypes([]);
    } finally {
      setProblemTypesLoading(false);
    }
  }

  const pid = () => appState.activeProjectId;
  // 切页自动保存用：挂载时刻的项目 id 与配置名快照（卸载时全局状态可能已切换到别的项目/已关闭项目）
  const pidSnapshot = pid();
  const [configFileNameSnapshot, setConfigFileNameSnapshot] = createSignal(
    getActiveConfigFileName(),
  );

  // 等真实配置名探测完成后再加载，避免用回退名 config.yaml 提前请求导致 404
  createEffect(() => {
    if (!pid()) {
      // 无打开项目时不发请求，且需主动取消 loading，否则页面会永久卡在“加载中…”
      setLoading(false);
      return;
    }
    if (!appState.configNameDetecting) loadData();
  });

  // 处理 ActivityBar 快捷按钮发起的滚动定位（等配置加载完成、问题检测卡片内容稳定后执行并清除标记）
  createEffect(() => {
    const target = appState.settingsScrollTarget;
    // 仅处理已知锚点；pc-top 为页面顶部，pc-group-problem-analyze 为问题检测分组
    if (!target || (target !== "pc-group-problem-analyze" && target !== "pc-top")) return;
    // 问题检测分组需等内容加载稳定再滚动；页面顶部无需等待问题类型
    const waitProblemTypes = target === "pc-group-problem-analyze";
    if (loading() || (waitProblemTypes && problemTypesLoading())) return;
    requestAnimationFrame(() => {
      const el = document.getElementById(target);
      // 目标元素不存在时也清除 target，避免 settingsScrollTarget 悬挂
      if (!el) {
        setAppState("settingsScrollTarget", null);
        return;
      }
      // 目标分组处于折叠时，先展开（SolidJS 响应式更新 DOM 需下一帧），下一帧再滚动
      const groupTitle = el.dataset.title;
      if (groupTitle && collapsedGroups().has(groupTitle)) {
        expandGroup(groupTitle);
        requestAnimationFrame(() => {
          el.scrollIntoView({ block: "start" });
          setAppState("settingsScrollTarget", null);
        });
      } else {
        el.scrollIntoView({ block: "start" });
        setAppState("settingsScrollTarget", null);
      }
    });
  });

  async function loadData() {
    if (!pid()) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      // 记录本次加载所用的配置名，供切页自动保存快照使用（卸载时 getActiveConfigFileName() 可能已指向别的项目）
      const cfgName = getActiveConfigFileName();
      setConfigFileNameSnapshot(cfgName);
      const [cfg, sch] = await Promise.all([
        fetchProjectConfig(pid()!, cfgName),
        fetchConfigSchema(pid()!).catch(() => ({ parameters: {} })),
      ]);
      setConfig({
        ...migrateBetterTranslationKey(
          migrateGuidelineKey(cfg.config as Record<string, ConfigValue>),
        ),
      });
      setSchemaDesc(sch?.parameters || {});
      // 翻译规范文件下拉选项（与配置加载并行，失败不阻塞主配置）
      fetchTranslationGuidelines()
        .then((list) => setGuidelines(list))
        .catch(() => setGuidelines([]))
        .finally(() => setGuidelinesLoaded(true));
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

  /**
   * 将嵌套配置按「统一分类表（settings-taxonomy）」归类，而非按物理前缀。
   * 结构签名缓存：编辑字段「值」不应触发列表重建。
   * 仅当「键集合 / 分组」变化时返回新数组；否则复用同一引用，
   * 否则 <For> 会在每次 setValue（按键）时按引用判定为「全新项」而重建所有 <input>，
   * 导致输入框失焦、输入法（IME）中断。元组内携带的 value 实际未被 JSX 使用
   * （值始终由响应式 getValue(key) 读取），故缓存过期值亦无副作用。
   */
  let _groupedSig = "";
  let _groupedCache: {
    section: import("../../lib/settings-taxonomy").TaxonomySection;
    directItems: [string, ConfigValue, string][];
    subsections: {
      subsection: import("../../lib/settings-taxonomy").TaxonomySubsection;
      items: [string, ConfigValue, string][];
    }[];
    fixedCards: import("../../lib/settings-taxonomy").FixedCardKind[];
  }[] | null = null;

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

  const classifiedGroups = () => {
    const c = config();
    const sig = keySignature(c);
    if (_groupedSig === sig && _groupedCache) return _groupedCache;

    // 1) 展平为点分键（过滤掉 omitt / conditional），并保留 dtype 供 renderFieldRow
    const flatItemByKey = new Map<string, [string, ConfigValue, string]>();

    function walk(obj: Record<string, ConfigValue>, prefix: string) {
      for (const [k, v] of Object.entries(obj)) {
        const key = prefix ? `${prefix}.${k}` : k;
        // 这些键不进入通用列表（专用卡片/隐藏行/已弃用键），直接跳过
        if (isOmittedFromList(key)) continue;
        if (v !== null && typeof v === "object" && !Array.isArray(v)) {
          walk(v as Record<string, ConfigValue>, key);
        } else {
          // 区分值类型以便渲染时选择展示方式
          let dtype = "scalar"; // string | number | boolean
          if (Array.isArray(v) || ARRAY_LIST_KEYS.has(key)) {
            dtype =
              Array.isArray(v) && v.length > 0 && typeof v[0] === "object" && v[0] !== null
                ? "object-array" // 如 tokens: [{token,endpoint,...}]
                : "array"; // 如 [1,2,3] 或 ["a","b"]（未配置时也按数组展示，避免误判为枚举）
          }
          flatItemByKey.set(key, [key, v, dtype]);
        }
      }
    }
    walk(c, "");

    // 2) 按 taxonomy 显式归类（未声明键自动归入「其他设置」并告警）
    const { groups, unclassified } = classifyKeys([...flatItemByKey.keys()]);

    // 3) 把 taxonomy 的 key 列表映射回展平元组
    const cache = groups.map((g) => ({
      section: g.section,
      directItems: g.directKeys
        .map((k) => flatItemByKey.get(k))
        .filter((x): x is [string, ConfigValue, string] => !!x),
      subsections: g.subsections.map((sub) => ({
        subsection: sub.subsection,
        items: sub.keys
          .map((k) => flatItemByKey.get(k))
          .filter((x): x is [string, ConfigValue, string] => !!x),
      })),
      fixedCards: g.fixedCards,
    }));
    void unclassified;
    _groupedCache = cache;
    _groupedSig = sig;
    return _groupedCache;
  };

  /** 单个配置项的渲染（通用列表） */
  function renderFieldRow(item: [string, ConfigValue, string]) {
    const [key, , dtype] = item;
    // 待实现/待验证的配置项：渲染 TODO 徽标并禁用编辑（功能落地后从 TODO_CONFIG_KEYS 移除）
    const effectiveTodoMsg = TODO_CONFIG_KEYS[key];
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
      <div class="pc-row" classList={{ "pc-row--todo": !!effectiveTodoMsg }}>
        <div class="pc-row-label">
          <span class="pc-label">{getFieldLabel(key)}</span>
          <Show when={effectiveTodoMsg}>
            <span class="pc-todo-badge">TODO</span>
          </Show>
          <div class="pc-key-hint">
            <code class="pc-key">{key}</code>
          </div>
          <Show when={getFieldHint(key)}>
            <p class="pc-desc">{getFieldHint(key)}</p>
          </Show>
          <Show when={effectiveTodoMsg}>
            <p class="pc-desc pc-desc--todo">⚠ {effectiveTodoMsg}，暂时不可修改</p>
          </Show>
        </div>
        <div class="pc-row-control">
          <fieldset disabled={!!effectiveTodoMsg} class="pc-fieldset">
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
                <Show
                  when={key === "internals.plotroute.userOutline"}
                  fallback={
                    <input
                      class="field__input pc-input"
                      type="text"
                      value={String(val ?? "")}
                      onInput={(e) => setValue(key, e.currentTarget.value)}
                    />
                  }
                >
                  {/* 剧情大纲：多行输入（Enter 换行，方案与游戏外部信息一致）
                      注意：value 必须直接调 getValue(key) 读取响应式值，不能用外层 const val 快照——
                      通用列表项经带值缓存签名的 <For> 包裹，renderFieldRow 仅挂载时执行一次，
                      const val 捕获的是首次渲染快照，setValue 后不刷新会导致 onKeyDown 插入的换行不可见。 */}
                  <textarea
                    class="field__input pc-input pc-textarea-multiline"
                    rows={5}
                    value={String(getValue(key) ?? "")}
                    onInput={(e) => setValue(key, e.currentTarget.value)}
                    onKeyDown={(e) => {
                      if (e.key !== "Enter" || e.isComposing) return;
                      e.preventDefault();
                      const ta = e.currentTarget as HTMLTextAreaElement;
                      const pos = ta.selectionStart;
                      const newVal =
                        ta.value.slice(0, pos) + "\n" + ta.value.slice(ta.selectionEnd);
                      setValue(key, newVal);
                      requestAnimationFrame(() => {
                        ta.selectionStart = ta.selectionEnd = pos + 1;
                      });
                    }}
                    spellcheck={false}
                  />
                </Show>
              </Match>
            </Switch>
          </Show>
          </fieldset>
        </div>
      </div>
    );
  }

  /** 固定卡片渲染（由 taxonomy section.fixedCards 驱动）：游戏外部信息 / 翻译规范文件 / 问题检测 */
  function renderFixedCard(kind: FixedCardKind) {
    if (kind === "externalInfo") {
      return (
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
            rows="6"
            value={String(getValue("externals.gameInfo") ?? "")}
            onInput={(e) => setValue("externals.gameInfo", e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key !== "Enter") return;
              e.preventDefault();
              const ta = e.currentTarget as HTMLTextAreaElement;
              const pos = ta.selectionStart;
              const newVal = ta.value.slice(0, pos) + "\n" + ta.value.slice(ta.selectionEnd);
              setValue("externals.gameInfo", newVal);
              requestAnimationFrame(() => {
                ta.selectionStart = ta.selectionEnd = pos + 1;
              });
            }}
            placeholder={"例如：\n游戏名：星之轨迹\n类型：科幻 ADV\n制作：某社\n简介：……"}
            spellcheck={false}
          />
        </div>
      );
    }
    if (kind === "translationGuideline") {
      return (
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
            {/* 列表加载完成前不渲染 select，避免 value 匹配不到异步 options 而回退显示列表第一项(Basic.md) */}
            <Show
              when={guidelinesLoaded()}
              fallback={
                <input
                  class="field__input pc-input"
                  type="text"
                  value={guidelineCurrent()}
                  readOnly
                  title="翻译规范文件列表加载中…"
                />
              }
            >
              <select
                class="field__input pc-input pc-select"
                value={guidelineCurrent()}
                onChange={(e) => setValue(GUIDELINE_KEY, e.currentTarget.value)}
              >
                {/* 常驻占位项：当前值无法读取或列表为空时稳定显示，绝不静默回退到列表第一项 */}
                <option value="">（未选择翻译规范）</option>
                {/* 当前值不在文件列表时动态插入，保证首帧即匹配，避免时序回退 */}
                <Show when={guidelineCurrent() && !guidelines().includes(guidelineCurrent())}>
                  <option value={guidelineCurrent()}>{guidelineCurrent()}</option>
                </Show>
                <For each={guidelines()}>
                  {(g) => <option value={g}>{g}</option>}
                </For>
              </select>
            </Show>
          </div>
        </div>
      );
    }
    // kind === "afterTranslation"：修复和改进译文（阶段 7）后处理顺序
    if (kind === "afterTranslation") {
      const order = () => parseAfterTranslation(getValue("common.gpt.afterTranslation"));
      return (
        <div class="pc-external-info">
          <div class="pc-row-label">
            <span class="pc-label">翻译后处理后端（阶段 7 执行顺序）</span>
            <div class="pc-key-hint">
              <code class="pc-key">common.gpt.afterTranslation</code>
            </div>
            <p class="pc-desc">
              完整流水线翻译完成后（阶段 7），按数字顺序逐文件执行修复/改进后端；留空则不执行。
              数字几就代表第几步执行，保存为有序数组（数组顺序即执行顺序）。关闭「阶段 7：
              修复和改进译文」开关后此处不生效。也可直接在后端下拉中选择
              ForImproveTranslation / ForBRStation / ForJPResidue / ForBanWordFix / ForSemCheck
              / ForSemCheckAgain 对已翻译文件手动执行（后者为命中句二次复核）。
            </p>
          </div>
          <AfterTranslationOrderEditor
            value={order()}
            onChange={(o) => setValue("common.gpt.afterTranslation", o)}
          />
        </div>
      );
    }
    // kind === "problemAnalyze"
    return (
      <div>
        <Show
          when={!problemTypesLoading()}
          fallback={<p class="pc-status">加载问题类型中…</p>}
        >
          <Show
            when={problemTypes().length > 0}
            fallback={<p class="pc-status">未连接到后端，无法获取问题类型列表。</p>}
          >
            <For each={problemTypes()}>
              {(pt) => (
                <div class="pc-row">
                  <div class="pc-row-label">
                    <label class="pc-label" for={`problem-${pt.name}`}>
                      {pt.name}
                    </label>
                    <p class="pc-desc">{pt.description}</p>
                  </div>
                  <div class="pc-row-control">
                    <label class="settings-toggle">
                      <input
                        id={`problem-${pt.name}`}
                        type="checkbox"
                        checked={enabledProblemTypes().includes(pt.name)}
                        onChange={() => toggleProblemType(pt.name)}
                      />
                      <span class="settings-toggle-knob" />
                    </label>
                  </div>
                </div>
              )}
            </For>
          </Show>
        </Show>
        <div class="pc-row">
          <div class="pc-row-label">
            <label class="pc-label" for="avg-sentence-length-threshold">
              平均分句长度阈值（长句丢失换行）
            </label>
            <p class="pc-desc">译文平均分句长度超过该值即报「长句丢失换行」，建议 15~25。</p>
          </div>
          <div class="pc-row-control">
            <input
              id="avg-sentence-length-threshold"
              class="field__input pc-input pc-input--num"
              type="number"
              min={10}
              max={50}
              step={1}
              value={avgThreshold()}
              onChange={(e) => {
                const raw = Number((e.target as HTMLInputElement).value);
                setAvgThresholdValue(Number.isFinite(raw) ? raw : 17);
              }}
            />
          </div>
        </div>
        <div class="pc-row">
          <div class="pc-row-label">
            <label class="pc-label" for="avg-sentence-length-threshold-h">
              H 场景平均分句长度阈值（长句丢失换行）
            </label>
            <p class="pc-desc">H 剧情区间内译文平均分句长度超过该值才报「长句丢失换行」，默认 24，建议 20~30。</p>
          </div>
          <div class="pc-row-control">
            <input
              id="avg-sentence-length-threshold-h"
              class="field__input pc-input pc-input--num"
              type="number"
              min={10}
              max={50}
              step={1}
              value={hAvgThreshold()}
              onChange={(e) => {
                const raw = Number((e.target as HTMLInputElement).value);
                setHAvgThresholdValue(Number.isFinite(raw) ? raw : 24);
              }}
            />
          </div>
        </div>
        <div class="pc-row">
          <div class="pc-row-label">
            <label class="pc-label" for="attributive-max-length">
              定语最大长度（定语过长）
            </label>
            <p class="pc-desc">「是……的」中间定语超过该字数即报「定语过长」，默认 10。</p>
          </div>
          <div class="pc-row-control">
            <input
              id="attributive-max-length"
              class="field__input pc-input pc-input--num"
              type="number"
              min={1}
              max={30}
              step={1}
              value={attrMaxLen()}
              onChange={(e) => {
                const raw = Number((e.target as HTMLInputElement).value);
                setAttrMaxLenValue(Number.isFinite(raw) ? raw : 10);
              }}
            />
          </div>
        </div>
        <div class="pc-row">
          <div class="pc-row-label">
            <label class="pc-label" for="adverbial-max-length">
              状语最大长度（状语过长）
            </label>
            <p class="pc-desc">「在……中/里」或「……地」状语超过该字数即报「状语过长」，默认 12。</p>
          </div>
          <div class="pc-row-control">
            <input
              id="adverbial-max-length"
              class="field__input pc-input pc-input--num"
              type="number"
              min={1}
              max={50}
              step={1}
              value={advMaxLen()}
              onChange={(e) => {
                const raw = Number((e.target as HTMLInputElement).value);
                setAdvMaxLenValue(Number.isFinite(raw) ? raw : 12);
              }}
            />
          </div>
        </div>
        <p class="pc-desc" style="padding: var(--space-1) var(--space-2)">
          已启用 {enabledProblemTypes().length} / {problemTypes().length} 个检测项。
          {enabledProblemTypes().length === 0 && " ⚠️ 未选择任何检测项时将不会发现问题。"}
        </p>
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
    setDirty(true);
    editVersion++;
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

  // 最近一次保存失败的详情（卸载自动保存的 failMessage 动态读取，避免错误详情丢失）
  let lastSaveError = "";

  async function doSave(
    showToast: boolean,
    targetPid?: string,
    targetConfigFileName?: string,
  ): Promise<boolean> {
    // 保存目标优先取显式传入（切页自动保存用挂载时快照）；手动保存缺省用当前全局状态
    const savePid = targetPid ?? pid();
    if (!savePid) return false;
    const saveConfigFileName = targetConfigFileName ?? getActiveConfigFileName();
    lastSaveError = "";
    setSaving(true);
    const versionAtSave = editVersion;
    try {
      // 保存前归一化翻译规范键：统一写入 common 段扁平键 gpt.translation_guideline
      //（后端 BaseTranslate/getKey 只读该位置），并清理顶层 gpt 段与 common.gpt 嵌套残留。
      // 注意：不回写前端 config——避免覆盖用户正在编辑的输入；后端下次加载会重新归一化。
      const cleaned = migrateBetterTranslationKey(
        migrateGuidelineKey({ ...config() } as Record<string, ConfigValue>),
      );
      // 保存前校验统一问题修复（fix）条目：问题类型为空时告警（运行时会跳过且不执行）
      const commonCfg = cleaned.common;
      const afterRaw =
        commonCfg &&
        typeof commonCfg === "object" &&
        "gpt.afterTranslation" in commonCfg
          ? (commonCfg as Record<string, ConfigValue>)["gpt.afterTranslation"]
          : undefined;
      const afterWarnings = validateAfterTranslation(parseAfterTranslation(afterRaw));
      for (const w of afterWarnings) toast.warning(w);
      await updateProjectConfig(savePid, {
        config: cleaned,
        config_file_name: saveConfigFileName,
      });
      // 仅当保存期间无新编辑时才清 dirty：否则保存飞行中新增的改动会被误判已保存而静默丢失
      if (editVersion === versionAtSave) setDirty(false);
      if (showToast) toast.success("配置已保存");
      return true;
    } catch (e) {
      lastSaveError = getErrorMessage(e);
      if (showToast) toast.error(`保存失败：${lastSaveError}`);
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function handleSave() {
    await doSave(true);
  }

  /** 等待在途手动保存结束（卸载自动保存前调用，带超时兜底），避免保存在途时跳过导致编辑未落盘 */
  async function waitForSavingDone(): Promise<void> {
    const deadline = Date.now() + 3000;
    while (saving() && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 30));
    }
  }

  // 切页自动保存：离开页面时若有未保存改动，用挂载时的项目/配置名快照保存。
  // 对齐全书 runPageAutosave 统一骨架（卸载时全局状态可能已切走，绝不读取全局）。
  // 不设 isBusy：waitForSavingDone 超时（在途手动保存卡死）后仍强制自动保存，
  // 避免被守卫拦截导致脏数据静默丢弃（保存为幂等覆盖写，与在途请求并发无冲突）。
  onCleanup(() => {
    // 卸载瞬间固化上次保存成败：最近一次保存失败后卸载重试成功时静默，
    // 避免"先失败、后成功"相互矛盾的两个 toast（与 ReviewPage 的 saveFailed 语义一致）
    const saveFailedAtUnmount = lastSaveError !== "";
    void runPageAutosave({
      waitForReady: waitForSavingDone,
      skip: () => !pidSnapshot || loading(),
      isDirty: () => dirty(),
      save: () => doSave(false, pidSnapshot, configFileNameSnapshot()),
      successMessage: () => (saveFailedAtUnmount ? "" : "已自动保存配置"),
      // 失败详情由 doSave 内部捕获（返回 false 不抛异常），failMessage 动态读取补上
      failMessage: () =>
        lastSaveError ? `自动保存配置失败：${lastSaveError}` : "自动保存配置失败",
    });
  });

  return (
    <div class="page page-project-config" id="pc-top">
      <div class="pc-header">
        <div>
          <h2 class="page-title">后端设置（项目设置）</h2>
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
          when={pid() && classifiedGroups().length > 0}
          fallback={<p class="pc-status">{!pid() ? "请先打开一个项目" : "暂无可编辑的配置参数"}</p>}
        >
          {/* 所有配置分区（含固定卡片）统一由 classifiedGroups() 按 taxonomy 渲染；
              游戏外部信息、翻译规范文件、问题检测卡片均通过 section.fixedCards 归位，
              不再以顶部独立块渲染。 */}

          {/* 问题检测（problemAnalyze）：已移回项目设置，随底部「保存配置」统一写回 YAML。
              作为固定卡片由 taxonomy 的「问题检测」section 渲染。 */}
          <div class="pc-field-list">
            <For each={classifiedGroups()}>
              {(g) => {
                const title = g.section.title;
                return (
                  <div
                    class="pc-group"
                    id={title === "问题检测" ? "pc-group-problem-analyze" : undefined}
                    data-title={title}
                  >
                    <div
                      class="pc-group-title pc-group-title--toggle"
                      role="button"
                      tabindex="0"
                      aria-expanded={collapsedGroups().has(title) ? "false" : "true"}
                      aria-controls={title === "问题检测" ? "pc-group-problem-analyze" : undefined}
                      onClick={() => toggleGroup(title)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          toggleGroup(title);
                        }
                      }}
                    >
                      <div>
                        <h3 class="pc-group-title-text">{title}</h3>
                        <Show when={g.section.desc}>
                          <p class="pc-group-desc">{g.section.desc}</p>
                        </Show>
                      </div>
                      <span class="pc-group-chevron" aria-hidden="true">
                        {collapsedGroups().has(title) ? "▸" : "▾"}
                      </span>
                    </div>

                    <div class="pc-group-body" classList={{ "pc-group-body--collapsed": collapsedGroups().has(title) }}>
                      {/* 后端专属：OpenAI 兼容接口跳转到全局后端配置 */}
                      <Show when={title === "后端专属"}>
                        <div class="pc-global-banner">
                          <div class="pc-global-banner__text">
                            <strong>OpenAI 兼容接口</strong> 的 API 令牌与连接参数已由程序全局「后端配置」统一管理，不再在项目设置中维护。
                          </div>
                          <button
                            class="btn btn--sm btn--primary"
                            onClick={(e) => {
                              e.stopPropagation();
                              navigateTo("backend-profiles");
                            }}
                          >
                            去后端配置 →
                          </button>
                        </div>
                      </Show>

                      {/* 固定卡片（游戏外部信息 / 翻译规范文件 / 问题检测），按 taxonomy 顺序渲染 */}
                      <For each={g.fixedCards}>
                        {(kind) => renderFixedCard(kind)}
                      </For>

                      {/* 一级直接字段（无二级时） */}
                      <For each={g.directItems}>
                        {(item) => renderFieldRow(item)}
                      </For>

                      {/* 二级子分组 */}
                      <For each={g.subsections}>
                        {(sub) => (
                          <div class="pc-subsection">
                            <Show when={sub.subsection.title}>
                              <h4 class="pc-subsection-title">{sub.subsection.title}</h4>
                            </Show>
                            <For each={sub.items}>
                              {(item) => renderFieldRow(item)}
                            </For>
                          </div>
                        )}
                      </For>
                    </div>
                  </div>
                );
              }}
            </For>
          </div>
        </Show>
      </Show>
    </div>
  );
}
