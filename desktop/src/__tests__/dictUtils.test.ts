/**
 * dictUtils 单句填空辅助函数单元测试
 * 覆盖：搜索词前缀解析/序列化、条件语义互转、条件项序列化、结构化行序列化往返。
 */
import { describe, it, expect, vi } from "vitest";

vi.mock("../lib/api/client", () => ({ apiRequest: vi.fn() }));

import {
  parseSearchPrefix,
  serializeSearchPrefix,
  condSemanticOf,
  applyCondSemantic,
  serializeCondItem,
  rowToText,
  rowsToStructuredText,
  normalizeDictRow,
} from "../components/dict/dictUtils";
import type { DictRow, ConditionItem } from "../components/dict/dictUtils";

describe("parseSearchPrefix", () => {
  it("识别 1^ 前缀为 first", () => {
    expect(parseSearchPrefix('1^"')).toEqual({ mode: "first", word: '"' });
  });
  it("识别 ^^ 前缀为 startswith", () => {
    expect(parseSearchPrefix("^^词")).toEqual({ mode: "startswith", word: "词" });
  });
  it("无前缀为 all", () => {
    expect(parseSearchPrefix("词")).toEqual({ mode: "all", word: "词" });
    expect(parseSearchPrefix("")).toEqual({ mode: "all", word: "" });
  });
});

describe("serializeSearchPrefix 往返", () => {
  it("all → 原词", () => {
    expect(serializeSearchPrefix("all", "词")).toBe("词");
  });
  it("first → 1^词", () => {
    expect(serializeSearchPrefix("first", "词")).toBe("1^词");
  });
  it("startswith → ^^词", () => {
    expect(serializeSearchPrefix("startswith", "词")).toBe("^^词");
  });
  it("parse∘serialize 幂等", () => {
    for (const raw of ["1^\"", "^^词", "词", ""]) {
      const { mode, word } = parseSearchPrefix(raw);
      expect(serializeSearchPrefix(mode, word)).toBe(raw);
    }
  });
});

describe("condSemanticOf", () => {
  it("映射四种语义", () => {
    const base: ConditionItem = {
      word: "x", op: "", negate: false, startswith: false, endswith: false, placeholder: false,
    };
    expect(condSemanticOf(base)).toBe("has");
    expect(condSemanticOf({ ...base, negate: true })).toBe("not");
    expect(condSemanticOf({ ...base, startswith: true })).toBe("startswith");
    expect(condSemanticOf({ ...base, placeholder: true })).toBe("same");
  });
});

describe("applyCondSemantic", () => {
  it("幂等覆盖全部标志", () => {
    const base: ConditionItem = {
      word: "サタン", op: "", negate: false, startswith: false, endswith: false, placeholder: false,
    };
    expect(applyCondSemantic(base, "has")).toEqual({
      ...base, negate: false, startswith: false, placeholder: false,
    });
    expect(applyCondSemantic(base, "not")).toEqual({
      ...base, negate: true, startswith: false, placeholder: false,
    });
    expect(applyCondSemantic(base, "startswith")).toEqual({
      ...base, negate: false, startswith: true, placeholder: false,
    });
    expect(applyCondSemantic(base, "same")).toEqual({
      ...base, placeholder: true, word: "", negate: false, startswith: false,
    });
  });
  it("semantic∘of 幂等", () => {
    const base: ConditionItem = {
      word: "サタン", op: "", negate: false, startswith: false, endswith: false, placeholder: false,
    };
    for (const sem of ["has", "not", "startswith", "same"] as const) {
      expect(condSemanticOf(applyCondSemantic(base, sem))).toBe(sem);
    }
  });
});

describe("serializeCondItem", () => {
  const base: ConditionItem = {
    word: "サタン", op: "", negate: false, startswith: false, endswith: false, placeholder: false,
  };
  it("普通/否定/开头/同上", () => {
    expect(serializeCondItem(base)).toBe("サタン");
    expect(serializeCondItem({ ...base, negate: true })).toBe("!サタン");
    expect(serializeCondItem({ ...base, startswith: true })).toBe(">サタン");
    expect(serializeCondItem({ ...base, placeholder: true, word: "" })).toBe("(同上)");
  });
});

