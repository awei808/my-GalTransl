/**
 * 问题类型多选过滤（AND 语义）回归测试。
 *
 * 背景：校对审核工具栏原「全部类型」为单选 <select>，改为多选下拉后，
 * 勾选多个类型时需同时命中所有勾选类型才保留（AND 语义）。本测试直接验证
 * 纯函数 applyProblemTypeFilter 的过滤逻辑（空数组=全部、单选、多选 AND、
 * 类型名带「：细节」后缀解析），并锁定 problemTypesOf 的类型名解析口径。
 */
import { describe, it, expect } from "vitest";
import type { CacheEntry } from "../lib/api/types";
import { applyProblemTypeFilter } from "../pages/review/ReviewPage";
import { countProblemTypes, problemTypesOf } from "../lib/problems";

function entry(index: number, problem: string | null): CacheEntry {
  return { index, problem } as CacheEntry;
}

describe("applyProblemTypeFilter 多选 AND 过滤", () => {
  const list: CacheEntry[] = [
    entry(1, "残留日文：ゴックシ"),
    entry(2, "缺控制符：[ ]"),
    entry(3, "残留日文"),
    entry(4, "比日文长"),
    entry(5, null),
    entry(6, "残留日文, 缺控制符：xx"),
  ];

  it("空数组返回原列表（全部类型）", () => {
    expect(applyProblemTypeFilter(list, [])).toEqual(list);
  });

  it("单选只保留命中该类型的条目", () => {
    const out = applyProblemTypeFilter(list, ["残留日文"]);
    expect(out.map((e) => e.index)).toEqual([1, 3, 6]);
  });

  it("多选为 AND 语义：须同时包含所有勾选类型才保留", () => {
    const out = applyProblemTypeFilter(list, ["残留日文", "缺控制符"]);
    // 仅 index 6 同时含「残留日文」与「缺控制符」；只含其一的不保留
    expect(out.map((e) => e.index)).toEqual([6]);
  });

  it("多选过滤后类型名带「：细节」后缀也能命中", () => {
    const out = applyProblemTypeFilter(list, ["缺控制符"]);
    // index 2 的 problem 为 "缺控制符：[ ]"，problemTypesOf 拆分后为 "缺控制符" → 命中
    expect(out.map((e) => e.index)).toEqual([2, 6]);
  });

  it("多选时缺少任一类型的条目被过滤（AND 不匹配）", () => {
    const out = applyProblemTypeFilter(list, ["残留日文", "比日文长"]);
    // 无条目同时含这两类 → 空列表
    expect(out).toEqual([]);
  });

  it("无匹配类型时返回空列表", () => {
    const out = applyProblemTypeFilter(list, ["词频过高"]);
    expect(out).toEqual([]);
  });

  it("problem 为 null/undefined 的条目不参与匹配", () => {
    const out = applyProblemTypeFilter(list, ["残留日文"]);
    expect(out.some((e) => e.index === 5)).toBe(false);
  });

  it("多选 AND 且含「：细节」后缀：仅同时命中所有类型者保留", () => {
    // index 6 为 "残留日文, 缺控制符：xx"（缺控制符带细节后缀）；
    // 同时勾选「残留日文」「缺控制符」仍应命中 index 6（细节后缀被剥离匹配）。
    const out = applyProblemTypeFilter(list, ["残留日文", "缺控制符"]);
    expect(out.map((e) => e.index)).toEqual([6]);
  });
});

/**
 * 类型名解析口径：problem 为「半角逗号分隔的类型列表」，detail 内的逗号必须由
 * 后端 normalize_problem_detail 归一为全角（GalTransl/Problem.py），否则详情会被
 * 切成伪类型名（问题检测统计里曾统计出「译文"已经结束了"过于生硬书面」这类条目）。
 */
