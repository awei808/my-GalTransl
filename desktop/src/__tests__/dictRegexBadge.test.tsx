/**
 * 字典页 re: 正则支持 UI：编辑器头部帮助文案 + 表格模式「正则」徽标（含非法正则 error 变体）。
 * parse 接口替换为本地桩，返回带 isRegex/regexError 标记的结构化行。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, fireEvent } from "@solidjs/testing-library";
import { DictionaryPage } from "../pages/dictionary/DictionaryPage";
import { setAppState } from "../stores/appStore";

vi.mock("../lib/api/project", () => ({
  fetchProjectDictionaryManager: vi.fn(),
  fetchCommonDictionaryManager: vi.fn(),
  saveProjectDictionaryFile: vi.fn(),
  saveCommonDictionaryFile: vi.fn(),
  createProjectDictionaryFile: vi.fn(),
  createCommonDictionaryFile: vi.fn(),
  deleteProjectDictionaryFile: vi.fn(),
  deleteCommonDictionaryFile: vi.fn(),
  fetchNameDict: vi.fn(),
  fetchNameTable: vi.fn(),
  generateNameTable: vi.fn(),
  saveNameTable: vi.fn(),
}));

vi.mock("../lib/api/general", () => ({
  fetchJob: vi.fn(),
}));

vi.mock("../components/dict/dictUtils", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../components/dict/dictUtils")>();
  return { ...actual, parseDictContent: vi.fn().mockResolvedValue(PARSE_ROWS) };
});

import {
  fetchProjectDictionaryManager,
  fetchCommonDictionaryManager,
  fetchNameDict,
  fetchNameTable,
} from "../lib/api/project";

const PID = "projA";
const P_GPT_A = "(project_dir)项目GPT字典.txt";

// 与后端 parse_dict_line 的 DictRow 输出同构（camelCase 归一化后）；vi.mock 工厂被提升，须经 vi.hoisted 定义
const PARSE_ROWS = vi.hoisted(() => [
  { type: "gpt", values: ["re:あ+い", "中文", "正则词条"], raw: "re:あ+い|中文|正则词条", isRegex: true },
  { type: "gpt", values: ["普通词", "中文", ""], raw: "普通词|中文", isRegex: false },
  { type: "gpt", values: ["re:坏*词", "中文", ""], raw: "re:坏*词|中文", isRegex: true, regexError: "正则可匹配空串" },
  { type: "comment", values: ["// 注释"], raw: "// 注释" },
  { type: "blank", values: [], raw: "" },
]);

function buildProjectRes() {
  return {
    project_dir: PID,
    config_file_name: "config.yaml",
    pre_dict_files: [],
    gpt_dict_files: [P_GPT_A],
    gpt_dict_files_h: [],
    gpt_dict_files_nh: [P_GPT_A],
    post_dict_files: [],
    h_dict_files: [],
    forbidden_dict_files_h: [],
    forbidden_dict_files_nh: [],
    dict_contents: {
      [P_GPT_A]: { path: P_GPT_A, lines: ["re:あ+い|中文|正则词条"], count: 1, mtime: 2 },
    },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  setAppState({
    activeProjectId: PID,
    activeConfigFileName: "config.yaml",
    configNameDetecting: false,
    activeFilePath: null,
    dirtyFiles: [],
  });
  vi.mocked(fetchProjectDictionaryManager).mockResolvedValue(buildProjectRes());
  vi.mocked(fetchCommonDictionaryManager).mockResolvedValue({
    dict_dir: "Dict",
    pre_dict_files: [],
    gpt_dict_files: [],
    gpt_dict_files_h: [],
    gpt_dict_files_nh: [],
    post_dict_files: [],
    h_dict_files: [],
    forbidden_dict_files_h: [],
    forbidden_dict_files_nh: [],
    dict_contents: {},
  });
  vi.mocked(fetchNameDict).mockResolvedValue({ project_dir: PID, name_dict: {} });
  vi.mocked(fetchNameTable).mockResolvedValue({ project_dir: PID, source_file: null, names: [] });
});

afterEach(() => {
  vi.clearAllMocks();
  setAppState("activeProjectId", null);
});

async function renderLoaded() {
  const result = render(() => <DictionaryPage />);
  await vi.waitFor(() => {
    expect(document.querySelector(".dict-textarea")).not.toBeNull();
  });
  return result;
}

function clickCardMode() {
  const btn = Array.from(document.querySelectorAll(".dict-view-btn")).find(
    (b) => b.textContent === "卡片",
  );
  expect(btn, "找不到「卡片」视图按钮").toBeTruthy();
  fireEvent.click(btn!);
}

describe("字典页 re: 正则帮助文案与「正则」徽标", () => {
  it("文本模式：编辑器头部下方显示 re: 用法帮助文案", async () => {
    await renderLoaded();
    const hint = document.querySelector(".dict-regex-hint");
    expect(hint).not.toBeNull();
    expect(hint!.textContent).toContain("re:");
    expect(hint!.textContent).toContain("1^");
    expect(hint!.textContent).toContain("字面量");
  });

  it("卡片模式：isRegex 行搜索词列显示「正则」徽标，非法正则为 error 变体", async () => {
    await renderLoaded();
    clickCardMode();
    await vi.waitFor(() => {
      expect(document.querySelector(".dict-table")).not.toBeNull();
    });
    // 两个正则词条（1 合法 + 1 非法），普通词与注释行无徽标
    expect(document.querySelectorAll(".dict-regex-badge")).toHaveLength(2);
    expect(document.querySelectorAll(".dict-cell-regex-wrap")).toHaveLength(2);
    const errorBadge = document.querySelector(".dict-regex-badge--error");
    expect(errorBadge).not.toBeNull();
    expect(errorBadge!.getAttribute("title")).toContain("正则可匹配空串");
    const okBadges = Array.from(document.querySelectorAll(".dict-regex-badge:not(.dict-regex-badge--error)"));
    expect(okBadges).toHaveLength(1);
    expect(okBadges[0].getAttribute("title")).toBe("re: 前缀正则词条");
  });
});
