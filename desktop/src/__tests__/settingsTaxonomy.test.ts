/**
 * settings-taxonomy 分类表测试：验证字典组收录 0.4.6 新增的
 * dictionary.skipOverlapCheck，以及「翻译后端-对话翻译」组收录 0.4.9 新增的
 * gpt.chatMode，防止新键漏归「未归类设置」。
 */
import { describe, it, expect } from "vitest";
import { classifyKeys, PROJECT_SETTINGS_TAXONOMY } from "../lib/settings-taxonomy";

describe("settings-taxonomy 字典组", () => {
  it("skipOverlapCheck 已声明在字典组中", () => {
    const section = PROJECT_SETTINGS_TAXONOMY.find((s) => s.title === "字典");
    expect(section).toBeDefined();
    const keys = (section?.subsections ?? []).flatMap((sub) => sub.keys);
    expect(keys).toContain("dictionary.skipOverlapCheck");
  });

  it("classifyKeys 将 skipOverlapCheck 归入字典组而非未归类设置", () => {
    const { groups, unclassified } = classifyKeys([
      "dictionary.sortDict",
      "dictionary.skipOverlapCheck",
    ]);
    const dictGroup = groups.find((g) => g.section.title === "字典");
    // 字典组的键在空标题 subsection 内（与 taxonomy 结构一致）
    const dictKeys = (dictGroup?.subsections ?? []).flatMap((s) => s.keys);
    expect(dictKeys).toContain("dictionary.skipOverlapCheck");
    expect(unclassified).not.toContain("dictionary.skipOverlapCheck");
  });
});

describe("settings-taxonomy 对话翻译组", () => {
  it("chatMode 已声明在「翻译后端-对话翻译」组中", () => {
    const section = PROJECT_SETTINGS_TAXONOMY.find(
      (s) => s.title === "翻译后端-对话翻译",
    );
    expect(section).toBeDefined();
    const keys = (section?.subsections ?? []).flatMap((sub) => sub.keys);
    expect(keys).toContain("common.gpt.chatMode");
  });

  it("classifyKeys 将 chatMode 归入对话翻译组而非未归类设置", () => {
    const { groups, unclassified } = classifyKeys(["common.gpt.chatMode"]);
    const group = groups.find((g) => g.section.title === "翻译后端-对话翻译");
    const keys = (group?.subsections ?? []).flatMap((s) => s.keys);
    expect(keys).toContain("common.gpt.chatMode");
    expect(unclassified).not.toContain("common.gpt.chatMode");
  });
});

describe("settings-taxonomy 全局分析（路线分片+汇总）组", () => {
  const SCOPE_KEYS = [
    "internals.globalanalysis.maxRoutes",
    "internals.globalanalysis.maxRouteInputChars",
    "internals.globalanalysis.routeParallelism",
    "internals.globalanalysis.forceRegenRouteAnalysis",
  ];

  it("全部键已声明在「翻译后端-完整流水线」的「全局分析（路线分片+汇总）」子组中", () => {
    const section = PROJECT_SETTINGS_TAXONOMY.find(
      (s) => s.title === "翻译后端-完整流水线",
    );
    expect(section).toBeDefined();
    const sub = (section?.subsections ?? []).find(
      (x) => x.title === "全局分析（路线分片+汇总）",
    );
    expect(sub).toBeDefined();
    for (const key of SCOPE_KEYS) {
      expect(sub?.keys).toContain(key);
    }
  });

  it("classifyKeys 不会把任一键落到「未归类设置」", () => {
    const { unclassified } = classifyKeys(SCOPE_KEYS);
    for (const key of SCOPE_KEYS) {
      expect(unclassified).not.toContain(key);
    }
  });

  it("阶段开关键已移入 pipelineSteps 固定卡片，不再入通用子组", () => {
    const section = PROJECT_SETTINGS_TAXONOMY.find(
      (s) => s.title === "翻译后端-完整流水线",
    );
    expect(section).toBeDefined();
    expect(section?.fixedCards).toContain("pipelineSteps");
    const keys = (section?.subsections ?? []).flatMap((x) => x.keys);
    // 开关键由固定卡片按步骤同口径同步写回，不出现（也不应出现）在通用列表
    expect(keys).not.toContain("internals.pipeline.enableGlobalPrompt");
    expect(keys).not.toContain("internals.pipeline.steps");
    // 全局分析参数仍在通用子组（仅开关键移出）
    expect(keys).toContain("internals.globalanalysis.maxRoutes");
  });
});

