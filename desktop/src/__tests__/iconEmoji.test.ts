/** 图标统一为 SVG：鲜艳模式的 emoji 替换已移除（对齐上游），注册表不得再引入 emoji。 */
import { describe, expect, it } from "vitest";

import { ICON_PATHS } from "../components/icons/icons";

describe("图标注册表（emoji 替换已移除）", () => {
  it("所有图标均为纯 svg 定义，无 emoji 字段残留", () => {
    for (const [key, def] of Object.entries(ICON_PATHS)) {
      expect((def as unknown as Record<string, unknown>).emoji, `图标 ${key} 不应再带 emoji 字段`).toBeUndefined();
      expect(def.d, `图标 ${key} 缺少 svg path`).toBeTruthy();
    }
  });
});
