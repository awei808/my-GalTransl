/**
 * 问题检测相关的共享工具。
 */

/**
 * 从 problem 文本中提取问题类型名列表（按首次出现顺序去重）。
 * problem 形如 "残留日文, 缺控制符：[ ]"，逗号分隔，类型名可能带 "：细节" 或 ":细节" 后缀。
 * 去重原因：同一条目可携带多条同类型问题（字典未使用按词条逐条列出），
 * 类型列表按集合语义返回；统计侧据此得到「含该类型的条目数」而非问题条数。
 */
// 旧显示名 → 新显示名：存量缓存 problem 串仍是旧名，目录/配置已用新名
const LEGACY_TYPE_ALIASES: Record<string, string> = {
  长句丢失换行: "单句过长",
};

export function problemTypesOf(problem: string | undefined | null): string[] {
  const types = (problem ?? "")
    .split(",")
    .map((s) => s.trim().split("：")[0].split(":")[0])
    .map((t) => LEGACY_TYPE_ALIASES[t] ?? t)
    .filter(Boolean);
  // 旧名映射后可能撞名（如「单句过长」与「长句丢失换行」并存），故在映射之后去重
  return [...new Set(types)];
}

/**
 * 按条目统计各类型命中数（口径：problem 非空的条目数）。
 * 与 /progress 的 problems 计数、校对页「问题 N」同口径：每个条目对同一类型只计一次。
 * 一条译文命中多个类型时会计入多个类型，故各类型计数之和可大于条目总数。
 */
export function countProblemTypes(
  entries: readonly { problem?: string | null }[],
): Map<string, number> {
  const counts = new Map<string, number>();
  for (const entry of entries) {
    for (const type of problemTypesOf(entry.problem)) {
      counts.set(type, (counts.get(type) ?? 0) + 1);
    }
  }
  return counts;
}
