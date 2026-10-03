/**
 * 剧情路线图画布的自绘手掌光标。
 * 系统 grab/grabbing 是白色位图，而浅色主题的画布面板本身是 #ffffff，
 * 悬停时会退化成与背景融为一体的小白点；这里自绘 32×32 双色手掌（白色描边层 + 近黑填充层），
 * 浅色画布靠黑色手形、深色画布靠白色描边都能看清。
 * 数据 URI 体积大且需要校验，故放这里而不是 CSS：形状是可读数组，测试可直接断言尺寸/热点/编码。
 */

/* 张开手掌：掌部圆角矩形 + 四根长度递减的手指 + 左侧拇指（空白处可拖拽） */
const OPEN_HAND_SHAPES = [
  "<rect x='7' y='13' width='16.5' height='12.5' rx='5.5'/>",
  "<rect x='8.6' y='4.6' width='4.6' height='12.5' rx='2.3' transform='rotate(-7 10.9 13)'/>",
  "<rect x='13.4' y='2.2' width='4.6' height='14.5' rx='2.3'/>",
  "<rect x='18.2' y='4' width='4.6' height='12.6' rx='2.3' transform='rotate(4 20.5 13)'/>",
  "<rect x='22.8' y='7' width='4.4' height='9.6' rx='2.2' transform='rotate(11 25 13)'/>",
  "<rect x='3.6' y='15.2' width='5.6' height='9.8' rx='2.8' transform='rotate(-24 6.4 20)'/>",
];

/* 握拳：拳体 + 四个折叠指节（顶端露出，靠白描边分出指节）+ 横过前面的拇指 */
const FIST_SHAPES = [
  "<rect x='7.6' y='12' width='17' height='13.5' rx='5.5'/>",
  "<rect x='7.8' y='8.6' width='5' height='9' rx='2.5' transform='rotate(-6 10.3 13)'/>",
  "<rect x='13' y='8.2' width='5' height='9.4' rx='2.5' transform='rotate(-2 15.5 13)'/>",
  "<rect x='18.2' y='8.6' width='5' height='9.2' rx='2.5' transform='rotate(3 20.7 13)'/>",
  "<rect x='23.4' y='9.6' width='4.8' height='8.2' rx='2.4' transform='rotate(8 25.8 13)'/>",
  "<rect x='4.6' y='17.6' width='10.6' height='5.2' rx='2.6' transform='rotate(-16 9.9 20.2)'/>",
];

/* 描边层负责深色画布上的轮廓；描边有一半压在形状内，由随后的填充层覆盖掉内部接缝 */
const HALO_ATTRS = "fill='none' stroke='#ffffff' stroke-width='3' stroke-linejoin='round' stroke-linecap='round'";
const BODY_COLOR = "#111827";

/* 只把 < > # 与空格编码：encodeURIComponent 已放过 ( ) ' ! * - _ . ~，其余还原以缩短 URI */
function encodeSvg(svg: string): string {
  return encodeURIComponent(svg)
    .replace(/%2C/g, ",")
    .replace(/%2F/g, "/")
    .replace(/%3A/g, ":")
    .replace(/%3D/g, "=");
}

/** 组装 cursor 值：url(数据 URI) 热点 关键字兜底（图片加载失败时浏览器顺延到关键字） */
function buildCursor(shapes: string[], hotspotX: number, hotspotY: number, fallback: string): string {
  const body = shapes.join("");
  const svg =
    "<svg xmlns='http://www.w3.org/2000/svg' width='32' height='32' viewBox='0 0 32 32'>" +
    `<g ${HALO_ATTRS}>${body}</g>` +
    `<g fill='${BODY_COLOR}' stroke='none'>${body}</g>` +
    "</svg>";
  return `url("data:image/svg+xml,${encodeSvg(svg)}") ${hotspotX} ${hotspotY}, ${fallback}`;
}

/* CSS 侧以 var(--cursor-canvas-grab, grab) 取用；改这两个名字要同步 part06.css / route-agent.css */
export const CANVAS_GRAB_VAR = "--cursor-canvas-grab";
export const CANVAS_GRABBING_VAR = "--cursor-canvas-grabbing";

/** 画布空白处可拖拽：张开手掌，热点取食指尖 */
export const CANVAS_GRAB_CURSOR = buildCursor(OPEN_HAND_SHAPES, 10, 4, "grab");

/** 画布拖拽中：握拳，热点取拳心 */
export const CANVAS_GRABBING_CURSOR = buildCursor(FIST_SHAPES, 16, 18, "grabbing");

/** 把两个光标写到给定元素的自定义属性上，供路线图样式表的 var() 取用（入口传 document.documentElement） */
export function applyCanvasCursors(root: HTMLElement): void {
  root.style.setProperty(CANVAS_GRAB_VAR, CANVAS_GRAB_CURSOR);
  root.style.setProperty(CANVAS_GRABBING_VAR, CANVAS_GRABBING_CURSOR);
}
