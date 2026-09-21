/**
 * settings-taxonomy 分类表测试：验证字典组收录 0.4.6 新增的
 * dictionary.skipOverlapCheck，以及「翻译后端-对话翻译」组收录 0.4.9 新增的
 * gpt.chatMode，防止新键漏归「其他设置」。
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

  it("classifyKeys 将 skipOverlapCheck 归入字典组而非其他设置", () => {
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

  it("classifyKeys 将 chatMode 归入对话翻译组而非其他设置", () => {
    const { groups, unclassified } = classifyKeys(["common.gpt.chatMode"]);
    const group = groups.find((g) => g.section.title === "翻译后端-对话翻译");
    const keys = (group?.subsections ?? []).flatMap((s) => s.keys);
    expect(keys).toContain("common.gpt.chatMode");
    expect(unclassified).not.toContain("common.gpt.chatMode");
  });
});

describe("settings-taxonomy 全局分析范围组", () => {
  const SCOPE_KEYS = [
    "internals.pipeline.globalPromptFiles",
    "internals.pipeline.globalPromptMergeFields",
  ];

  it("两个键已声明在「翻译后端-完整流水线」的「全局分析范围」子组中", () => {
    const section = PROJECT_SETTINGS_TAXONOMY.find(
      (s) => s.title === "翻译后端-完整流水线",
    );
    expect(section).toBeDefined();
    const sub = (section?.subsections ?? []).find(
      (x) => x.title === "全局分析范围",
    );
    expect(sub).toBeDefined();
    for (const key of SCOPE_KEYS) {
      expect(sub?.keys).toContain(key);
    }
  });

  it("classifyKeys 不会把两键落到「其他设置」", () => {
    const { unclassified } = classifyKeys(SCOPE_KEYS);
    for (const key of SCOPE_KEYS) {
      expect(unclassified).not.toContain(key);
    }
  });

  it("阶段开关子组不受影响", () => {
    const section = PROJECT_SETTINGS_TAXONOMY.find(
      (s) => s.title === "翻译后端-完整流水线",
    );
    const toggleSub = (section?.subsections ?? []).find(
      (x) => x.title === "流水线阶段开关",
    );
    expect(toggleSub?.keys).toContain("internals.pipeline.enableGlobalPrompt");
    expect(toggleSub?.keys).not.toContain("internals.pipeline.globalPromptFiles");
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

  it("classifyKeys 不会把五键落到「其他设置」", () => {
    const { unclassified } = classifyKeys(BLOCK_KEYS);
    for (const key of BLOCK_KEYS) {
      expect(unclassified).not.toContain(key);
    }
  });
});
