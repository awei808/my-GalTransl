/**
 * 修复/改进后端（阶段 7 后处理）清单与有序数组配置的解析辅助。
 *
 * gpt.afterTranslation 配置形态：有序数组（[improve, brfix]），数组顺序即执行顺序；
 * 空数组 = 不执行。旧字符串格式（none / improve+brfix）仍兼容读取，统一解析为数组。
 * 前后端解析口径一致（后端见 GalTransl/Frontend/LLMTranslate.py 的
 * _resolve_after_translation_order），避免显示与执行不一致。
 */

export interface AfterTranslationBackend {
  /** 配置数组元素值，与后端白名单 key 一致 */
  key: string;
  /** 展示名称 */
  label: string;
  /** 面向零基础用户的说明 */
  hint: string;
}

export const AFTER_TRANSLATION_BACKENDS: AfterTranslationBackend[] = [
  {
    key: "improve",
    label: "改进轮",
    hint: "AI 评估整文件译文质量，对可改进的句子给出备选译文（可在校对页一键交换）。",
  },
  {
    key: "brfix",
    label: "换行修复",
    hint: "针对译文内换行位置异常（未紧跟中文标点）的句子生成备选译文。",
  },
  {
    key: "jpfix",
    label: "残留日文修复",
    hint: "对照原文清除译文残留的日文假名，生成备选译文。",
  },
  {
    key: "banfix",
    label: "禁用词修复",
    hint: "针对标注「用词不当」的译文重新翻译，生成备选译文。",
  },
  {
    key: "semcheck",
    label: "语义差异检测",
    hint: "用 AI 判定疑似错译、漏译、译文串行，标记「疑似错误」问题（不生成备选译文）。",
  },
];

/** 是否为受支持的后处理后端 key */
export function isAfterTranslationBackend(key: string): boolean {
  return AFTER_TRANSLATION_BACKENDS.some((b) => b.key === key);
}

/**
 * 解析配置值（有序数组或旧字符串 none/improve+brfix）为有序后端 key 数组。
 * 数组元素仅保留字符串、白名单内、去重保序；"none" 哨兵值忽略。
 */
export function parseAfterTranslation(value: unknown): string[] {
  if (Array.isArray(value)) {
    return filterOrder(value.map((item) => (typeof item === "string" ? item : "")));
  }
  if (typeof value === "string") {
    const s = value.trim().toLowerCase();
    if (!s || s === "none") return [];
    return filterOrder(s.split("+"));
  }
  return [];
}

function filterOrder(parts: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const part of parts) {
    const p = part.trim().toLowerCase();
    if (p === "none") continue;
    if (isAfterTranslationBackend(p) && !seen.has(p)) {
      seen.add(p);
      result.push(p);
    }
  }
  return result;
}
