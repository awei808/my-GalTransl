/**
 * 自定义流水线步骤（internals.pipeline.steps 低代码有序列表）的前端解析辅助。
 *
 * 配置形态：有序数组，数组顺序即执行顺序；不配置该键时后端按各阶段 enable* 开关
 * 执行默认 9 阶段（旧版行为不变）。条目两类：
 * 1. 阶段 key（validate/compress/.../improve；improve 为宏 = 展开 gpt.afterTranslation 链）；
 * 2. AI 初步处理后端条目（与 gpt.afterTranslation 同构：字符串 key 或
 *    {fix: {types, injectProblem}} 对象），同一后端允许多次出现。
 *
 * 前后端解析口径一致（后端见 GalTransl/Frontend/pipeline_stages.py 的
 * resolve_pipeline_steps），避免显示与执行不一致。
 */
import {
  AFTER_TRANSLATION_BACKENDS,
  FixEntry,
  isFixEntry,
  normalizeFixEntry,
} from "./afterTranslation";
import { FALLBACK_STAGES } from "./pipelineStages";

export type PipelineStep = string | FixEntry;

/** 阶段 key 白名单（顺序即默认执行顺序；与后端 PIPELINE_STAGES 同源） */
export const PIPELINE_STAGE_STEP_KEYS: string[] = FALLBACK_STAGES.map((s) => s.key);

/**
 * 可作为顶层步骤的 AI 初步处理后端 key：improve 除外——它在步骤列表中是阶段宏，
 * 「改进轮」后端仍经 gpt.afterTranslation 链（improve 宏展开）执行。
 */
export const AFTER_TRANS_STEP_KEYS: string[] = AFTER_TRANSLATION_BACKENDS.map(
  (b) => b.key,
).filter((k) => k !== "improve");

export function isPipelineStageStep(step: PipelineStep): boolean {
  return typeof step === "string" && PIPELINE_STAGE_STEP_KEYS.includes(step);
}

export function isAfterTransStep(step: PipelineStep): boolean {
  if (isFixEntry(step)) return true;
  // 裸字符串 "fix" 不算：fix 必须是 {fix: {types, ...}} 对象条目（与后端口径一致）
  return (
    typeof step === "string" && step !== "fix" && AFTER_TRANS_STEP_KEYS.includes(step)
  );
}

/** 步骤展示名：阶段用 display_label 去掉「阶段 N：」前缀；译后条目用后端清单标签 */
export function pipelineStepLabel(step: PipelineStep): string {
  if (isFixEntry(step)) {
    const types = step.fix.types;
    if (types.length === 0) return "统一问题修复（未选类型）";
    const shown = types.slice(0, 2).join("、") + (types.length > 2 ? `等${types.length}类` : "");
    return `统一问题修复（${shown}）`;
  }
  const stage = FALLBACK_STAGES.find((s) => s.key === step);
  if (stage) return stage.label;
  const backend = AFTER_TRANSLATION_BACKENDS.find((b) => b.key === step);
  return backend ? backend.label : String(step);
}

/**
 * 解析配置值为有序步骤数组：保留阶段 key、译后 key 与 fix 对象条目，字符串
 * 条目规范化为小写去空白（与后端 resolve_pipeline_steps 同口径）；未知条目忽略；
 * 同一后端允许多次出现（不去重）。
 */
export function parsePipelineSteps(value: unknown): PipelineStep[] {
  if (!Array.isArray(value)) return [];
  const result: PipelineStep[] = [];
  for (const item of value) {
    if (typeof item === "string") {
      const p = item.trim().toLowerCase();
      if (p === "fix") continue; // 裸 fix 字符串无效：须为 {fix: {...}} 对象条目
      if (
        PIPELINE_STAGE_STEP_KEYS.includes(p) ||
        AFTER_TRANS_STEP_KEYS.includes(p)
      ) {
        result.push(p);
      }
      continue;
    }
    if (isFixEntry(item)) result.push({ fix: normalizeFixEntry(item.fix) });
  }
  return result;
}

/**
 * 保存前校验：返回告警文案列表（toast 用），口径与后端 resolve_pipeline_steps 一致。
 */
export function validatePipelineSteps(steps: PipelineStep[]): string[] {
  const warnings: string[] = [];
  // 译后步骤位于翻译前（或本流水线无翻译阶段）都没有输入，与后端同口径告警
  const translateIdx = steps.indexOf("translate");
  const firstAfterIdx = steps.findIndex((s) => isAfterTransStep(s));
  if (firstAfterIdx >= 0 && (translateIdx < 0 || firstAfterIdx < translateIdx)) {
    warnings.push(
      "存在位于「翻译执行」之前（或本流水线无翻译阶段）的 AI 初步处理步骤：此时无译文与问题标注，该步骤将检测不到任何待处理句",
    );
  }
  const counts = new Map<string, number>();
  for (const s of steps) {
    if (typeof s === "string") counts.set(s, (counts.get(s) ?? 0) + 1);
  }
  for (const [k, n] of counts) {
    if (n > 1 && PIPELINE_STAGE_STEP_KEYS.includes(k)) {
      warnings.push(`流水线步骤「${k}」出现 ${n} 次：该阶段重复执行通常无意义，请确认配置`);
    }
  }
  // 完全相同的译后条目共享断点标记分组：第二次出现会被直接跳过（与后端口径一致）
  const seenCanon = new Set<string>();
  for (const s of steps) {
    if (!isAfterTransStep(s)) continue;
    const canon =
      typeof s === "string"
        ? s
        : JSON.stringify({ fix: { types: [...s.fix.types], injectProblem: s.fix.injectProblem } });
    if (seenCanon.has(canon)) {
      warnings.push(
        "存在完全相同的译后步骤重复出现：断点标记按条目内容分组，第二次出现将被直接跳过（如需再次执行请调整类型组合）",
      );
    }
    seenCanon.add(canon);
  }
  steps.forEach((s, i) => {
    if (isFixEntry(s) && s.fix.types.length === 0) {
      warnings.push(
        `第 ${i + 1} 步「统一问题修复」未选择任何问题类型，运行时将跳过且不执行`,
      );
    }
  });
  return warnings;
}

/**
 * 由步骤列表同步阶段开关键（enableXxx 短名 → 是否在步骤中）。
 * steps 配置存在即权威（enable* 不再生效），写回同口径开关仅为旧版本程序回滚兼容。
 */
export function stageEnableSync(steps: PipelineStep[]): Record<string, boolean> {
  const present = new Set(steps.filter((s): s is string => typeof s === "string"));
  return Object.fromEntries(
    FALLBACK_STAGES.map((s) => {
      const short = s.enabled_key.split(".").pop() as string;
      return [short, present.has(s.key)];
    }),
  );
}