describe("settings-taxonomy 元数据行数阈值组", () => {
  // 行数不足 metaMinLines 的文件不生成元数据：新键须归入元数据提取组而非「未归类设置」
  it("metaMinLines 已声明在「翻译后端-文件/批次元数据提取」组中", () => {
    const section = PROJECT_SETTINGS_TAXONOMY.find(
      (s) => s.title === "翻译后端-文件/批次元数据提取",
    );
    expect(section).toBeDefined();
    const keys = (section?.subsections ?? []).flatMap((sub) => sub.keys);
    expect(keys).toContain("internals.pipeline.metaMinLines");
  });

  it("classifyKeys 将 metaMinLines 归入元数据提取组", () => {
    const { groups, unclassified } = classifyKeys([
      "internals.pipeline.metaMinLines",
    ]);
    const group = groups.find(
      (g) => g.section.title === "翻译后端-文件/批次元数据提取",
    );
    const keys = (group?.subsections ?? []).flatMap((s) => s.keys);
    expect(keys).toContain("internals.pipeline.metaMinLines");
    expect(unclassified).not.toContain("internals.pipeline.metaMinLines");
  });
});

describe("settings-taxonomy 检测轮批次划分组", () => {
  const DETECT_KEYS = [
    "internals.detect.batch_from_metadata",
    "internals.detect.batch_from_metadata_max_sentences",
  ];

  it("两个键已声明在检测类后端子组中", () => {
    const section = PROJECT_SETTINGS_TAXONOMY.find(
      (s) => s.title === "翻译后端-修复改进",
    );
    expect(section).toBeDefined();
    const sub = (section?.subsections ?? []).find((x) =>
      x.title.includes("检测轮"),
    );
    expect(sub).toBeDefined();
    for (const key of DETECT_KEYS) {
      expect(sub?.keys).toContain(key);
    }
  });

  it("classifyKeys 不会把两键落到「未归类设置」", () => {
    const { unclassified } = classifyKeys(DETECT_KEYS);
    for (const key of DETECT_KEYS) {
      expect(unclassified).not.toContain(key);
    }
  });
});

describe("settings-taxonomy 提示词注入块组", () => {
  const BLOCK_KEYS = [
    "internals.promptBlocks.translationGuideline",
    "internals.promptBlocks.glossary",
    "internals.promptBlocks.plotMetadata",
    "internals.promptBlocks.batchMetadata",
    "internals.promptBlocks.globalPrompt",
  ];

  it("五个键已声明在「提示词注入块」子组中", () => {
    const section = PROJECT_SETTINGS_TAXONOMY.find(
      (s) => s.title === "翻译后端-完整流水线",
    );
    expect(section).toBeDefined();
    const sub = (section?.subsections ?? []).find(
      (x) => x.title.includes("提示词注入块"),
    );
    expect(sub).toBeDefined();
    for (const key of BLOCK_KEYS) {
      expect(sub?.keys).toContain(key);
    }
  });

  it("classifyKeys 不会把五键落到「未归类设置」", () => {
    const { unclassified } = classifyKeys(BLOCK_KEYS);
    for (const key of BLOCK_KEYS) {
      expect(unclassified).not.toContain(key);
    }
  });
});

