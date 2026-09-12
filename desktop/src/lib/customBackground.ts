import { createSignal } from "solid-js";
import type { CustomBackgroundPreference } from "./api/types";
import { getCustomBackgroundPreference, CUSTOM_BACKGROUND_CHANGE_EVENT } from "./api/preferences";

/* 自定义背景应用与监听：对齐 theme.ts 的模式，统一管理根属性，供 App 启动与设置页事件调用
   - 根元素 .custom-bg-on：开/关表面 token 半透明覆盖（见 styles/custom-background.css）
   - --custom-bg-opacity / --custom-bg-surface-opacity：图层与表面不透明度（0-1） */

export const [customBackground, setCustomBackground] = createSignal<CustomBackgroundPreference>(
  getCustomBackgroundPreference(),
);

function apply(): void {
  const pref = getCustomBackgroundPreference();
  const root = document.documentElement;
  root.classList.toggle("custom-bg-on", !!pref.imageDataUrl);
  root.style.setProperty("--custom-bg-opacity", String(pref.opacity / 100));
  root.style.setProperty("--custom-bg-surface-opacity", String(pref.surfaceOpacity / 100));
  setCustomBackground(pref);
}

let listenersBound = false;
function bindBackgroundListeners(): void {
  if (listenersBound || typeof window === "undefined") return;
  listenersBound = true;
  // 偏好写入（设置页换图/调透明度/清除）后统一重读并应用
  window.addEventListener(CUSTOM_BACKGROUND_CHANGE_EVENT, () => apply());
}

/** 应用已保存的自定义背景偏好（启动时调用，幂等） */
export function applyCustomBackgroundPreference(): void {
  apply();
  bindBackgroundListeners();
}

// 模块加载即按已存偏好应用一次（早于首次渲染，避免背景闪烁）
if (typeof document !== "undefined") {
  applyCustomBackgroundPreference();
}
