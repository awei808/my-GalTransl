/**
 * 设置页统一分类体系（二三级语义化分类）。
 *
 * 设计动机：
 * 原项目设置页的二三级分组由「后端 YAML 配置键的物理前缀」自动切分（key.split(".")[0]），
 * 导致同类语义被拆散、空组/杂烩组并存、无二级子分组。本模块改用「显式映射表」驱动：
 * 每个配置键被显式归入 一级 section → 二级 subsection → 字段列表，与物理键名解耦。
 *
 * 两页共用：项目设置页（PROJECT_SETTINGS_TAXONOMY）与程序设置页（APP_SETTINGS_TAXONOMY）
 * 均由此模块定义，保证命名与层级一致。
 *
 * 漏归告警：classifyKeys 会把任何未出现在表内的键归入「其他设置」section，
 * 并在控制台以 warn 级输出，避免字段静默丢失。
 */

/** 项目设置页：一个可渲染的字段条目 */
export interface TaxonomyField {
  /** 展平后的点分键，如 "common.workersPerProject" */
  key: string;
}

/** 项目设置页：二级分类（subsection） */
export interface TaxonomySubsection {
  /** 二级标题 */
  title: string;
  /** 该二级下的字段键（顺序即渲染顺序） */
  keys: string[];
}

/** 项目设置页：固定卡片类型（由 ProjectConfigPage 专用的自定义控件渲染，不入通用列表） */
export type FixedCardKind = "translationGuideline" | "externalInfo" | "problemAnalyze";

/** 项目设置页：一级分类（section） */
export interface TaxonomySection {
  /** 一级标题 */
  title: string;
  /** 可选说明，渲染在标题下方 */
  desc?: string;
  /** 二级分类列表（为空表示不分二级，直接渲染 keys） */
  subsections: TaxonomySubsection[];
  /** 不分二级时的一级直接字段 */
  keys?: string[];
  /** 该 section 内要渲染的固定卡片（有序）。如游戏外部信息、翻译规范文件等专用控件 */
  fixedCards?: FixedCardKind[];
  /** 即便没有任何实际字段/卡片，也始终渲染该 section（用于占位分区，如「多轮对话翻译」） */
  alwaysShow?: boolean;
}

/**
 * 项目设置页显式分类表。
 * 顺序即渲染顺序；每个键只应出现一次，重复会出现在两个组（本模块不做去重校验，靠维护约束）。
 *
 * 分区顺序（用户 2026-08-05 指定）：
 *   目标语言 → 翻译规范文件 → 翻译后端总设置 → 翻译后端-全局提示词（含游戏外部信息）
 *   → 翻译后端-文件/批次元数据提取 → 翻译后端-多轮对话翻译（占位）
 *   → 翻译后端-修复改进 → 问题检测 → 代理 → 缓存与日志 → 字典 → 后端专属 → 其他设置
 */
