/**
 * 画布光标自检：data URI 能否解析、尺寸/热点是否合法、是否双层双色、注入的变量名是否与 CSS 对齐。
 * 数据 URI 光标最常见的失效方式是编码坏掉或缺 width/height——浏览器会静默忽略整条 cursor，
 * 故这里按浏览器需要的形式做结构化断言；CSS 侧的 var() 取用（jsdom 不跑样式表）由真机审计确认。
 */
import { describe, expect, it } from "vitest";
import {
  applyCanvasCursors,
  CANVAS_GRAB_CURSOR,
  CANVAS_GRAB_VAR,
  CANVAS_GRABBING_CURSOR,
  CANVAS_GRABBING_VAR,
} from "../lib/plotRouteCursor";

interface ParsedCursor {
  svg: string;
  hotspot: [number, number];
  fallback: string;
}

function parseCursor(value: string): ParsedCursor {
  const parts = value.match(/^url\("data:image\/svg\+xml,([^"]+)"\)\s+(\d+)\s+(\d+),\s*([a-z-]+)$/);
  if (!parts) throw new Error(`不是 url("data:image/svg+xml,…") + 热点 + 兜底关键字的写法`);
  /* data 段里的 < > # " 必须百分号编码，否则按 URL 解析会失败 */
  if (/[<>#"]/.test(parts[1])) throw new Error("data URI 含未编码的 < > # \"");
  return {
    svg: decodeURIComponent(parts[1]),
    hotspot: [Number(parts[2]), Number(parts[3])],
    fallback: parts[4],
  };
}

const GRAB = parseCursor(CANVAS_GRAB_CURSOR);
const GRABBING = parseCursor(CANVAS_GRABBING_CURSOR);

describe("画布光标", () => {
  it("都是 svg 数据 URI + 热点 + 关键字兜底", () => {
    expect(GRAB.fallback).toBe("grab");
    expect(GRABBING.fallback).toBe("grabbing");
  });

  it("解码出的 SVG 可解析且显式声明 32×32", () => {
    for (const token of [GRAB, GRABBING]) {
      const doc = new DOMParser().parseFromString(token.svg, "image/svg+xml");
      expect(doc.querySelector("parsererror")).toBeNull();
      const svg = doc.documentElement;
      expect(svg.tagName.toLowerCase()).toBe("svg");
      expect(svg.getAttribute("xmlns")).toBe("http://www.w3.org/2000/svg");
      expect(svg.getAttribute("width")).toBe("32");
      expect(svg.getAttribute("height")).toBe("32");
      expect(svg.getAttribute("viewBox")).toBe("0 0 32 32");
    }
  });

  it("都画了白色描边层与近黑填充层（浅色/深色画布都要看得清）", () => {
    for (const token of [GRAB, GRABBING]) {
      expect(token.svg).toContain("stroke='#ffffff'");
      expect(token.svg).toContain("fill='#111827'");
      /* 两层：描边层（白）压不住形状内部，必须有独立的填充层 */
      expect(token.svg.match(/<g /g)?.length).toBe(2);
    }
  });

  it("热点落在 32×32 画布内", () => {
    for (const token of [GRAB, GRABBING]) {
      const [x, y] = token.hotspot;
      expect(x).toBeGreaterThanOrEqual(0);
      expect(y).toBeGreaterThanOrEqual(0);
      expect(x).toBeLessThan(32);
      expect(y).toBeLessThan(32);
    }
  });

  it("按 CSS 侧约定的变量名注入根元素", () => {
    /* jsdom 的 CSSOM 对自定义属性支持不稳，这里用最小替身只验证写入的键值 */
    const written = new Map<string, string>();
    const root = {
      style: { setProperty: (name: string, value: string) => written.set(name, value) },
    } as unknown as HTMLElement;
    applyCanvasCursors(root);
    expect([...written.keys()]).toEqual(["--cursor-canvas-grab", "--cursor-canvas-grabbing"]);
    expect(written.get("--cursor-canvas-grab")).toBe(CANVAS_GRAB_CURSOR);
    expect(written.get("--cursor-canvas-grabbing")).toBe(CANVAS_GRABBING_CURSOR);
    /* 与 part06.css / route-agent.css 里 var(--cursor-canvas-*) 的字面量保持一致 */
    expect(CANVAS_GRAB_VAR).toBe("--cursor-canvas-grab");
    expect(CANVAS_GRABBING_VAR).toBe("--cursor-canvas-grabbing");
  });
});
