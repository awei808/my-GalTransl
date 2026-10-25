/**
 * 自定义流水线步骤（internals.pipeline.steps）解析辅助测试。
 *
 * 与后端 GalTransl/Frontend/pipeline_stages.py 的 resolve_pipeline_steps 保持
 * 口径一致（阶段 key + 译后条目、重复允许、未知忽略），避免显示与执行不一致。
 */
import { describe, it, expect } from "vitest";
import {
  AFTER_TRANS_STEP_KEYS,
  PIPELINE_STAGE_STEP_KEYS,
  isAfterTransStep,
  parsePipelineSteps,
  pipelineStepLabel,
  stageEnableSync,
  validatePipelineSteps,
} from "../lib/pipelineSteps";

describe("parsePipelineSteps", () => {
  it("保留阶段 key 与译后条目（含 fix 对象），重复允许", () => {
    const fixA = { fix: { types: ["残留日文"], injectProblem: true } };
    const fixB = { fix: { types: [3, "4"], injectProblem: false } };
    const parsed = parsePipelineSteps([
      "validate",
      "translate",
      fixA,
      "semcheck",
      fixB,
      "semcheck",
    ]);
    expect(parsed).toHaveLength(6);
    expect(parsed[0]).toBe("validate");
    expect(parsed[2]).toEqual({ fix: { types: ["残留日文"], injectProblem: true } });
    expect(parsed[5]).toBe("semcheck");
  });

  it("忽略未知条目与非数组输入", () => {
    expect(parsePipelineSteps("validate+translate")).toEqual([]);
    expect(parsePipelineSteps(null)).toEqual([]);
    expect(parsePipelineSteps(["validate", "hahaha", 42])).toEqual(["validate"]);
  });

  it("裸 fix 字符串无效（须为对象条目）；大小写/空白变体规范化", () => {
    expect(parsePipelineSteps(["translate", "fix"])).toEqual(["translate"]);
    expect(parsePipelineSteps(["VALIDATE", " Translate ", "SemCheck"])).toEqual([
      "validate",
      "translate",
      "semcheck",
    ]);
  });

  it("fix 条目 types 保留数字序号（后端按目录顺序解析，编辑保存不丢失）", () => {
    const parsed = parsePipelineSteps([{ fix: { types: [3, "4"], injectProblem: true } }]);
    expect(parsed).toEqual([{ fix: { types: [3, "4"], injectProblem: true } }]);
  });

  it("fix 条目缺省字段归一（types 非数组回空、injectProblem 缺省 true）", () => {
    const parsed = parsePipelineSteps([{ fix: { types: "残留日文" } }]);
    expect(parsed).toEqual([{ fix: { types: [], injectProblem: true } }]);
  });

  it("improve 是阶段宏，不属于译后步骤词表", () => {
    expect(AFTER_TRANS_STEP_KEYS).not.toContain("improve");
    expect(PIPELINE_STAGE_STEP_KEYS).toContain("improve");
    expect(isAfterTransStep("improve")).toBe(false);
    expect(isAfterTransStep("semcheck")).toBe(true);
    expect(isAfterTransStep({ fix: { types: [], injectProblem: true } })).toBe(true);
  });
});

describe("validatePipelineSteps", () => {
  it("译后步骤位于翻译前告警", () => {
    const warnings = validatePipelineSteps(["validate", "semcheck", "translate"]);
    expect(warnings.some((w) => w.includes("位于「翻译执行」之前"))).toBe(true);
  });

  it("无翻译阶段的译后步骤同样告警（与后端口径一致）", () => {
    const warnings = validatePipelineSteps(["validate", "compress", "semcheck"]);
    expect(warnings.some((w) => w.includes("位于「翻译执行」之前"))).toBe(true);
  });

  it("高成本阶段重复告警，译后条目重复不告警", () => {
    const warnings = validatePipelineSteps([
      "file_meta",
      "file_meta",
      "translate",
      { fix: { types: ["残留日文"], injectProblem: true } },
      { fix: { types: ["单句过长"], injectProblem: true } },
    ]);
    expect(warnings.some((w) => w.includes("file_meta"))).toBe(true);
    expect(warnings.some((w) => w.includes("重复执行通常无意义") && w.includes("fix"))).toBe(false);
  });

  it("fix 实例未选问题类型按步号告警", () => {
    const warnings = validatePipelineSteps(["translate", { fix: { types: [], injectProblem: true } }]);
    expect(warnings.some((w) => w.includes("第 2 步") && w.includes("未选择任何问题类型"))).toBe(true);
  });

  it("完全相同的译后条目重复告警（断点分组相同，第二遍被跳过）", () => {
    const fix = { fix: { types: ["残留日文"], injectProblem: true } };
    const warnings = validatePipelineSteps([
      "translate",
      fix,
      "semcheck",
      { fix: { ...fix.fix } },
    ]);
    expect(warnings.some((w) => w.includes("完全相同"))).toBe(true);
  });

  it("常规清单无告警", () => {
    expect(validatePipelineSteps([...PIPELINE_STAGE_STEP_KEYS])).toEqual([]);
  });
});

describe("stageEnableSync / pipelineStepLabel", () => {
  it("按步骤同口径生成 enable* 开关键", () => {
    const sync = stageEnableSync(["validate", "compress", "translate"]);
    expect(sync.enableValidate).toBe(true);
    expect(sync.enableFileMeta).toBe(false);
    expect(sync.enableImprove).toBe(false);
  });

  it("fix 条目不参与开关键同步", () => {
    const sync = stageEnableSync(["translate", { fix: { types: ["残留日文"], injectProblem: true } }]);
    expect(sync.enableTranslate).toBe(true);
    expect(Object.values(sync).filter((v) => v)).toEqual([true]);
  });

  it("展示名：阶段用短名，fix 附类型短标注", () => {
    expect(pipelineStepLabel("translate")).toBe("翻译执行");
    expect(pipelineStepLabel({ fix: { types: ["残留日文", "换行位置异常"], injectProblem: true } })).toBe(
      "统一问题修复（残留日文、换行位置异常）",
    );
    expect(pipelineStepLabel({ fix: { types: [], injectProblem: true } })).toBe(
      "统一问题修复（未选类型）",
    );
    expect(pipelineStepLabel("semcheck")).toBe("语义差异检测");
  });
});
