/**
 * 路线图工作台执行计划纯逻辑（plan.ts）的单测。
 *
 * 回归背景：0.5.4 执行终端按指令构造 /api/jobs 的 file_filter/config_overrides，
 * 「重生成路线图」指令的输入来自元数据缓存，下发 file_filter 会被后端静默忽略，
 * 必须在构造层强制全项目。
 */
import { describe, it, expect } from "vitest";
import {
  buildFileFilter,
  buildJobExtras,
  buildOverrides,
  defaultConfig,
  type InstructionConfig,
} from "../pages/routeAgent/plan";

function cfg(patch: Partial<InstructionConfig> = {}): InstructionConfig {
  return { ...defaultConfig(), ...patch };
}

describe("buildFileFilter", () => {
  it("selected 模式取 Agent 所选文件", () => {
    expect(buildFileFilter("ForGal-json-translate", cfg(), ["a.json", "b.json"])).toEqual(["a.json", "b.json"]);
  });

  it("all 模式返回 undefined（缺省=全项目）", () => {
    expect(buildFileFilter("ForGal-json-translate", cfg({ filesMode: "all" }), ["a.json"])).toBeUndefined();
  });

  it("custom 模式按换行/逗号切分并去空", () => {
    expect(buildFileFilter("ForGal-json-translate", cfg({ filesMode: "custom", customFilesText: "a.json\n\nb.json，c.json" }), [])).toEqual([
      "a.json",
      "b.json",
      "c.json",
    ]);
  });

  it("ForPlotRouteMap 强制忽略文件范围（其输入来自 pass1 元数据缓存）", () => {
    expect(buildFileFilter("ForPlotRouteMap", cfg(), ["a.json"])).toBeUndefined();
    expect(buildFileFilter("ForPlotRouteMap", cfg({ filesMode: "custom", customFilesText: "a.json" }), [])).toBeUndefined();
  });
});

describe("buildOverrides", () => {
  it("三态开关映射：default 不写入 / on 为 true / off 为 false", () => {
    const result = buildOverrides(
      cfg({
        injections: {
          "internals.promptBlocks.glossary": "on",
          "internals.promptBlocks.plotMetadata": "off",
        },
      }),
    );
    expect(result).toEqual({
      "internals.promptBlocks.glossary": true,
      "internals.promptBlocks.plotMetadata": false,
    });
  });

  it("合并高级 JSON 覆盖并可覆盖注入项", () => {
    const result = buildOverrides(
      cfg({
        injections: { "internals.promptBlocks.glossary": "on" },
        advancedText: '{"internals.promptBlocks.glossary": false, "gpt.afterTranslation": ["brfix"]}',
      }),
    );
    expect(result).toEqual({
      "internals.promptBlocks.glossary": false,
      "gpt.afterTranslation": ["brfix"],
    });
  });

  it("非法 JSON 抛错", () => {
    expect(() => buildOverrides(cfg({ advancedText: "{not json" }))).toThrow();
  });

  it("全 default 且无高级覆盖时返回空对象", () => {
    expect(buildOverrides(cfg())).toEqual({});
  });
});

describe("buildJobExtras", () => {
  it("有文件与覆盖时两者都带上", () => {
    const extras = buildJobExtras(
      "ForGal-json-translate",
      cfg({ injections: { "internals.promptBlocks.batchMetadata": "off" } }),
      ["a.json"],
    );
    expect(extras).toEqual({
      file_filter: ["a.json"],
      config_overrides: { "internals.promptBlocks.batchMetadata": false },
    });
  });

  it("无文件且无覆盖时返回空对象（全项目、不覆盖）", () => {
    expect(buildJobExtras("ForGal-json-translate", cfg({ filesMode: "all" }), [])).toEqual({});
  });

  it("文件范围为空列表返回 null 由调用方拦截", () => {
    expect(buildJobExtras("ForGal-json-translate", cfg(), [])).toBeNull();
  });

  it("ForPlotRouteMap 即使未选文件也能执行（强制全项目）", () => {
    const extras = buildJobExtras("ForPlotRouteMap", cfg(), []);
    expect(extras).not.toBeNull();
    expect(extras!.file_filter).toBeUndefined();
  });
});
