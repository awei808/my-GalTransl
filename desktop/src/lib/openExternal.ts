/**
 * 外链打开助手 — Tauri 下用系统浏览器，纯浏览器开发模式回退新窗口。
 */
import { open } from "@tauri-apps/plugin-shell";

/** 用系统浏览器打开 http(s) 外链；shell open 失败（如浏览器模式）时回退 window.open */
export function openExternal(href: string): void {
  open(href).catch(() => window.open(href, "_blank", "noopener"));
}
