/**
 * 有序条目数组的「数字框」交互公共实现。
 *
 * 供 AI 初步处理顺序编辑器（AfterTranslationOrderEditor）与流水线步骤编辑器
 * （PipelineStepsEditor）共用，统一交互约定：
 * - 数字几 = 该条目在第几步执行（数组顺序即执行顺序）；
 * - 清空 / 非法值 = 移除该条目，其余自动紧凑重排（序号始终连续 1..N）；
 * - 越界数字自动夹取到首/尾。
 * 全部为纯函数，输入输出均为新数组（不原地修改）。
 */

/** 解析数字框输入：合法返回 ≥1 的整数（越界由 clampPos 夹取），清空/非法返回 null */
export function parseStepNumber(raw: string): number | null {
  const n = Number(raw);
  if (raw.trim() === "" || !Number.isFinite(n) || n < 1) return null;
  return Math.floor(n);
}

/** 把目标位夹取到 [1, restLength + 1]（插入时允许追加到末尾之后一位） */
export function clampPos(pos: number, restLength: number): number {
  return Math.min(Math.max(pos, 1), restLength + 1);
}

/** 已有条目输入：移动到对应执行位；清空/非法值 = 移除 */
export function moveOrRemoveAt<T>(list: T[], idx: number, raw: string): T[] {
  const pos = parseStepNumber(raw);
  const rest = list.filter((_, i) => i !== idx);
  if (pos === null) return rest;
  const next = [...rest];
  next.splice(clampPos(pos, rest.length) - 1, 0, list[idx]);
  return next;
}

/** 占位框新建条目：有数字放到对应执行位，无数字（纯聚焦）追加到末尾 */
export function insertNewAt<T>(list: T[], entry: T, raw: string): T[] {
  const pos = parseStepNumber(raw);
  const next = [...list];
  next.splice(clampPos(pos ?? list.length + 1, list.length) - 1, 0, entry);
  return next;
}
