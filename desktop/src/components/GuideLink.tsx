/**
 * GuideLink — 页内「指南」入口：跳转到使用指南页的指定篇目。
 *
 * 统一各页面进入指南的交互与样式；经 navigateToGuide 走正常导航
 * （校对页有未保存修改时会触发确认流程，与手工切页一致）。
 */
import { navigateToGuide } from "../stores/appStore";

interface GuideLinkProps {
  /** 目标篇目文件名（guides/ 目录下的 .md）；缺省打开指南页默认篇目 */
  guide?: string;
  /** 按钮文案，默认「指南」 */
  label?: string;
}

export function GuideLink(props: GuideLinkProps) {
  return (
    <button
      class="guide-link"
      type="button"
      title="打开使用指南"
      onClick={() => navigateToGuide(props.guide ?? null)}
    >
      {props.label ?? "指南"}
    </button>
  );
}