describe("problemTypesOf 类型名解析", () => {
  it("节奏单一只按半角逗号切分，行号/分号明细不产生伪类型", () => {
    expect(problemTypesOf("节奏单一：第1、2行各9字；第2、3行各9字")).toEqual([
      "节奏单一",
    ]);
  });

  it("细节含全角逗号（后端归一后的落盘形态）只切出真实类型", () => {
    const problem =
      '疑似错误：语义反转：我的事无所谓→我的事就别提了, 词语色彩不一致：标注轻松调侃区间，译文"已经结束了"过于生硬书面，缺乏口语轻浮感';
    expect(problemTypesOf(problem)).toEqual(["疑似错误", "词语色彩不一致"]);
  });

  it("契约锁定：前端只按半角逗号切分、无类型白名单兜底（故写入侧必须归一）", () => {
    // 记录缺陷形态本身，不是后端修复的回归守卫（后端归一由 tests/test_forwordtonecheck.py
    // 等后端测例守）：这是修复前缓存里的真实 problem 串，也是「问题检测」统计出
    // 「译文"已经结束了"过于生硬书面 1」「缺乏口语轻浮感 1」等伪类型的直接原因。
    // 若日后给前端加白名单/别名兜底使本用例变绿，请先确认数据源已保证不再产生半角逗号。
    const raw =
      '疑似错误：语义反转：我的事无所谓→我的事就别提了, 词语色彩不一致：标注轻松调侃区间,译文"已经结束了"过于生硬书面,缺乏口语轻浮感';
    expect(problemTypesOf(raw)).toEqual([
      "疑似错误",
      "词语色彩不一致",
      '译文"已经结束了"过于生硬书面',
      "缺乏口语轻浮感",
    ]);
  });

  it("旧显示名归一为现名（存量缓存兼容）", () => {
    expect(problemTypesOf("长句丢失换行")).toEqual(["单句过长"]);
  });

  it("同一条目内重复的类型名只保留一个", () => {
    // 字典未使用按词条逐条列出，同一类型会出现多次；类型列表按集合语义返回
    expect(
      problemTypesOf(
        "项目GPT字典-生成未使用：ルザリオ---鲁扎里奥, 项目GPT字典-生成未使用：シュカ---舒卡",
      ),
    ).toEqual(["项目GPT字典-生成未使用"]);
  });

  it("旧名映射撞名时也只保留一个", () => {
    expect(problemTypesOf("单句过长, 长句丢失换行")).toEqual(["单句过长"]);
  });

  it("空 problem 返回空数组", () => {
    expect(problemTypesOf("")).toEqual([]);
    expect(problemTypesOf(undefined)).toEqual([]);
  });
});

/**
 * 统计口径：与总数（problem 非空的条目数）、/progress 的 problems 计数、校对页「问题 N」
 * 保持一致——每个条目对同一类型只计一次。曾按「类型出现次数」累加，导致字典未使用
 * （一条译文漏多个词条）等类型被重复计入，chips 之和虚高。
 */
describe("countProblemTypes 按条目统计", () => {
  it("同类型多条问题、多类型混合时均按条目数计", () => {
    const entries = [
      entry(1, "残留日文"),
      entry(2, "残留日文, 缺控制符：[ ]"),
      // 同一类型在同一份 problem 串里出现两次 → 仍只计 1 条
      entry(3, "项目GPT字典-生成未使用：A---a, 项目GPT字典-生成未使用：B---b"),
      entry(4, ""),
      entry(5, null),
    ];
    const counts = countProblemTypes(entries);
    expect(counts.get("残留日文")).toBe(2);
    expect(counts.get("缺控制符")).toBe(1);
    expect(counts.get("项目GPT字典-生成未使用")).toBe(1);
    expect(counts.size).toBe(3);
  });

  it("各类型条目数之和可大于条目总数（一条命中多类型，集合重叠非重复计数）", () => {
    const entries = [entry(1, "残留日文, 缺控制符：[ ]")];
    const counts = countProblemTypes(entries);
    const sum = [...counts.values()].reduce((a, b) => a + b, 0);
    expect(counts.get("残留日文")).toBe(1);
    expect(counts.get("缺控制符")).toBe(1);
    expect(sum).toBeGreaterThan(entries.filter((e) => !!e.problem).length);
  });

  it("无问题条目不计入任何类型", () => {
    const counts = countProblemTypes([entry(1, ""), entry(2, null)]);
    expect(counts.size).toBe(0);
  });
});
