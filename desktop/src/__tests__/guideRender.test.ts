/**
 * 指南渲染工具测试：markdown 消毒与标题提取。
 */
import { describe, it, expect } from "vitest";
import { guideTitle, renderMarkdown } from "../pages/guide/guideUtils";

describe("renderMarkdown", () => {
  it("渲染标题/列表/表格等基础结构", () => {
    const html = renderMarkdown("# 标题\n\n- 项目 A\n- 项目 B\n\n| 列1 | 列2 |\n|---|---|\n| a | b |");
    expect(html).toContain("<h1");
    expect(html).toContain("项目 A");
    expect(html).toContain("<table");
  });

  it("剔除 script 标签与事件属性（防御纵深，指南内容仍统一消毒）", () => {
    const html = renderMarkdown('# T\n\n<script>alert(1)</script>\n\n[x](javascript:alert(2))\n\n<img src="x" onerror="alert(3)">');
    expect(html).not.toContain("<script");
    expect(html).not.toContain("onerror");
    expect(html).not.toContain("javascript:");
  });

  it("剔除 style 标签与 style 属性", () => {
    const html = renderMarkdown('<style body{display:none} </style>\n\n<span style="color:red">文本</span>');
    expect(html).not.toContain("<style");
    expect(html).not.toContain("style=");
  });
});

describe("guideTitle", () => {
  it("提取首个一级标题", () => {
    expect(guideTitle("04-review.md", "前言文字\n\n# 校对审核\n\n## 小节")).toBe("校对审核");
  });

  it("无一级标题时回退文件名", () => {
    expect(guideTitle("fallback.md", "只有正文")).toBe("fallback.md");
  });
});
