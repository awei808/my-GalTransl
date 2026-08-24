/**
 * 字典页文件切换 + 文件列表 DOM 重建（悬停闪烁根因排查）。
 * 使用真实数据形态：项目字典（带 (project_dir) 标记）与公共字典混合。
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

// 字典解析走后端 parse 接口，测试中替换为本地桩，避免真实网络请求
vi.mock("../components/dict/dictUtils", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../components/dict/dictUtils")>();
  return { ...actual, parseDictContent: vi.fn().mockResolvedValue([]) };
});

import {
  fetchProjectDictionaryManager,
  fetchCommonDictionaryManager,
  saveProjectDictionaryFile,
  fetchNameDict,
  fetchNameTable,
} from "../lib/api/project";

const PID = "projA";
// 项目字典（带标记）
const P_GPT_A = "(project_dir)项目GPT字典.txt";
const P_GPT_B = "(project_dir)项目GPT字典-生成.txt";
// 公共字典（裸文件名）
const C_GPT_H = "GPT字典_h.txt";
const C_GPT_NH = "GPT字典_非h.txt";

function buildProjectRes() {
  return {
    project_dir: PID,
    config_file_name: "config.yaml",
    pre_dict_files: ["(project_dir)项目字典_译前.txt"],
    gpt_dict_files: [P_GPT_A, P_GPT_B],
    gpt_dict_files_h: [],
    gpt_dict_files_nh: [P_GPT_A, P_GPT_B],
    post_dict_files: ["(project_dir)项目字典_译后.txt"],
    h_dict_files: [],
    forbidden_dict_files_h: [],
    forbidden_dict_files_nh: [],
    dict_contents: {
      [P_GPT_A]: { path: P_GPT_A, lines: ["A行1", "A行2"], count: 2, mtime: 2 },
      [P_GPT_B]: { path: P_GPT_B, lines: ["B行1"], count: 1, mtime: 1 },
      "(project_dir)项目字典_译前.txt": { path: "", lines: ["pre行"], count: 1, mtime: 1 },
      "(project_dir)项目字典_译后.txt": { path: "", lines: ["post行"], count: 1, mtime: 1 },
    },
  };
}

function buildCommonRes() {
  return {
    dict_dir: "Dict",
    pre_dict_files: ["00通用字典_译前.txt", "01H字典_矫正_译前.txt"],
    gpt_dict_files: [C_GPT_H, C_GPT_NH],
    gpt_dict_files_h: [C_GPT_H],
    gpt_dict_files_nh: [C_GPT_NH],
    post_dict_files: ["00通用字典_符号_译后.txt", "00通用字典_译后.txt"],
    h_dict_files: [],
    forbidden_dict_files_h: ["禁用词_H.txt"],
    forbidden_dict_files_nh: ["禁用词_非h.txt"],
    dict_contents: {
      [C_GPT_H]: { path: "Dict/" + C_GPT_H, lines: ["H行1"], count: 1, mtime: 5 },
      [C_GPT_NH]: { path: "Dict/" + C_GPT_NH, lines: ["NH行1", "NH行2"], count: 2, mtime: 4 },
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
  vi.mocked(fetchCommonDictionaryManager).mockResolvedValue(buildCommonRes());
  vi.mocked(fetchNameDict).mockResolvedValue({ project_dir: PID, name_dict: {} });
  vi.mocked(fetchNameTable).mockResolvedValue({ project_dir: PID, source_file: null, names: [] });
  vi.mocked(saveProjectDictionaryFile).mockResolvedValue({ success: true, file_key: P_GPT_A });
});

afterEach(() => {
  // 仅清调用记录，不清模块 mock 实现（restoreAllMocks 会清掉 dictUtils 桩的 mockResolvedValue）
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

function taValue(): string {
  return (document.querySelector(".dict-textarea") as HTMLTextAreaElement).value;
}

function clickFile(name: string) {
  const items = Array.from(document.querySelectorAll(".dict-file-item"));
  const item = items.find((el) => el.querySelector(".dict-file-name-text")?.textContent?.includes(name));
  expect(item, `找不到文件项 ${name}，现有: ${items.map((i) => i.textContent).join(", ")}`).toBeTruthy();
  fireEvent.click(item!);
}

describe("字典页切换复现（真实数据形态）", () => {
  it("项目字典切换到公共字典（GPT tab 分组视图）", async () => {
    await renderLoaded();
    // 初始自动选中第一个项目文件（排序按 mtime 降序：A mtime=2 > B mtime=1）
    expect(taValue()).toContain("A行1");

    clickFile("GPT字典_h.txt"); // 公共字典
    await vi.waitFor(() => {
      expect(taValue()).toContain("H行1");
    });
  });

  it("切换到第二个项目文件并检查选中态", async () => {
    await renderLoaded();
    clickFile("项目GPT字典-生成.txt");
    await vi.waitFor(() => {
      expect(taValue()).toContain("B行1");
    });
    const items = Array.from(document.querySelectorAll(".dict-file-item"));
    const selected = items.find((el) => el.classList.contains("selected"));
    expect(selected?.textContent).toContain("项目GPT字典-生成.txt");
  });

  it("编辑当前文件后再切换：切换应正常且保存旧文件", async () => {
    await renderLoaded();
    fireEvent.input(document.querySelector(".dict-textarea") as HTMLTextAreaElement, {
      target: { value: "我改过了A" },
    });
    // 切换到 B
    clickFile("项目GPT字典-生成.txt");
    await vi.waitFor(() => {
      expect(taValue()).toContain("B行1");
    });
    // 旧文件 A 应被保存
    expect(saveProjectDictionaryFile).toHaveBeenCalledWith(
      PID,
      expect.objectContaining({ file_key: P_GPT_A, content: "我改过了A" }),
    );
  });
});
