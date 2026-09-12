/** 主题模式：旧偏好迁移、新五值往返、双属性应用与信号同步。 */
import { beforeEach, describe, expect, it } from "vitest";

import {
  getThemeModePreference,
  setThemeModePreference,
} from "../lib/api/preferences";
import {
  applyTheme,
  isDarkTheme,
  isVividTheme,
  themeDark,
  themeVivid,
} from "../lib/theme";
import type { ThemeMode } from "../lib/api/types";

describe("主题偏好迁移", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("旧值 light/dark 一次性迁移到对应扁平模式并回写存储", () => {
    localStorage.setItem("galtransl-theme-mode", "light");
    expect(getThemeModePreference()).toBe("light-flat");
    expect(localStorage.getItem("galtransl-theme-mode")).toBe("light-flat");

    localStorage.setItem("galtransl-theme-mode", "dark");
    expect(getThemeModePreference()).toBe("dark-flat");
    expect(localStorage.getItem("galtransl-theme-mode")).toBe("dark-flat");
  });

  it("非法值回退 system，新五值原样往返", () => {
    localStorage.setItem("galtransl-theme-mode", "nonsense");
    expect(getThemeModePreference()).toBe("system");

    for (const mode of [
      "light-flat",
      "dark-flat",
      "light-vivid",
      "dark-vivid",
      "system",
    ] as ThemeMode[]) {
      expect(setThemeModePreference(mode)).toBe(mode);
      expect(getThemeModePreference()).toBe(mode);
    }
  });
});

describe("主题应用", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("applyTheme 同步双属性与信号", () => {
    applyTheme("dark-vivid");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme-style")).toBe("vivid");
    expect(themeDark()).toBe(true);
    expect(themeVivid()).toBe(true);
    expect(isDarkTheme()).toBe(true);
    expect(isVividTheme()).toBe(true);

    applyTheme("light-flat");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(document.documentElement.getAttribute("data-theme-style")).toBe("flat");
    expect(themeDark()).toBe(false);
    expect(themeVivid()).toBe(false);

    applyTheme("dark-flat");
    expect(themeDark()).toBe(true);
    expect(themeVivid()).toBe(false);
  });
});
