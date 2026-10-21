/**
 * previewSelection 单测：结果预览面板的选取口径。
 *
 * 回归背景：非 worker 上下文的后端（剧情路线图 / 全局分析）写公共 key "-1"，
 * 一旦存在 worker 板块，旧实现只取选中 worker，这类后端的结果永远显示不出来。
 */
import { describe, expect, it } from "vitest";
import { pickTranslationPreview } from "../lib/previewSelection";

describe("pickTranslationPreview", () => {
  it("无预览数据时返回空串", () => {
    expect(pickTranslationPreview({})).toBe("");
    expect(pickTranslationPreview(undefined)).toBe("");
    expect(pickTranslationPreview(undefined, "0")).toBe("");
  });

  it("选中 worker 有内容时优先取该板块", () => {
    const previews = { "0": "译文 w0", "1": "译文 w1" };
    expect(pickTranslationPreview(previews, "0")).toBe("译文 w0");
    expect(pickTranslationPreview(previews, "1")).toBe("译文 w1");
  });

  it("选中板块无内容时回退公共 key -1（单上下文后端）", () => {
    const previews = { "0": "旧阶段译文", "-1": "路线图 mermaid" };
    expect(pickTranslationPreview(previews, "3")).toBe("路线图 mermaid");
    expect(pickTranslationPreview(previews, undefined)).toBe("路线图 mermaid");
  });

  it("选中板块为空串时同样回退，不停留在空面板", () => {
    const previews = { "0": "", "-1": "全局分析 JSON" };
    expect(pickTranslationPreview(previews, "0")).toBe("全局分析 JSON");
  });

  it("选中 worker 有值时公共 key 不抢占", () => {
    const previews = { "1": "译文 w1", "-1": "全局分析 JSON" };
    expect(pickTranslationPreview(previews, "1")).toBe("译文 w1");
  });

  it("有 worker 板块时不得拿别的 worker 顶替（tab 标签会说谎）", () => {
    const previews = { "0": "译文 w0" };
    expect(pickTranslationPreview(previews, "1")).toBe("");
  });

  it("无板块时回退首条可用预览", () => {
    expect(pickTranslationPreview({ "2": "仅 worker2 有结果" }, undefined)).toBe(
      "仅 worker2 有结果"
    );
  });
});
