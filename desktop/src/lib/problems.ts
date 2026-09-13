/**
 * 问题检测相关的共享工具。
 */

/**
 * 从 problem 文本中提取问题类型名列表。
 * problem 形如 "残留日文, 缺控制符：[ ]"，逗号分隔，类型名可能带 "：细节" 或 ":细节" 后缀。
 */
// 旧显示名 → 新显示名：存量缓存 problem 串仍是旧名，目录/配置已用新名
const LEGACY_TYPE_ALIASES: Record<string, string> = {
  长句丢失换行: "单句过长",
};

export function problemTypesOf(problem: string | undefined | null): string[] {
  return (problem ?? "")
    .split(",")
    .map((s) => s.trim().split("：")[0].split(":")[0])
    .map((t) => LEGACY_TYPE_ALIASES[t] ?? t)
    .filter(Boolean);
}