describe("settings-taxonomy 其他设置组", () => {
  // 低频/历史遗留键显式归置到「其他设置」（2026-10 移组），防止漂移回功能组
  const PARKED_KEYS = [
    "common.gpt.enhance_jailbreak",
    "common.gpt.change_prompt",
    "common.gpt.prompt_content",
  ];

  it("三个低频键已声明在「其他设置」组中", () => {
    const section = PROJECT_SETTINGS_TAXONOMY.find(
      (s) => s.title === "其他设置",
    );
    expect(section).toBeDefined();
    for (const key of PARKED_KEYS) {
      expect(section?.keys).toContain(key);
    }
  });

  it("classifyKeys 将三个低频键归入「其他设置」组而非漏归兜底", () => {
    const { groups, unclassified } = classifyKeys(PARKED_KEYS);
    const group = groups.find((g) => g.section.title === "其他设置");
    for (const key of PARKED_KEYS) {
      expect(group?.directKeys).toContain(key);
    }
    expect(unclassified).toEqual([]);
  });

  it("三个低频键不再出现在「翻译后端总设置」", () => {
    const section = PROJECT_SETTINGS_TAXONOMY.find(
      (s) => s.title === "翻译后端总设置",
    );
    const keys = (section?.subsections ?? []).flatMap((sub) => sub.keys);
    for (const key of PARKED_KEYS) {
      expect(keys).not.toContain(key);
    }
  });

  it("taxonomy 全表每个键只声明一次（防双渲染）", () => {
    const all = PROJECT_SETTINGS_TAXONOMY.flatMap((s) => [
      ...(s.keys ?? []),
      ...s.subsections.flatMap((sub) => sub.keys),
    ]);
    expect(new Set(all).size).toBe(all.length);
  });
});

describe("settings-taxonomy 缓存与重翻组", () => {
  it("save_steps/retranslFail/retranslKey 已声明在「缓存与重翻」组中", () => {
    const section = PROJECT_SETTINGS_TAXONOMY.find(
      (s) => s.title === "缓存与重翻",
    );
    expect(section).toBeDefined();
    const keys = (section?.subsections ?? []).flatMap((sub) => sub.keys);
    for (const key of [
      "common.save_steps",
      "common.retranslFail",
      "common.retranslKey",
    ]) {
      expect(keys).toContain(key);
    }
  });

  it("调度/敏感句/死键不再归入缓存组", () => {
    const section = PROJECT_SETTINGS_TAXONOMY.find(
      (s) => s.title === "缓存与重翻",
    );
    const keys = (section?.subsections ?? []).flatMap((sub) => sub.keys);
    for (const key of ["common.start_time", "common.skipH", "common.smartRetry"]) {
      expect(keys).not.toContain(key);
    }
  });
});

describe("settings-taxonomy 调度键归位", () => {
  it("start_time 已声明在「翻译后端总设置」中，linebreakSymbol 不再声明", () => {
    const section = PROJECT_SETTINGS_TAXONOMY.find(
      (s) => s.title === "翻译后端总设置",
    );
    const keys = (section?.subsections ?? []).flatMap((sub) => sub.keys);
    expect(keys).toContain("common.start_time");
    // linebreakSymbol 为死键（getlbSymbol 定义后全仓无调用），不声明、由隐藏清单接管
    expect(keys).not.toContain("common.linebreakSymbol");
  });

  it("classifyKeys 将 start_time 归入总设置而非未归类设置", () => {
    const { groups, unclassified } = classifyKeys(["common.start_time"]);
    const group = groups.find((g) => g.section.title === "翻译后端总设置");
    const keys = (group?.subsections ?? []).flatMap((s) => s.keys);
    expect(keys).toContain("common.start_time");
    expect(unclassified).not.toContain("common.start_time");
  });
});

describe("settings-taxonomy H 档位组", () => {
  it("skipH 已声明在「H 档位阈值与禁用词」子组中", () => {
    const section = PROJECT_SETTINGS_TAXONOMY.find(
      (s) => s.title === "翻译后端-完整流水线",
    );
    const sub = (section?.subsections ?? []).find((x) =>
      x.title.includes("H 档位"),
    );
    expect(sub).toBeDefined();
    expect(sub?.keys).toContain("common.skipH");
  });

  it("classifyKeys 将 skipH 归入完整流水线而非未归类设置", () => {
    const { groups, unclassified } = classifyKeys(["common.skipH"]);
    const group = groups.find(
      (g) => g.section.title === "翻译后端-完整流水线",
    );
    const keys = (group?.subsections ?? []).flatMap((s) => s.keys);
    expect(keys).toContain("common.skipH");
    expect(unclassified).toEqual([]);
  });
});

