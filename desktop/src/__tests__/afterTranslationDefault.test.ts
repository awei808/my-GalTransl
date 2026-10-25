/**
 * 新建项目默认后处理清单（createDefaultAfterTranslation）测试。
 *
 * 与后端模板 GalTransl/DefaultProjectConfig.py 的 gpt.afterTranslation 默认值
 * 保持一致（后端侧由 tests/test_default_project_config_defaults.py 锁定）。
 */
import { describe, it, expect } from "vitest";
import {
  createDefaultAfterTranslation,
  isFixEntry,
  parseAfterTranslation,
} from "../lib/afterTranslation";

describe("createDefaultAfterTranslation", () => {
  it("默认顺序：统一问题修复 → 语义差异检测 → 词语色彩一致性检查", () => {
    const order = createDefaultAfterTranslation();
    expect(order).toHaveLength(3);
    expect(isFixEntry(order[0])).toBe(true);
    if (isFixEntry(order[0])) {
      expect(order[0].fix.types).toEqual([
        "残留日文",
        "换行位置异常",
        "多加换行",
        "频繁换行",
        "单句过长",
      ]);
      expect(order[0].fix.injectProblem).toBe(true);
    }
    expect(order[1]).toBe("semcheck");
    expect(order[2]).toBe("tonecheck");
  });

  it("工厂每次返回全新对象，types 数组互不共享", () => {
    const a = createDefaultAfterTranslation();
    const b = createDefaultAfterTranslation();
    expect(a).not.toBe(b);
    expect(a[0]).not.toBe(b[0]);
    expect(isFixEntry(a[0]) && isFixEntry(b[0])).toBe(true);
    if (isFixEntry(a[0]) && isFixEntry(b[0])) {
      expect(a[0].fix.types).not.toBe(b[0].fix.types);
      a[0].fix.types.push("词频过高");
      expect(b[0].fix.types).not.toContain("词频过高");
    }
  });

  it("默认清单可被 parseAfterTranslation 原样解析（口径一致）", () => {
    const order = createDefaultAfterTranslation();
    const roundtrip = parseAfterTranslation(JSON.parse(JSON.stringify(order)));
    expect(roundtrip).toEqual(order);
  });
});

describe("parseAfterTranslation 重复条目", () => {
  it("同一后端允许多次出现（不去重，数组顺序即执行顺序）", () => {
    const parsed = parseAfterTranslation(["improve", "semcheck", "improve"]);
    expect(parsed).toEqual(["improve", "semcheck", "improve"]);
  });

  it("多个 fix 条目（不同类型组合）都保留", () => {
    const first = { fix: { types: ["残留日文"], injectProblem: true } };
    const second = { fix: { types: ["单句过长"], injectProblem: true } };
    const parsed = parseAfterTranslation([first, "semcheck", second]);
    expect(parsed).toHaveLength(3);
    expect(parsed[0]).toEqual(first);
    expect(parsed[2]).toEqual(second);
  });

  it("fix 条目的数字序号 types 原样保留（编辑保存不丢失后端合法配置）", () => {
    const parsed = parseAfterTranslation([{ fix: { types: [3, "4"], injectProblem: false } }]);
    expect(parsed).toEqual([{ fix: { types: [3, "4"], injectProblem: false } }]);
  });

  it("裸 fix 字符串无效（须为对象条目），与后端口径一致", () => {
    const entry = { fix: { types: ["残留日文"], injectProblem: true } };
    const parsed = parseAfterTranslation(["fix", entry, "FIX"]);
    expect(parsed).toEqual([entry]);
  });
});
