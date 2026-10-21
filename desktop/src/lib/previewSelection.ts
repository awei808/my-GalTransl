/**
 * previewSelection — 翻译控制台「结果预览」面板的选取口径（纯函数，便于单测）。
 *
 * 后端按 worker_id 分板块写入 `translation_previews`，非 worker 上下文的后端
 * （文件/批次元数据之外的单上下文后端：剧情路线图、全局分析等）写入公共 key "-1"。
 * 选中板块还没有内容时依次回退，避免这类后端的结果被残留/空闲的 worker 板块遮蔽。
 */
export function pickTranslationPreview(
  previews: Record<string, string> | undefined | null,
  workerId?: string | null,
): string {
  if (!previews) return "";
  if (workerId) {
    // 有 worker 板块时回退链止于公共 key "-1"：拿别的 worker 的结果顶替会让
    // 高亮的「Worker N」标签说谎，宁可留空由 UI 显示占位文案
    return previews[workerId] || previews["-1"] || "";
  }
  // 无分板块（单上下文后端：元数据 / 全局分析 / 剧情路线图）时取公共结果或首条
  return previews["-1"] || Object.values(previews)[0] || "";
}
