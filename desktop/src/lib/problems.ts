/**
 * 问题检测相关的共享工具。
 */

/**
 * 从 problem 文本中提取问题类型名列表。
 * problem 形如 "残留日文, 缺控制符：[ ]"，逗号分隔，类型名可能带 "：细节" 或 ":细节" 后缀。
 */
export function problemTypesOf(problem: string | undefined | null): string[] {
  return (problem ?? "")
    .split(",")
    .map((s) => s.trim().split("：")[0].split(":")[0])
    .filter(Boolean);
}
