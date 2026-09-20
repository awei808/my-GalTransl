/**
 * 角色名颜色生成与显示层翻译（0.4.10 从 ReviewPage.tsx 抽出）。
 *
 * 算法：黄金角度散列 + HSL 感知补偿，保证「同一名字永远同色」且相邻色差异明显。
 * 前 20 色固化为常量（锁定观感），index >= 20 回退算法计算。
 */
import { isDarkTheme } from "../../lib/theme";

/* ── 角色名颜色生成（黄金角度 + 感知补偿）── */

interface ThemeConfig {
  baseColor: string;
  mode: "light" | "dark";
}

const LIGHT_THEME: ThemeConfig = { baseColor: "#0066cc", mode: "light" };
const DARK_THEME: ThemeConfig = { baseColor: "#0099ff", mode: "dark" };

/* 前 20 种颜色（由上方算法对 index 0-19 精确计算后固化，避免每次重算并锁定观感）；
   index >= 20 时回退到算法计算（见 generateColorAt）。 */
const LIGHT_PALETTE_20: string[] = [
  "#0066cc", "#cc002a", "#00cc11", "#4d00cc", "#c58e20",
  "#00ccc4", "#e000a8", "#5fba12", "#0022cc", "#cc1a00",
  "#00cc55", "#9f00e0", "#c5c520", "#0090cc", "#cc0055",
  "#27ba12", "#2200cc", "#cc5e00", "#00cc99", "#e000d6",
];
const DARK_PALETTE_20: string[] = [
  "#0099ff", "#ff004f", "#1ae817", "#4600ff", "#ff9100",
  "#00f0ce", "#ff1adc", "#8ce817", "#0044ff", "#ff0700",
  "#00ff51", "#a51aff", "#dbc924", "#00c1f0", "#ff0083",
  "#46e817", "#1200ff", "#ff5c00", "#00ffa6", "#f21aff",
];

function hexToHsl(hex: string): [number, number, number] {
  const r = parseInt(hex.slice(1, 3), 16) / 255;
  const g = parseInt(hex.slice(3, 5), 16) / 255;
  const b = parseInt(hex.slice(5, 7), 16) / 255;
  const max = Math.max(r, g, b),
    min = Math.min(r, g, b);
  const l = (max + min) / 2;
  let h = 0,
    s = 0;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    switch (max) {
      case r:
        h = ((g - b) / d + (g < b ? 6 : 0)) / 6;
        break;
      case g:
        h = ((b - r) / d + 2) / 6;
        break;
      case b:
        h = ((r - g) / d + 4) / 6;
        break;
    }
  }
  return [h * 360, s * 100, l * 100];
}

function hslToHex(h: number, s: number, l: number): string {
  h = (((h % 360) + 360) % 360);
  s = Math.max(0, Math.min(100, s)) / 100;
  l = Math.max(0, Math.min(100, l)) / 100;
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l - c / 2;
  let r = 0,
    g = 0,
    b = 0;
  if (h < 60) {
    r = c;
    g = x;
  } else if (h < 120) {
    r = x;
    g = c;
  } else if (h < 180) {
    g = c;
    b = x;
  } else if (h < 240) {
    g = x;
    b = c;
  } else if (h < 300) {
    r = x;
    b = c;
  } else {
    r = c;
    b = x;
  }
  const toHex = (v: number) => Math.round((v + m) * 255).toString(16).padStart(2, "0");
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

function hashId(id: string | number): number {
  const str = String(id);
  let hash = 2166136261;
  for (let i = 0; i < str.length; i++) {
    hash ^= str.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

const GOLDEN_ANGLE = 137.5077640500378;

function perceptualAdjust(hue: number, baseSat: number, baseLight: number, mode: "light" | "dark") {
  const h = (((hue % 360) + 360) % 360);
  let sat = baseSat,
    light = baseLight;
  if (h >= 40 && h <= 80) {
    sat *= 0.72;
    if (mode === "light") light = Math.min(light + 5, 65);
  } else if (h > 80 && h <= 120) {
    sat *= 0.82;
  } else if (h >= 170 && h <= 200) {
    if (mode === "dark") light = Math.max(light - 3, 35);
  } else if (h >= 270 && h <= 320) {
    if (mode === "light") light = Math.min(light + 4, 60);
    if (mode === "dark") light = Math.max(light + 5, 50);
  }
  return { sat, light };
}

function generateColorAt(index: number, config: ThemeConfig): string {
  // 前 20 色走固化常量，超出再调用算法（复用下方 hexToHsl/hslToHex/perceptualAdjust）
  if (index >= 0 && index < 20) {
    return (config.mode === "dark" ? DARK_PALETTE_20 : LIGHT_PALETTE_20)[index];
  }
  const [baseHue, baseSat, baseLight] = hexToHsl(config.baseColor);
  const hue = (baseHue + index * GOLDEN_ANGLE) % 360;
  const { sat, light } = perceptualAdjust(hue, baseSat, baseLight, config.mode);
  return hslToHex(hue, sat, light);
}

/** 根据角色名确定性获取颜色（同一名字永远同色） */
export function getNameColor(name: string): string {
  if (!name) return "#999";
  const idx = hashId(name) % 10000;
  return generateColorAt(idx, isDarkTheme() ? DARK_THEME : LIGHT_THEME);
}

/** 显示层角色名翻译：name 可能是字符串或数组，仅在展示时应用替换表，不修改缓存数据 */
export function displaySpeakerName(name: string | string[], nameDict: Record<string, string>): string {
  if (Array.isArray(name)) return name.map((n) => nameDict[n] ?? n).join(" / ");
  return nameDict[name] ?? name;
}