export const PROJECT_SETTINGS_TAXONOMY: TaxonomySection[] = [
  {
    title: "目标语言",
    desc: "译文输出的目标语言。",
    subsections: [],
    keys: ["common.language"],
  },
  {
    title: "翻译规范文件",
    desc: "位于 translation_guidelines 目录，影响文风与措辞。",
    subsections: [],
    fixedCards: ["translationGuideline"],
  },
  {
    title: "翻译后端总设置",
    desc: "翻译主流程的总开关：接口并发、提示词与规范、换行符等。",
    subsections: [
      {
        title: "",
        keys: [
          "common.workersPerProject",
          "common.autoAdjustWorkers",
          "common.sortBy",
          "common.gpt.enhance_jailbreak",
          "common.gpt.change_prompt",
          "common.gpt.prompt_content",
        ],
      },
    ],
  },
  {
    title: "翻译后端-全局提示词",
    desc: "全局分析（ForGlobalPrompt）的提示词参数与外部背景资料。",
    subsections: [
      {
        title: "",
        keys: [
          "internals.forglobalprompt.inject_guideline",
          "internals.pipeline.maxInputChars",
        ],
      },
    ],
    // 游戏外部信息卡片并入此区（原顶部独立块）
    fixedCards: ["externalInfo"],
  },
  {
    title: "翻译后端-文件/批次元数据提取",
    desc: "文件与批次元数据的生成参数（术语表、批次划分、参考翻译规范等）。",
    subsections: [
      {
        title: "",
        keys: [
          "internals.forbatchmeta.inject_guideline",
          "internals.forfilemeta.inject_guideline",
          "internals.forbatchmeta.max_batches",
          "internals.forbatchmeta.min_batch_size",
          "internals.forbatchmeta.max_batch_size",
        ],
      },
    ],
  },
  {
    // 占位分区：当前无对应字段，未来由修复类/改进类后端接管
    title: "翻译后端-多轮对话翻译",
    desc: "用于配置多轮对话翻译后端（当前暂无对应设置项，预留分区）。",
    subsections: [],
    alwaysShow: true,
  },
  {
    title: "翻译后端-修复改进",
    desc: "改进轮与相关配置，未来将专门用于「修复类 / 改进类」后端（当前为通用改进轮参数）。",
    subsections: [
      {
        title: "",
        keys: [
          "common.gpt.numPerRequestBetter",
          "common.gpt.enableProblemInject",
          "common.gpt.problemInjectTypes",
          "common.gpt.swapFixToCurrent",
        ],
      },
      {
        title: "语义差异检测（ForSemCheck）",
        keys: [
          "common.gpt.semCheck.enabled",
          "common.gpt.semCheck.endpoint",
          "common.gpt.semCheck.modelName",
          "common.gpt.semCheck.apiKey",
          "common.gpt.semCheck.apiTimeout",
          "common.gpt.semCheck.stream",
          "common.gpt.semCheck.provider",
        ],
      },
    ],
  },
  {
    title: "翻译后端-完整流水线",
    desc: "完整流水线（压缩 + 翻译 + 修复改进）的总开关、翻译后处理后端、术语表生成与容错策略，以及各阶段独立开关。",
    subsections: [
      {
        title: "",
        keys: [
          "common.gpt.afterTranslation",
          "internals.pipeline.forceRegenDic",
          "internals.pipeline.abortOnDicFailure",
          "internals.pipeline.forceRegenPlotRoute",
        ],
      },
      {
        title: "流水线阶段开关",
        keys: [
          "internals.pipeline.enableValidate",
          "internals.pipeline.enableCompress",
          "internals.pipeline.enableGlobalPrompt",
          "internals.pipeline.enableGenDic",
          "internals.pipeline.enableFileMeta",
          "internals.pipeline.enablePlotRoute",
          "internals.pipeline.enableBatchMeta",
          "internals.pipeline.enableTranslate",
        ],
      },
    ],
  },
  {
    title: "翻译后端-剧情路线图",
    desc: "剧情路线图（阶段 4.5）的生成参数：剧情结构类型与用户提供的剧情大纲（纯文本）。",
    subsections: [
      {
        title: "",
        keys: [
          "internals.plotroute.structureType",
          "internals.plotroute.userOutline",
        ],
      },
    ],
  },
  {
    title: "问题检测",
    desc: "校对审核需要检测的问题类型与长句阈值（随项目配置保存）。",
    subsections: [],
    fixedCards: ["problemAnalyze"],
  },
  {
    title: "代理",
    desc: "网络代理设置。",
    subsections: [
      {
        title: "",
        keys: ["proxy.enableProxy"],
      },
    ],
  },
  {
    title: "缓存",
    desc: "缓存节奏、日志级别与容错开关。",
    subsections: [
      {
        title: "",
        keys: [
          "common.save_steps",
          "common.start_time",
          "common.retranslFail",
          "common.retranslKey",
          "common.skipH",
          "common.smartRetry",
        ],
      },
    ],
  },
  {
    title: "字典",
    desc: "译前/译后/GPT 字典文件与排序。",
    subsections: [
      {
        title: "",
        keys: [
          "dictionary.defaultDictFolder",
          "dictionary.usePreDictInName",
          "dictionary.usePostDictInName",
          "dictionary.useGPTDictInName",
          "dictionary.sortDict",
        ],
      },
    ],
  },
  {
    title: "后端专属",
    desc: "由程序全局「后端配置」统一管理的接口与插件。",
    subsections: [
      {
        title: "插件",
        keys: [
          "plugin.filePlugin",
          "plugin.file_galtransl_json.output_with_src",
          "backendSpecific.SakuraLLM.rewriteModelName",
          "common.gpt.token_limit",
        ],
      },
    ],
  },
];

/** 程序设置页一级分类骨架：仅元数据（标题/描述/顺序），控件由 SettingsPage 手工渲染 */
export interface AppSettingsSectionMeta {
  /** 稳定 id，供 SettingsPage 内部 switch 对应渲染块 */
  id: string;
  title: string;
  desc: string;
}

