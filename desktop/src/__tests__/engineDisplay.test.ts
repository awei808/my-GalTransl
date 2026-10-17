/**
 * engineDisplay 单测：展示元数据的回退口径、搜索过滤、分组排序。
 *
 * 覆盖范围与后端清单的漂移由两份测试共同锁定：
 * 本文件用 /api/translators 实测返回的引擎名做全量覆盖断言，
 * tests/test_engine_display_consistency.py 从后端 TRANSLATOR_SUPPORTED 反向核对。
 */
import { describe, expect, it } from "vitest";
import type { TranslatorOption } from "../lib/api/types";
import {
  ENGINE_HINTS,
  ENGINE_LABELS,
  engineGroup,
  engineHint,
  engineLabel,
  engineShortName,
  engineTitle,
  filterEngines,
  flattenGroups,
  groupEngines,
} from "../lib/engineDisplay";

const opt = (name: string, description = ""): TranslatorOption => ({ name, description });

/** /api/translators 实测返回（18 项，已排除 rebuildr/rebuilda/dump-name/show-plugs 与 3 个工具引擎） */
const API_NAMES = [
  "ForGal-full-pipeline",
  "ForGlobalPrompt",
  "ForRouteAnalysis",
  "ForGlobalAnalysis",
  "ForGal-json-translate",
  "ForImproveTranslation",
  "ForBRStation",
  "ForJPResidue",
  "ForBanWordFix",
  "ForSemCheck",
  "ForSemCheckAgain",
  "ForToneCheck",
  "ForToneCheckAgain",
  "ForFixRound",
  "ForFileMetaData",
  "ForBatchMetaData",
  "ForPlotRouteMap",
  "GenDic",
];
const API_LIST = API_NAMES.map((n) => opt(n, `${n} 的后端描述`));

describe("engineLabel / engineShortName / engineGroup", () => {
  it("已登记引擎返回中文短名与分组", () => {
    expect(engineShortName("ForToneCheck")).toBe("色彩检测");
    expect(engineShortName("ForGal-full-pipeline")).toBe("完整流水线");
    expect(engineGroup("ForSemCheckAgain")).toBe("postprocess");
    expect(engineGroup("ForFileMetaData")).toBe("analysis");
    expect(engineGroup("ForGal-json-translate")).toBe("translate");
  });

  it("未登记引擎回退为引擎名 + other 组，不抛错", () => {
    expect(engineLabel("ForFutureEngine")).toBe("");
    expect(engineShortName("ForFutureEngine")).toBe("ForFutureEngine");
    expect(engineGroup("ForFutureEngine")).toBe("other");
  });
});

describe("engineHint / engineTitle", () => {
  it("已登记引擎用短说明，未登记回退后端描述", () => {
    expect(engineHint("ForToneCheck", "后端长描述")).toBe("标记用词色彩偏离基准的句子");
    expect(engineHint("ForFutureEngine", "后端长描述")).toBe("后端长描述");
    expect(engineHint("ForFutureEngine")).toBe("");
  });

  it("悬浮提示含引擎名，短说明与长描述不同时两者都给", () => {
    const title = engineTitle("ForToneCheck", "后端长描述");
    expect(title).toContain("ForToneCheck");
    expect(title).toContain("标记用词色彩偏离基准的句子");
    expect(title).toContain("后端长描述");
  });

  it("短说明与长描述相同时不重复展示", () => {
    const title = engineTitle("ForFutureEngine", "只有一段描述");
    expect(title.split("只有一段描述").length - 1).toBe(1);
  });
});

describe("filterEngines", () => {
  it("空查询与纯空白查询返回原列表（保持引用）", () => {
    expect(filterEngines(API_LIST, "")).toBe(API_LIST);
    expect(filterEngines(API_LIST, "   ")).toBe(API_LIST);
  });

  it("按引擎名匹配且大小写不敏感", () => {
    const got = filterEngines(API_LIST, "forgal-json");
    expect(got.map((t) => t.name)).toEqual(["ForGal-json-translate"]);
  });

  it("按中文短名匹配", () => {
    const got = filterEngines(API_LIST, "残留");
    expect(got.map((t) => t.name)).toEqual(["ForJPResidue"]);
  });

  it("按短说明匹配（含关键词联想）", () => {
    const got = filterEngines(API_LIST, "色彩");
    expect(got.map((t) => t.name)).toEqual(["ForToneCheck", "ForToneCheckAgain"]);
  });

  it("空白分词为 AND 语义", () => {
    expect(filterEngines(API_LIST, "语义 复核").map((t) => t.name)).toEqual(["ForSemCheckAgain"]);
    expect(filterEngines(API_LIST, "语义 不存在的词")).toEqual([]);
  });

  it("未登记引擎按后端描述匹配", () => {
    const list = [opt("ForFutureEngine", "未来某条路线分析用后端")];
    expect(filterEngines(list, "路线").map((t) => t.name)).toEqual(["ForFutureEngine"]);
  });

  it("无命中返回空数组", () => {
    expect(filterEngines(API_LIST, "zzz-不存在")).toEqual([]);
  });
});

describe("groupEngines", () => {
  it("按 翻译执行 / 分析与元数据 / 后处理与校对 分组，空组不输出", () => {
    const groups = groupEngines(API_LIST);
    expect(groups.map((g) => g.id)).toEqual(["translate", "analysis", "postprocess"]);
    expect(groups.every((g) => g.items.length > 0)).toBe(true);
  });

  it("首个选项保持 ForGal-full-pipeline（与无选中时的默认值口径一致）", () => {
    const flat = flattenGroups(groupEngines(API_LIST));
    // 默认值来自 /api/translators 首项（ensureBackendSelection），此处锁定分组后首项与之一致
    expect(flat[0].name).toBe("ForGal-full-pipeline");
    expect(flat.length).toBe(API_LIST.length);
  });

  it("组内为面向使用的聚类顺序（作品级分析 → 术语表 → 文件级 → 批次级）", () => {
    const groups = groupEngines(API_LIST);
    const analysis = groups.find((g) => g.id === "analysis")!;
    expect(analysis.items.map((t) => t.name)).toEqual([
      "ForGlobalPrompt",
      "ForRouteAnalysis",
      "ForGlobalAnalysis",
      "GenDic",
      "ForFileMetaData",
      "ForPlotRouteMap",
      "ForBatchMetaData",
    ]);
  });

  it("未登记引擎归入末尾的「其它」组", () => {
    const groups = groupEngines([...API_LIST, opt("ForFutureEngine")]);
    expect(groups.map((g) => g.id)).toEqual([
      "translate",
      "analysis",
      "postprocess",
      "other",
    ]);
    expect(groups[3].items.map((t) => t.name)).toEqual(["ForFutureEngine"]);
    expect(groups[3].label).toBe("其它");
  });

  it("不修改入参数组的顺序", () => {
    const input = [...API_LIST];
    groupEngines(input);
    expect(input.map((t) => t.name)).toEqual(API_NAMES);
  });
});

describe("后端清单覆盖", () => {
  it("每个后端引擎都有中文短名与短说明", () => {
    for (const name of API_NAMES) {
      expect(ENGINE_LABELS[name], `${name} 缺少中文短名`).toBeTruthy();
      expect(ENGINE_HINTS[name], `${name} 缺少短说明`).toBeTruthy();
    }
  });

  it("每个后端引擎都能落到具体分组（不落 other）", () => {
    for (const name of API_NAMES) {
      expect(engineGroup(name), `${name} 未登记分组`).not.toBe("other");
    }
  });
});
