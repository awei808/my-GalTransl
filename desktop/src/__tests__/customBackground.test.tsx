/**
 * 自定义背景：偏好往返与归一化边界、事件监听后根元素类名/透明度变量/图层渲染的联动。
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { render, cleanup } from "@solidjs/testing-library";

import { App } from "../App";
import {
  CUSTOM_BACKGROUND_CHANGE_EVENT,
  clearCustomBackgroundPreference,
  getCustomBackgroundPreference,
  setCustomBackgroundPreference,
} from "../lib/api/preferences";

const PNG_DATA_URL =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
const PNG_DATA_URL_2 =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==2";

describe("自定义背景偏好", () => {
  beforeEach(() => {
    localStorage.clear();
    clearCustomBackgroundPreference();
  });

  afterEach(() => cleanup());

  it("未设置时返回默认值，设置后往返读取", () => {
    const cleared = getCustomBackgroundPreference();
    expect(cleared.imageDataUrl).toBe("");
    expect(cleared.opacity).toBeGreaterThan(0);
    expect(cleared.surfaceOpacity).toBeGreaterThan(0);

    const saved = setCustomBackgroundPreference({
      imageDataUrl: PNG_DATA_URL,
      imageName: "bg.png",
      opacity: 50,
      surfaceOpacity: 60,
    });
    expect(saved.imageDataUrl).toBe(PNG_DATA_URL);
    expect(getCustomBackgroundPreference().opacity).toBe(50);
    expect(getCustomBackgroundPreference().surfaceOpacity).toBe(60);
  });

  it("保存偏好派发变更事件，App 收到后应用根类名、透明度变量与背景图层", async () => {
    render(() => <App />);

    // 未设置：无 custom-bg-on、无图层
    expect(document.documentElement.classList.contains("custom-bg-on")).toBe(false);
    expect(document.querySelector(".app-custom-background")).toBeNull();

    window.dispatchEvent(
      new CustomEvent(CUSTOM_BACKGROUND_CHANGE_EVENT, {
        detail: setCustomBackgroundPreference({
          imageDataUrl: PNG_DATA_URL,
          imageName: "bg.png",
          opacity: 40,
          surfaceOpacity: 55,
        }),
      }),
    );

    expect(document.documentElement.classList.contains("custom-bg-on")).toBe(true);
    expect(document.documentElement.style.getPropertyValue("--custom-bg-opacity")).toBe("0.4");
    expect(document.documentElement.style.getPropertyValue("--custom-bg-surface-opacity")).toBe("0.55");
    const layer = document.querySelector(".app-custom-background") as HTMLElement | null;
    expect(layer).not.toBeNull();
    expect(layer!.style.backgroundImage).toContain(PNG_DATA_URL);
  });

  it("换图后图层 background-image 跟随更新", async () => {
    render(() => <App />);
    window.dispatchEvent(
      new CustomEvent(CUSTOM_BACKGROUND_CHANGE_EVENT, {
        detail: setCustomBackgroundPreference({
          imageDataUrl: PNG_DATA_URL,
          imageName: "bg1.png",
          opacity: 40,
          surfaceOpacity: 55,
        }),
      }),
    );
    window.dispatchEvent(
      new CustomEvent(CUSTOM_BACKGROUND_CHANGE_EVENT, {
        detail: setCustomBackgroundPreference({
          imageDataUrl: PNG_DATA_URL_2,
          imageName: "bg2.png",
          opacity: 40,
          surfaceOpacity: 55,
        }),
      }),
    );
    const layer = document.querySelector(".app-custom-background") as HTMLElement | null;
    expect(layer!.style.backgroundImage).toContain(PNG_DATA_URL_2);
  });

  it("归一化边界：透明度 0 保留、NaN 回退默认、越界钳制、非 data:image/ 前缀拒绝", () => {
    const zero = setCustomBackgroundPreference({
      imageDataUrl: PNG_DATA_URL,
      imageName: "bg.png",
      opacity: 0,
      surfaceOpacity: 33,
    });
    expect(zero.opacity).toBe(0);

    const nan = setCustomBackgroundPreference({
      imageDataUrl: PNG_DATA_URL,
      imageName: "bg.png",
      opacity: NaN,
      surfaceOpacity: 33,
    });
    expect(nan.opacity).toBe(35);

    const clamped = setCustomBackgroundPreference({
      imageDataUrl: PNG_DATA_URL,
      imageName: "bg.png",
      opacity: 200,
      surfaceOpacity: 5,
    });
    expect(clamped.opacity).toBe(80);
    expect(clamped.surfaceOpacity).toBe(18);

    const rejected = setCustomBackgroundPreference({
      imageDataUrl: "javascript:alert(1)",
      imageName: "x.png",
      opacity: 35,
      surfaceOpacity: 33,
    });
    expect(rejected.imageDataUrl).toBe("");
    expect(document.documentElement.classList.contains("custom-bg-on")).toBe(false);
  });

  it("清除偏好后根类名与图层撤销", async () => {
    setCustomBackgroundPreference({
      imageDataUrl: PNG_DATA_URL,
      imageName: "bg.png",
      opacity: 40,
      surfaceOpacity: 55,
    });
    render(() => <App />);
    expect(document.documentElement.classList.contains("custom-bg-on")).toBe(true);

    window.dispatchEvent(
      new CustomEvent(CUSTOM_BACKGROUND_CHANGE_EVENT, { detail: clearCustomBackgroundPreference() }),
    );

    expect(document.documentElement.classList.contains("custom-bg-on")).toBe(false);
    expect(document.querySelector(".app-custom-background")).toBeNull();
  });
});
