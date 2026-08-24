/**
 * 统计 fetchProjectDictionaryManager / fetchCommonDictionaryManager 调用次数。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "@solidjs/testing-library";
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

import {
  fetchProjectDictionaryManager,
  fetchCommonDictionaryManager,
  fetchNameDict,
  fetchNameTable,
} from "../lib/api/project";

const PID = "projA";
const P_GPT_A = "(project_dir)项目GPT字典.txt";
const P_GPT_B = "(project_dir)项目GPT字典-生成.txt";
const C_GPT_H = "GPT字典_h.txt";

function buildProjectRes() {
  return {
    project_dir: PID,
    config_file_name: "config.yaml",
    pre_dict_files: [],
    gpt_dict_files: [P_GPT_A, P_GPT_B],
    gpt_dict_files_h: [],
    gpt_dict_files_nh: [P_GPT_A, P_GPT_B],
    post_dict_files: [],
    h_dict_files: [],
    forbidden_dict_files_h: [],
    forbidden_dict_files_nh: [],
    dict_contents: {
      [P_GPT_A]: { path: P_GPT_A, lines: ["A行1"], count: 1, mtime: 2 },
      [P_GPT_B]: { path: P_GPT_B, lines: ["B行1"], count: 1, mtime: 1 },
    },
  };
}

function buildCommonRes() {
  return {
    dict_dir: "Dict",
    pre_dict_files: [],
    gpt_dict_files: [C_GPT_H],
    gpt_dict_files_h: [C_GPT_H],
    gpt_dict_files_nh: [],
    post_dict_files: [],
    h_dict_files: [],
    forbidden_dict_files_h: [],
    forbidden_dict_files_nh: [],
    dict_contents: { [C_GPT_H]: { path: "Dict/" + C_GPT_H, lines: ["H行1"], count: 1, mtime: 5 } },
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
  vi.mocked(fetchCommonDictionaryManager).mockResolvedValue(buildCommonRes());
  vi.mocked(fetchNameDict).mockResolvedValue({ project_dir: PID, name_dict: {} });
  vi.mocked(fetchNameTable).mockResolvedValue({ project_dir: PID, source_file: null, names: [] });
});

afterEach(() => {
  vi.restoreAllMocks();
  setAppState("activeProjectId", null);
});

describe("加载循环检测", () => {
  it("渲染后 500ms 内不应反复调用加载接口", async () => {
    render(() => <DictionaryPage />);
    await new Promise((r) => setTimeout(r, 500));
    const n1 = vi.mocked(fetchProjectDictionaryManager).mock.calls.length;
    const n2 = vi.mocked(fetchCommonDictionaryManager).mock.calls.length;
    console.log(`[COUNT] 500ms 内 project 加载调用: ${n1}, common 加载调用: ${n2}`);
    // 正常情况：初始加载 1 次；循环 bug 会达到几十上百次
    expect(n1).toBeLessThan(5);
  });
});
