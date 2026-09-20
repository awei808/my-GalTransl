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