describe("rowToText 结构化往返", () => {
  it("[and] + 否定条件", () => {
    const row: DictRow = {
      type: "conditional",
      values: ['post_jp', '「 [and] !"', '1^"', '「', ''],
      raw: 'post_jp|「 [and] !"|1^"|「',
      target: "post_jp",
      splWord: "and",
      condItems: [
        { word: "「", op: "", negate: false, startswith: false, endswith: false, placeholder: false },
        { word: '"', op: "and", negate: true, startswith: false, endswith: false, placeholder: false },
      ],
      note: "",
    };
    // 条件列重建会规范化空格（「[and]!"），引擎 split+strip 后语义一致
    expect(rowToText(row)).toBe('post_jp|「[and]!"|1^"|「');
  });

  it("[or] + 注释", () => {
    const row: DictRow = {
      type: "conditional",
      values: ['pre_jp', '人妻[or]ひとづま', '有夫之妇', '人妻', '//条件字典例子'],
      raw: 'pre_jp|人妻[or]ひとづま|有夫之妇|人妻|//条件字典例子',
      target: "pre_jp",
      splWord: "or",
      condItems: [
        { word: "人妻", op: "", negate: false, startswith: false, endswith: false, placeholder: false },
        { word: "ひとづま", op: "or", negate: false, startswith: false, endswith: false, placeholder: false },
      ],
      note: "条件字典例子",
    };
    expect(rowToText(row)).toBe('pre_jp|人妻[or]ひとづま|有夫之妇|人妻|//条件字典例子');
  });

  it("(同上) 占位符", () => {
    const row: DictRow = {
      type: "conditional",
      values: ['pre_jp', '(同上)', '已婚妇女', '人妻', ''],
      raw: 'pre_jp|(同上)|已婚妇女|人妻',
      target: "pre_jp",
      splWord: "",
      condItems: [
        { word: "", op: "", negate: false, startswith: false, endswith: false, placeholder: true },
      ],
      note: "",
    };
    expect(rowToText(row)).toBe('pre_jp|(同上)|已婚妇女|人妻');
  });

  it("无备注时保留非注释 rest（防丢数据）", () => {
    const row: DictRow = {
      type: "conditional",
      values: ['pre_jp', '実家', '娘家', '本家', 'extra字段'],
      raw: 'pre_jp|実家|娘家|本家|extra字段',
      target: "pre_jp",
      splWord: "",
      condItems: [
        { word: "実家", op: "", negate: false, startswith: false, endswith: false, placeholder: false },
      ],
      note: "",
    };
    expect(rowToText(row)).toBe('pre_jp|実家|娘家|本家|extra字段');
  });
});

describe("normalizeDictRow（后端 snake_case → 前端 camelCase）", () => {
  it("顶层字段与 cond_items 嵌套子项都转换", () => {
    const row = normalizeDictRow({
      type: "conditional",
      values: ['post_jp', '「 [and] !"', '1^"', '「', ''],
      raw: 'post_jp|「 [and] !"|1^"|「',
      target: "post_jp",
      spl_word: "and",
      cond_items: [
        { word: "「", op: "", negate: false, startswith: false, endswith: false, placeholder: false },
        { word: '"', op: "and", negate: true, startswith: false, endswith: false, placeholder: false },
      ],
      note: "",
    });
    expect(row.splWord).toBe("and");
    expect(row.condItems).toHaveLength(2);
    expect(row.condItems?.[1].negate).toBe(true);
    expect(row.target).toBe("post_jp");
  });
  it("无结构化字段的行（blank/comment）也兼容", () => {
    const row = normalizeDictRow({ type: "comment", values: ["//xx"], raw: "//xx" });
    expect(row.type).toBe("comment");
    expect(row.values).toEqual(["//xx"]);
  });
});

describe("rowsToStructuredText（保存路径序列化）", () => {
  it("conditional 走结构化重建，normal/comment 走 values join", () => {
    const rows: DictRow[] = [
      {
        type: "normal",
        values: ["女佣", "女仆", "//普通字典例子"],
        raw: "女佣|女仆|//普通字典例子",
        note: "普通字典例子",
      },
      {
        type: "conditional",
        values: ['post_jp', '「 [and] !"', '1^"', '「', ''],
        raw: 'post_jp|「 [and] !"|1^"|「',
        target: "post_jp",
        splWord: "and",
        condItems: [
          { word: "「", op: "", negate: false, startswith: false, endswith: false, placeholder: false },
          { word: '"', op: "and", negate: true, startswith: false, endswith: false, placeholder: false },
        ],
        note: "",
      },
      { type: "comment", values: ["//====="], raw: "//=====" },
    ];
    const out = rowsToStructuredText(rows);
    const lines = out.split("\n");
    // normal 行保留原样（含 // 备注）
    expect(lines[0]).toBe("女佣|女仆|//普通字典例子");
    // conditional 行规范化（空格/尾随空列被丢弃）——既定行为
    expect(lines[1]).toBe('post_jp|「[and]!"|1^"|「');
    // comment 行保留
    expect(lines[2]).toBe("//=====");
  });
  it("空行输出空串", () => {
    expect(rowsToStructuredText([{ type: "blank", values: [], raw: "" }])).toBe("");
  });
});
