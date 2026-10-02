/**
 * 使用指南渲染工具 — markdown → 消毒 HTML、标题提取。
 */
import DOMPurify from "dompurify";
import { marked } from "marked";

/** 渲染 markdown 为 HTML。指南内容来自本地 guides/ 目录，仍统一经 DOMPurify 消毒（防御纵深）。 */
export function renderMarkdown(md: string): string {
  const html = marked.parse(md, { async: false, gfm: true, breaks: true });
  return DOMPurify.sanitize(html, {
    FORBID_TAGS: ["style", "form"],
    FORBID_ATTR: ["style"],
  });
}

/** 从 markdown 内容提取首个一级标题作为目录显示名；无标题回退文件名。 */
export function guideTitle(name: string, content: string): string {
  const match = /^#\s+(.+)$/m.exec(content);
  return match ? match[1].trim() : name;
}