/** 程序设置页分类骨架（与项目设置页命名一致） */
export const APP_SETTINGS_TAXONOMY: AppSettingsSectionMeta[] = [
  { id: "ai-api", title: "AI API 调用接口相关", desc: "后端连接、翻译插件与提示词模板。" },
  { id: "backend", title: "后端服务配置（元数据 / 翻译 / 问题修复）", desc: "后端与问题修复后端的跳转与选择。" },
  { id: "display", title: "前端显示相关", desc: "主题、背景、字号与首页记忆。" },
  { id: "log", title: "日志相关", desc: "控制各类日志是否写入文件；error.log 始终写入。" },
  { id: "cache", title: "缓存 / 校对 / 字典相关", desc: "缓存、问题检测、字典均在项目设置中配置。" },
  { id: "about", title: "关于", desc: "项目基础信息与版本更新。" },
];

/** 将 taxonomy 中所有显式列出的键收集成 Set，供漏归检测使用 */
function collectDeclaredKeys(sections: TaxonomySection[]): Set<string> {
  const s = new Set<string>();
  for (const section of sections) {
    for (const sub of section.subsections) {
      for (const k of sub.keys) s.add(k);
    }
    if (section.keys) for (const k of section.keys) s.add(k);
  }
  return s;
}

export interface ClassifiedGroup {
  section: TaxonomySection;
  /** 仅属于该 section 且实际存在（已过滤）的键：section.key 的直接字段 */
  directKeys: string[];
  /** 二级分组（已过滤掉空组、空键） */
  subsections: { subsection: TaxonomySubsection; keys: string[] }[];
  /** 该 section 要渲染的固定卡片（按 taxonomy 顺序） */
  fixedCards: FixedCardKind[];
}

/**
 * 把「当前配置中实际存在的扁平键集合」按 taxonomy 分类。
 * @param existingKeys 当前配置里所有已展平的可见键（已是过滤后、准备进通用列表的键）
 * @param declaredKeys 显式声明的键集合（来自 taxonomy，用于漏归告警）
 * @returns 仅包含「至少含一个实际键，或标记 alwaysShow/fixedCards」的 section；完全无键的组被剔除
 *
 * 注意：existingKeys 必须是已经过 isOmittedFromList / 条件板块等过滤后的键，
 * 本函数只负责「按 taxonomy 归位」，不重复做业务过滤。
 */
export function classifyKeys(
  existingKeys: string[],
  declaredKeys: Set<string> = collectDeclaredKeys(PROJECT_SETTINGS_TAXONOMY),
): { groups: ClassifiedGroup[]; unclassified: string[] } {
  const present = new Set(existingKeys);

  // 漏归检测：配置里有、但 taxonomy 没声明的键 → 进「其他设置」
  const unclassified = existingKeys.filter((k) => !declaredKeys.has(k));
  if (unclassified.length > 0) {
    console.warn(
      "[ProjectConfig] 以下配置键未归入分类表（已自动放入「其他设置」）：\n" +
        unclassified.join("\n"),
    );
  }

  const groups: ClassifiedGroup[] = [];
  for (const section of PROJECT_SETTINGS_TAXONOMY) {
    const directKeys = (section.keys ?? []).filter((k) => present.has(k));
    const subsections: { subsection: TaxonomySubsection; keys: string[] }[] = [];
    for (const sub of section.subsections) {
      const keys = sub.keys.filter((k) => present.has(k));
      if (keys.length > 0) subsections.push({ subsection: sub, keys });
    }
    const fixedCards = section.fixedCards ?? [];
    // 有实际键、有非空二级、有固定卡片、或被显式标记 alwaysShow → 渲染
    if (
      directKeys.length > 0 ||
      subsections.length > 0 ||
      fixedCards.length > 0 ||
      section.alwaysShow
    ) {
      groups.push({ section, directKeys, subsections, fixedCards });
    }
  }

  // 「其他设置」：漏归键单独成组（永远在最后）
  if (unclassified.length > 0) {
    groups.push({
      section: { title: "其他设置", desc: "未在分类表中声明的配置项，请反馈以便归位。", subsections: [] },
      directKeys: unclassified,
      subsections: [],
      fixedCards: [],
    });
  }

  return { groups, unclassified };
}
