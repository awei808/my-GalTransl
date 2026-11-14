/**
 * FixReportPanel 交互测试：空态 / 统计条 / 分组折叠 / 条目明细 / 截断提示 / 跳校对页。
 *
 * 组件数据来自 /fix-report 端点（本次任务修复/改进报告，按文件分组），
 * 展示口径与 SidebarPanel 的 ProblemList/AltList 同构（problem-* 样式类）。
 */
import { describe, it, expect, beforeEach } from "vitest";
import { fireEvent, render } from "@solidjs/testing-library";

import { FixReportPanel } from "../components/FixReportPanel";
import { appState, setAppState } from "../stores/appStore";
import type { FixReportResponse } from "../lib/api/types";

const REPORT: FixReportResponse = {
  project_dir: "D:/proj",
  exists: true,
  label: "ForImproveTranslation",
  started_at: "2026-11-07T06:00:00Z",
  finished_at: "2026-11-07T06:01:00Z",
  total: 3,
  truncated: false,
  files: [
    {
      filename: "scene01-1.json",
      count: 2,
      truncated: false,
      entries: [
        {
          filename: "scene01-1.json",
          cache_path: "pass3_cache/scene01-1.json",
          index: 103,
          speaker: "爱丽丝",
          post_src: "こんにちは",
          old_dst: "旧译文一",
          new_dst: "新译文一",
          problems: "残留日文：です",
          backend: "ForFixRound",
          writes_main: true,
        },
        {
          filename: "scene01-1.json",
          index: 107,
          speaker: "",
          post_src: "おはよう",
          old_dst: "旧译文二",
          new_dst: "新译文二",
          problems: "",
          backend: "ForImproveTranslation",
          writes_main: false,
        },
      ],
    },
    {
      filename: "scene02-3.json",
      count: 1,
      truncated: true,
      entries: [
        {
          filename: "scene02-3.json",
          cache_path: "pass3_cache/scene02-3.json",
          index: 55,
          speaker: "",
          post_src: "x",
          old_dst: "旧译文三",
          new_dst: "新译文三",
          problems: "用词不当, 换行位置异常：第2行",
          backend: "ForBRStation",
          writes_main: false,
        },
      ],
    },
  ],
};

function normalizeAppState() {
  setAppState({
    activeView: "home",
    activeFilePath: null,
    reviewJumpToIndex: null,
    sidebarOpen: false,
    sidebarTab: null,
  });
}

