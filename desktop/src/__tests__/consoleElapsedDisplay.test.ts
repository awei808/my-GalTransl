/**
 * 翻译控制台时间显示纯函数单元测试
 * 覆盖：formatElapsed（运行中走秒 / 结束冻结 / 跨小时 / 非法入参）与
 * formatTimeOfDay（短格式时:分:秒 / 非法值回退原文 / 空值）。
 */
import { describe, it, expect } from "vitest";

import { formatElapsed, formatTimeOfDay } from "../lib/format";

describe("formatElapsed", () => {
  it("运行中按 nowMs 计算已用时（分秒）", () => {
    // 本地时区解析（无 Z 后缀），避免测试依赖宿主时区
    const start = "2026-11-14T10:00:00";
    expect(formatElapsed(start, null, new Date("2026-11-14T10:02:30").getTime())).toBe(
      "2分 30秒",
    );
    expect(formatElapsed(start, undefined, new Date("2026-11-14T10:00:45").getTime())).toBe(
      "45秒",
    );
  });

  it("超过 1 小时显示 Xh Ym", () => {
    const start = "2026-11-14T10:00:00";
    expect(formatElapsed(start, null, new Date("2026-11-14T12:05:00").getTime())).toBe(
      "2h 5m",
    );
  });

  it("结束后按 endIso 冻结为总耗时，与 nowMs 无关", () => {
    const start = "2026-11-14T10:00:00";
    const end = "2026-11-14T10:03:20";
    expect(formatElapsed(start, end, new Date("2026-11-15T09:00:00").getTime())).toBe(
      "3分 20秒",
    );
  });

  it("结束时间早于开始时间（异常数据）不显示负数", () => {
    expect(
      formatElapsed("2026-11-14T10:00:00", "2026-11-14T09:59:00", 0),
    ).toBe("0秒");
  });

  it("非法或空开始时间返回占位符", () => {
    expect(formatElapsed("", null, Date.now())).toBe("—");
    expect(formatElapsed("not-a-date", null, Date.now())).toBe("—");
  });
});

describe("formatTimeOfDay", () => {
  it("输出 时:分:秒 短格式", () => {
    // 本地时区构造，断言数字部分稳定（zh-CN 24 小时制，补零两位）
    const text = formatTimeOfDay("2026-11-14T16:30:05");
    expect(text).toMatch(/^\d{2}:\d{2}:\d{2}$/);
    expect(text).toContain("30:05");
  });

  it("非法时间返回原文", () => {
    expect(formatTimeOfDay("not-a-date")).toBe("not-a-date");
  });

  it("空值返回空串", () => {
    expect(formatTimeOfDay("")).toBe("");
  });
});
