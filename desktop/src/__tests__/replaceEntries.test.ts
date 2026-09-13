/**
 * 查找替换纯函数（replaceInEntries）单元测试
 * 覆盖：dst/all 字段口径（仅译文侧）、同字段全量替换、$ 字面量替换语义、
 * src 不参与替换、未命中不变、历史兼容别名、onlyIndex 单条替换、
 * before/after 完整快照、空 query 防御。
 */
import { describe, it, expect } from "vitest";

import { replaceInEntries } from "../lib/replaceEntries";
import type { CacheEntry, CacheReplaceField } from "../lib/api/types";

function entry(partial: Partial<CacheEntry> & { index: number }): CacheEntry {
  return {
    name: "",
    pre_src: "",
    post_src: "",
    pre_dst: "",
    ...partial,
  };
}

describe("replaceInEntries", () => {
  it("dst 字段替换 pre_dst 与 proofread_dst", () => {
    const list = [entry({ index: 1, pre_dst: "你好喵", proofread_dst: "你好喵喵" })];
    const res = replaceInEntries(list, "喵", "酱", "dst");
    expect(res.entries[0].pre_dst).toBe("你好酱");
    expect(res.entries[0].proofread_dst).toBe("你好酱酱");
    expect(res.changed).toHaveLength(1);
    expect(res.changed[0].before.pre_dst).toBe("你好喵");
    expect(res.changed[0].after.pre_dst).toBe("你好酱");
  });

  it("运行时防御：非法字段（src）不替换任何文本", () => {
    const list = [entry({ index: 1, post_src: "こんにちは", pre_dst: "你好" })];
    // 类型已收窄为 "dst" | "all"，此处断字传非法值验证运行时兜底（原文不可替换）
    const res = replaceInEntries(list, "にち", "ばん", "src" as CacheReplaceField);
    expect(res.entries[0].post_src).toBe("こんにちは");
    expect(res.entries[0].pre_dst).toBe("你好");
    expect(res.changed).toHaveLength(0);
  });

  it("all 字段仅替换译文侧字段，post_src 不动", () => {
    const list = [entry({ index: 1, post_src: "A-猫-B", pre_dst: "甲-猫-乙", proofread_dst: "丙-猫-丁" })];
    const res = replaceInEntries(list, "猫", "犬", "all");
    expect(res.entries[0].post_src).toBe("A-猫-B");
    expect(res.entries[0].pre_dst).toBe("甲-犬-乙");
    expect(res.entries[0].proofread_dst).toBe("丙-犬-丁");
    expect(res.changed).toHaveLength(1);
  });

  it("同一条译文内多处匹配全量替换（与后端 str.replace 一致）", () => {
    const list = [entry({ index: 1, pre_dst: "喵喵喵" })];
    const res = replaceInEntries(list, "喵", "酱", "dst");
    expect(res.entries[0].pre_dst).toBe("酱酱酱");
  });

  it("替换串中的 $ 序列按字面量处理（与后端 str.replace 一致）", () => {
    const list = [entry({ index: 1, pre_dst: "A-B" })];
    const res = replaceInEntries(list, "-", "$&$'", "dst");
    expect(res.entries[0].pre_dst).toBe("A$&$'B");
  });

  it("未命中条目不变化且不入 changed", () => {
    const list = [entry({ index: 1, pre_dst: "原文" }), entry({ index: 2, pre_dst: "无关" })];
    const res = replaceInEntries(list, "不存在", "x", "dst");
    expect(res.entries).toEqual(list);
    expect(res.changed).toHaveLength(0);
  });

  it("历史兼容别名 pre_zh / proofread_zh 参与替换，post_jp 不动", () => {
    const list = [
      { index: 1, post_jp: "旧日文", pre_zh: "旧中文", proofread_zh: "旧校对" } as unknown as CacheEntry,
    ];
    const res = replaceInEntries(list, "旧", "新", "all");
    expect(res.entries[0].post_jp).toBe("旧日文");
    expect(res.entries[0].pre_zh).toBe("新中文");
    expect(res.entries[0].proofread_zh).toBe("新校对");
    expect(res.changed).toHaveLength(1);
  });

  it("onlyIndex 仅替换指定条目", () => {
    const list = [
      entry({ index: 1, pre_dst: "甲-猫" }),
      entry({ index: 2, pre_dst: "乙-猫" }),
      entry({ index: 3, pre_dst: "丙-猫" }),
    ];
    const res = replaceInEntries(list, "猫", "犬", "dst", { onlyIndex: 2 });
    expect(res.entries[0].pre_dst).toBe("甲-猫");
    expect(res.entries[1].pre_dst).toBe("乙-犬");
    expect(res.entries[2].pre_dst).toBe("丙-猫");
    expect(res.changed.map((c) => c.index)).toEqual([2]);
  });

  it("onlyIndex 未命中时不变更", () => {
    const list = [entry({ index: 1, pre_dst: "甲-猫" })];
    const res = replaceInEntries(list, "猫", "犬", "dst", { onlyIndex: 99 });
    expect(res.entries).toEqual(list);
    expect(res.changed).toHaveLength(0);
  });

  it("空 query 直接返回原列表（防御）", () => {
    const list = [entry({ index: 1, pre_dst: "任意文本" })];
    const res = replaceInEntries(list, "", "x", "dst");
    expect(res.entries).toEqual(list);
    expect(res.changed).toHaveLength(0);
  });

  it("before/after 为完整条目快照且互不影响", () => {
    const list = [entry({ index: 1, pre_dst: "旧译文", proofread_dst: "旧校对" })];
    const res = replaceInEntries(list, "旧", "新", "all");
    const c = res.changed[0];
    expect(c.before.pre_dst).toBe("旧译文");
    expect(c.before.proofread_dst).toBe("旧校对");
    expect(c.after.pre_dst).toBe("新译文");
    expect(c.after.proofread_dst).toBe("新校对");
    // before 快照不被替换污染
    expect(res.entries[0].pre_dst).toBe("新译文");
    expect(c.before.pre_dst).toBe("旧译文");
  });
});