describe("FixReportPanel", () => {
  beforeEach(normalizeAppState);

  it("report 为空或无数据显示空态占位（exists=false 不得误报任务未结束）", () => {
    const { container } = render(() => <FixReportPanel report={null} />);
    expect(container.textContent).toContain("暂无修复/改进记录");
    expect(container.textContent).not.toContain("任务尚未结束");
    expect(container.querySelector(".problem-stats")).toBeNull();
    // exists=false（后端重启且无报告文件）与 exists=true 但本次无修复，口径区分
    const noData: FixReportResponse = { ...REPORT, exists: false, total: 0 };
    const { container: c2 } = render(() => <FixReportPanel report={noData} />);
    expect(c2.textContent).toContain("暂无修复/改进记录");
    expect(c2.textContent).not.toContain("任务尚未结束");
  });

  it("任务未结束（finished_at 为空）提示报告待生成", () => {
    const running: FixReportResponse = { ...REPORT, total: 0, finished_at: "" };
    const { container } = render(() => <FixReportPanel report={running} />);
    expect(container.textContent).toContain("任务尚未结束");
  });

  it("任务已结束但无修复（total=0）显示本次无记录", () => {
    const empty: FixReportResponse = { ...REPORT, total: 0, finished_at: "2026-11-07T06:01:00Z" };
    const { container } = render(() => <FixReportPanel report={empty} />);
    expect(container.textContent).toContain("本次任务暂无修复/改进记录");
  });

  it("统计条显示总条数、文件数与完成时间；文件分组默认折叠", () => {
    const { container } = render(() => <FixReportPanel report={REPORT} />);
    expect(container.textContent).toContain("共修复 3 条");
    expect(container.textContent).toContain("涉及 2 个文件");
    expect(container.textContent).toContain("完成于");
    const filenameRows = [...container.querySelectorAll(".problem-filename")].map(
      (e) => e.textContent
    );
    expect(filenameRows).toEqual(["scene01-1.json", "scene02-3.json"]);
    // 默认折叠：不渲染条目行
    expect(container.querySelectorAll(".problem-entry")).toHaveLength(0);
  });

  it("展开文件后渲染条目明细：旧→新、写入位置与问题类型 chips", () => {
    const { container } = render(() => <FixReportPanel report={REPORT} />);
    fireEvent.click(container.querySelector(".problem-toggle")!);
    const entries = [...container.querySelectorAll(".problem-entry")];
    expect(entries).toHaveLength(2);
    const first = entries[0].textContent!;
    expect(first).toContain("#103");
    expect(first).toContain("旧译文一");
    expect(first).toContain("新译文一");
    expect(first).toContain("已覆盖主译文"); // writes_main=true
    expect(first).toContain("统一修复"); // engineLabel("ForFixRound")
    expect(first).toContain("残留日文：です");
    expect(first).toContain("爱丽丝");
    const second = entries[1].textContent!;
    expect(second).toContain("写入备选译文"); // writes_main=false
    expect(second).toContain("译文改进"); // engineLabel("ForImproveTranslation")
    expect(second).not.toContain("已覆盖主译文");
  });

  it("truncated 分组显示截断提示；再次点击收起后条目隐藏", () => {
    const { container } = render(() => <FixReportPanel report={REPORT} />);
    const toggles = container.querySelectorAll(".problem-toggle");
    fireEvent.click(toggles[1]);
    expect(container.textContent).toContain("仅显示前 1 条（共 1 条）");
    // 展开第一组 → 两组条目共 3 条；再点击第一组收起 → 只剩第二组 1 条
    fireEvent.click(toggles[0]);
    expect(container.querySelectorAll(".problem-entry")).toHaveLength(3);
    fireEvent.click(toggles[0]);
    expect(container.querySelectorAll(".problem-entry")).toHaveLength(1);
  });

  it("点击条目跳校对页：用 cache_path 打开文件并展开文件侧栏", () => {
    const { container } = render(() => <FixReportPanel report={REPORT} />);
    const toggles = container.querySelectorAll(".problem-toggle");
    fireEvent.click(toggles[0]);
    fireEvent.click(toggles[1]);
    const entries = [...container.querySelectorAll(".problem-entry")];
    fireEvent.click(entries[0]); // scene01-1.json #103
    expect(appState.activeView).toBe("review");
    expect(appState.reviewJumpToIndex).toBe(103);
    // 流水线文件名不能直接当缓存路径用，必须取 cache_path
    expect(appState.activeFilePath).toBe("pass3_cache/scene01-1.json");
    // 跨视图跳转要显式打开文件侧栏，否则侧栏为空
    expect(appState.sidebarOpen).toBe(true);
    expect(appState.sidebarTab).toBe("explorer");
    fireEvent.click(entries[2]); // scene02-3.json #55
    expect(appState.activeFilePath).toBe("pass3_cache/scene02-3.json");
    expect(appState.reviewJumpToIndex).toBe(55);
  });

  it("cache_path 缺失（旧报告）时回退用 filename 跳转", () => {
    const legacy: FixReportResponse = {
      ...REPORT,
      total: 1,
      files: [
        {
          filename: "old.json",
          count: 1,
          truncated: false,
          entries: [
            {
              filename: "old.json",
              index: 7,
              speaker: "",
              post_src: "s",
              old_dst: "旧",
              new_dst: "新",
              problems: "",
              backend: "ForImproveTranslation",
              writes_main: false,
            },
          ],
        },
      ],
    };
    const { container } = render(() => <FixReportPanel report={legacy} />);
    fireEvent.click(container.querySelector(".problem-toggle")!);
    fireEvent.click(container.querySelector(".problem-entry")!);
    expect(appState.activeView).toBe("review");
    expect(appState.activeFilePath).toBe("old.json");
    expect(appState.reviewJumpToIndex).toBe(7);
    expect(appState.sidebarTab).toBe("explorer");
  });
});
