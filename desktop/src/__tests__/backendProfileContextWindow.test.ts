import { describe, expect, it } from "vitest";
import { DEFAULT_CONTEXT_WINDOW, parseContextWindowInput } from "../pages/backends/BackendProfilesPage";

describe("后端配置「上下文大小」输入", () => {
  it("纯数字落成 number", () => {
    expect(parseContextWindowInput("200000")).toBe(200000);
  });

  it("带单位写法原样保留，交给后端解析", () => {
    expect(parseContextWindowInput("128k")).toBe("128k");
  });

  it("空白输入返回 undefined（删键走默认）", () => {
    expect(parseContextWindowInput("")).toBeUndefined();
    expect(parseContextWindowInput("   ")).toBeUndefined();
  });

  it("输入两侧空白被裁剪", () => {
    expect(parseContextWindowInput(" 131072 ")).toBe(131072);
  });

  it("默认窗口与配置模板默认值成对（128000）", () => {
    expect(DEFAULT_CONTEXT_WINDOW).toBe(128000);
  });
});
