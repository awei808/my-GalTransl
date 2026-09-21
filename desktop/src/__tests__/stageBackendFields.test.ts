/**
 * stageBackendFields 测试（0.5.0 批次 2：每阶段独立后端）。
 *
 * 锁定「大阶段独立 API」卡片清单的派生口径：
 *  - 后端清单可达时按阶段顺序派生，validate/compress 无独立槽位被跳过
 *  - 槽位为旧键 metadata 的阶段不单独出卡片（由末尾旧键条目代表）
 *  - 后端不可达（空清单 / undefined）时退回旧 4 键，界面不空
 *  - 按 key 去重，防后端清单异常导致重复渲染
 */
import { describe, it, expect } from "vitest";
import {
  buildStageBackendFields,
  LEGACY_STAGE_BACKEND_FIELDS,
} from "../lib/stageBackendFields";
import type { PipelineStageInfo } from "../lib/api/types";

/** 与后端 pipeline_stages.to_payload() 的 stages[] 同构的最小替身。 */
function stage(
  key: string,
  order: number,
  backendSlot: string,
  label = key,
): PipelineStageInfo {
  return {
    key,
    label,
    order,
    backend_slot: backendSlot,
  } as PipelineStageInfo;
}

// 与后端 PIPELINE_STAGES 一致：9 阶段，validate/compress 槽位为空
const BACKEND_STAGES: PipelineStageInfo[] = [
  stage("validate", 0, "", "输入校验"),
  stage("compress", 1, "", "文本压缩"),
  stage("global_prompt", 2, "global_prompt", "全局游戏分析"),
  stage("gen_dic", 3, "gen_dic", "术语表构建"),
  stage("file_meta", 4, "file_meta", "文件级元数据"),
  stage("plot_route", 5, "plot_route", "剧情路线图"),
  stage("batch_meta", 6, "batch_meta", "批次级元数据"),
  stage("translate", 7, "translate", "翻译执行"),
  stage("improve", 8, "afterTrans", "修复和改进译文"),
];

describe("buildStageBackendFields 后端清单可达", () => {
  it("按阶段顺序派生，跳过无独立槽位的阶段", () => {
    const keys = buildStageBackendFields(BACKEND_STAGES).map((f) => f.key);
    expect(keys).not.toContain("validate");
    expect(keys).not.toContain("compress");
    expect(keys).toEqual([
      "global_prompt",
      "gen_dic",
      "file_meta",
      "plot_route",
      "batch_meta",
      "translate",
      "afterTrans",
      "proofread",
      "metadata",
    ]);
  });

  it("label 带阶段序号，desc 取前端维护的人话说明", () => {
    const f = buildStageBackendFields(BACKEND_STAGES).find(
      (x) => x.key === "plot_route",
    );
    expect(f?.label).toBe("剧情路线图（阶段 5）");
    expect(f?.desc).toContain("剧情路线图");
  });

  it("未知阶段回退到通用 desc，不产生空说明", () => {
    const f = buildStageBackendFields([
      stage("brand_new", 9, "brand_new", "新阶段"),
    ]).find((x) => x.key === "brand_new");
    expect(f?.desc).toBe("后端槽位：brand_new");
  });

  it("末尾始终保留旧键 metadata 与预留 proofread", () => {
    const fields = buildStageBackendFields(BACKEND_STAGES);
    const tail = fields.slice(-2).map((f) => f.key);
    expect(tail).toEqual(["proofread", "metadata"]);
    expect(fields.find((f) => f.key === "proofread")?.reserved).toBe(true);
  });
});

describe("buildStageBackendFields 后端不可达兜底", () => {
  it.each([[undefined], [null], [[]]])(
    "空清单退回旧 4 键（stages=%s）",
    (stages) => {
      expect(buildStageBackendFields(stages as PipelineStageInfo[])).toEqual(
        LEGACY_STAGE_BACKEND_FIELDS,
      );
    },
  );

  it("兜底清单不丢 translate / afterTrans", () => {
    const keys = LEGACY_STAGE_BACKEND_FIELDS.map((f) => f.key);
    expect(keys).toContain("translate");
    expect(keys).toContain("afterTrans");
    expect(keys).toContain("metadata");
    expect(keys).toContain("proofread");
  });
});

describe("buildStageBackendFields 去重", () => {
  it("多个阶段共用同一槽位时只出一次卡片", () => {
    const fields = buildStageBackendFields([
      stage("file_meta", 4, "metadata", "文件级元数据"),
      stage("batch_meta", 6, "metadata", "批次级元数据"),
    ]);
    expect(fields.filter((f) => f.key === "metadata")).toHaveLength(1);
  });

  it("后端清单重复给出同一槽位时只出一张卡片", () => {
    const fields = buildStageBackendFields([
      stage("translate", 7, "translate"),
      stage("translate_dup", 7, "translate"),
    ]);
    expect(fields.filter((f) => f.key === "translate")).toHaveLength(1);
  });
});
