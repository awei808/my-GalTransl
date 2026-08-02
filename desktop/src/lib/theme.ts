import { createSignal } from "solid-js";
import type { ThemeMode } from "./api/types";
import { getThemeModePreference, THEME_MODE_CHANGE_EVENT } from "./api/preferences";

/* 主题应用与监听：统一管理 data-theme 属性，供 App 启动与设置页调用 */

const systemDarkQuery = (): MediaQueryList | null =>
  typeof window !== "undefined" ? window.matchMedia("(prefers-color-scheme: dark)") : null;

/** 当前是否为深色：优先读 data-theme，未设置时回退系统偏好（system 模式） */
export function isDarkTheme(): boolean {
  if (typeof document === "undefined") return false;
  const attr = document.documentElement.getAttribute("data-theme");
  if (attr === "dark") return true;
  if (attr === "light") return false;
  return systemDarkQuery()?.matches ?? false;
}

/** 主题信号：组件可在 memo/effect 中追踪，主题切换时自动重算 */
export const [themeDark, setThemeDark] = createSignal(false);

/** 将 data-theme 属性与 themeDark 信号同步为当前实际主题 */
function sync(): void {
  const dark = isDarkTheme();
  setThemeDark(dark);
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "");
}

let listenersBound = false;
function bindThemeListeners(): void {
  if (listenersBound || typeof window === "undefined") return;
  listenersBound = true;
  // 偏好写入（含设置页切换）后统一同步
  window.addEventListener(THEME_MODE_CHANGE_EVENT, () => sync());
  // 仅 system 模式需要跟随系统深浅色变化
  systemDarkQuery()?.addEventListener("change", () => {
    if (getThemeModePreference() === "system") sync();
  });
}

/** 按指定模式应用主题（light/dark/system） */
export function applyTheme(mode: ThemeMode): void {
  const dark = mode === "dark" || (mode === "system" && (systemDarkQuery()?.matches ?? false));
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "");
  setThemeDark(dark);
  bindThemeListeners();
}

/** 应用已保存的主题偏好（启动时调用，幂等） */
export function applyThemePreference(): void {
  applyTheme(getThemeModePreference());
}

// 模块加载即按已存偏好应用一次（早于首次渲染，避免主题闪烁）
if (typeof document !== "undefined") {
  applyThemePreference();
}
