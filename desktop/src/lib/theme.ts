import { createSignal } from "solid-js";
import type { ThemeMode } from "./api/types";
import { getThemeModePreference, THEME_MODE_CHANGE_EVENT } from "./api/preferences";

/* 主题应用与监听：统一管理两个根属性，供 App 启动与设置页调用
   - data-theme="light|dark"：深浅（CSS 既有暗色覆盖全部基于它）
   - data-theme-style="flat|vivid"：扁平/鲜艳风格（vivid 为配色与装饰覆盖块）
   正交组合避免复制整套深浅覆盖规则 */

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

/** 当前是否为鲜艳风格（vivid：配色与装饰取上游观感） */
export function isVividTheme(): boolean {
  if (typeof document === "undefined") return false;
  return document.documentElement.getAttribute("data-theme-style") === "vivid";
}

/** 主题信号：组件可在 memo/effect 中追踪，主题切换时自动重算 */
export const [themeDark, setThemeDark] = createSignal(false);
export const [themeVivid, setThemeVivid] = createSignal(false);

let currentMode: ThemeMode = "system";

function apply(mode: ThemeMode): void {
  currentMode = mode;
  const dark =
    mode === "dark-flat" ||
    mode === "dark-vivid" ||
    (mode === "system" && (systemDarkQuery()?.matches ?? false));
  const vivid = mode === "light-vivid" || mode === "dark-vivid";
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  document.documentElement.setAttribute("data-theme-style", vivid ? "vivid" : "flat");
  setThemeDark(dark);
  setThemeVivid(vivid);
  console.info(
    `[theme] 应用主题 mode=${mode} -> data-theme=${dark ? "dark" : "light"} style=${vivid ? "vivid" : "flat"}`,
  );
}

let listenersBound = false;
function bindThemeListeners(): void {
  if (listenersBound || typeof window === "undefined") return;
  listenersBound = true;
  // 偏好写入（含设置页切换）后按事件携带的新模式统一同步
  window.addEventListener(THEME_MODE_CHANGE_EVENT, (e) => {
    const mode = (e as CustomEvent).detail as ThemeMode | undefined;
    apply(typeof mode === "string" ? mode : getThemeModePreference());
  });
  // 仅 system 模式需要跟随系统深浅色变化
  systemDarkQuery()?.addEventListener("change", () => {
    if (currentMode === "system") apply("system");
  });
}

/** 按指定模式应用主题（light-flat/dark-flat/light-vivid/dark-vivid/system） */
export function applyTheme(mode: ThemeMode): void {
  apply(mode);
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
