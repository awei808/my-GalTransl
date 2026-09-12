/** 图标 emoji 机制：注册表 emoji 仅限审批清单，且带 emoji 的图标必有 svg 回退。 */
import { describe, expect, it } from "vitest";

import { ICON_PATHS } from "../components/icons/icons";

/** 用户审批的 emoji 白名单：新增强制走审批更新此表 */
const APPROVED_EMOJI: Record<string, string> = {
  folder: "📁",
  "folder-open": "📂",
  "open-in-folder": "📂",
  "chevron-down": "▼",
  "tone-success": "✅",
  "tone-error": "❌",
  "tone-warning": "⚠️",
  "tone-info": "ℹ️",
  // 左侧 ActivityBar 导航与快捷入口
  "play-stroke": "🌐",
  edit: "📝",
  search: "🔍",
  "alert-circle": "🚨",
  swap: "🔀",
  book: "📖",
  terminal: "💻",
  settings: "⚙️",
  server: "🖥️",
  exclamation: "❗",
};

describe("图标注册表 emoji 机制", () => {
  it("注册表中带 emoji 的图标与审批清单一一对应", () => {
    const actual = Object.fromEntries(
      Object.entries(ICON_PATHS)
        .filter(([, def]) => def.emoji)
        .map(([key, def]) => [key, def.emoji as string]),
    );
    expect(actual).toEqual(APPROVED_EMOJI);
  });

  it("带 emoji 的图标必须同时保留 svg 路径（扁平模式回退）", () => {
    for (const [key, def] of Object.entries(ICON_PATHS)) {
      if (def.emoji) {
        expect(def.d, `图标 ${key} 缺少 svg path`).toBeTruthy();
      }
    }
  });
});