describe("settings-taxonomy 强制重生/中止键归位", () => {
  it("forceRegenDic/abortOnDicFailure 已声明在「翻译后端-术语表（GenDic）」组中", () => {
    const section = PROJECT_SETTINGS_TAXONOMY.find(
      (s) => s.title === "翻译后端-术语表（GenDic）",
    );
    const keys = (section?.subsections ?? []).flatMap((sub) => sub.keys);
    expect(keys).toContain("internals.pipeline.forceRegenDic");
    expect(keys).toContain("internals.pipeline.abortOnDicFailure");
  });

  it("forceRegenPlotRoute 已声明在「翻译后端-剧情路线图」组中", () => {
    const section = PROJECT_SETTINGS_TAXONOMY.find(
      (s) => s.title === "翻译后端-剧情路线图",
    );
    const keys = (section?.subsections ?? []).flatMap((sub) => sub.keys);
    expect(keys).toContain("internals.pipeline.forceRegenPlotRoute");
  });

  it("三个键不再出现在「翻译后端-完整流水线」", () => {
    const section = PROJECT_SETTINGS_TAXONOMY.find(
      (s) => s.title === "翻译后端-完整流水线",
    );
    const keys = (section?.subsections ?? []).flatMap((sub) => sub.keys);
    expect(keys).not.toContain("internals.pipeline.forceRegenDic");
    expect(keys).not.toContain("internals.pipeline.abortOnDicFailure");
    expect(keys).not.toContain("internals.pipeline.forceRegenPlotRoute");
  });

  it("forceRegenGlobal 已声明在「全局分析（路线分片+汇总）」子组中", () => {
    const section = PROJECT_SETTINGS_TAXONOMY.find(
      (s) => s.title === "翻译后端-完整流水线",
    );
    const sub = (section?.subsections ?? []).find(
      (x) => x.title === "全局分析（路线分片+汇总）",
    );
    expect(sub?.keys).toContain("internals.pipeline.forceRegenGlobal");
  });

  it("classifyKeys 不会把四个键落到「未归类设置」", () => {
    const { unclassified } = classifyKeys([
      "internals.pipeline.forceRegenDic",
      "internals.pipeline.abortOnDicFailure",
      "internals.pipeline.forceRegenPlotRoute",
      "internals.pipeline.forceRegenGlobal",
    ]);
    expect(unclassified).toEqual([]);
  });
});

describe("settings-taxonomy 字典组禁用词键", () => {
  it("forbiddenDictH/NonH 已声明在「字典」组中", () => {
    const section = PROJECT_SETTINGS_TAXONOMY.find((s) => s.title === "字典");
    const keys = (section?.subsections ?? []).flatMap((sub) => sub.keys);
    expect(keys).toContain("dictionary.forbiddenDictH");
    expect(keys).toContain("dictionary.forbiddenDictNonH");
  });

  it("classifyKeys 将两键归入字典组而非未归类设置", () => {
    const { groups, unclassified } = classifyKeys([
      "dictionary.forbiddenDictH",
      "dictionary.forbiddenDictNonH",
    ]);
    const group = groups.find((g) => g.section.title === "字典");
    const keys = (group?.subsections ?? []).flatMap((s) => s.keys);
    expect(keys).toContain("dictionary.forbiddenDictH");
    expect(keys).toContain("dictionary.forbiddenDictNonH");
    expect(unclassified).toEqual([]);
  });
});

describe("settings-taxonomy 插件组", () => {
  it("插件键已声明在「插件」组的 directKeys 中（原「后端专属」更名）", () => {
    const section = PROJECT_SETTINGS_TAXONOMY.find((s) => s.title === "插件");
    expect(section).toBeDefined();
    for (const key of [
      "plugin.filePlugin",
      "plugin.textPlugins",
      "plugin.file_galtransl_json.output_with_src",
    ]) {
      expect(section?.keys).toContain(key);
    }
    expect(
      PROJECT_SETTINGS_TAXONOMY.find((s) => s.title === "后端专属"),
    ).toBeUndefined();
  });

  it("classifyKeys 将插件键归入插件组而非未归类设置", () => {
    const { groups, unclassified } = classifyKeys(["plugin.filePlugin"]);
    const group = groups.find((g) => g.section.title === "插件");
    expect(group?.directKeys).toContain("plugin.filePlugin");
    expect(unclassified).toEqual([]);
  });
});
