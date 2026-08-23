/**
 * DictionaryPage 卸载自动保存验证（渲染集成）
 *
 * 覆盖统一自动保存的关键不变式：
 *   1. 编辑字典后卸载页面 → 保存当前字典文件（成功 toast.info）
 *   2. 切项目场景：项目字典的保存目标始终是「挂载时项目」，绝不使用切走后的新项目 pid
 *     （onCleanup 用 mountPid 快照，而非运行时全局 activeProjectId）
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, fireEvent } from "@solidjs/testing-library";
import { DictionaryPage } from "../pages/dictionary/DictionaryPage";
import { setAppState } from "../stores/appStore";
import { toast } from "../stores/toastStore";

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
  saveProjectDictionaryFile,
  fetchNameDict,
  fetchNameTable,
} from "../lib/api/project";

const PID = "projA";
const DICT_KEY = "(project_dir)GPT字典.txt";

beforeEach(() => {
  vi.clearAllMocks();
  setAppState({
    activeProjectId: PID,
    activeConfigFileName: "config.yaml",
    configNameDetecting: false,
    activeFilePath: null,
    dirtyFiles: [],
  });
  // 项目字典：gpt tab 下有一个文件（带项目标记），dict_contents 含初始内容
  vi.mocked(fetchProjectDictionaryManager).mockResolvedValue({
    project_dir: PID,
    config_file_name: "config.yaml",
    pre_dict_files: [],
    gpt_dict_files: [DICT_KEY],
    gpt_dict_files_h: [],
    gpt_dict_files_nh: [],
    post_dict_files: [],
    h_dict_files: [],
    forbidden_dict_files_h: [],
    forbidden_dict_files_nh: [],
    dict_contents: { [DICT_KEY]: { path: DICT_KEY, lines: ["旧行"], count: 1, mtime: 1 } },
  });
  vi.mocked(fetchCommonDictionaryManager).mockResolvedValue({
    dict_dir: "",
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
  vi.mocked(saveProjectDictionaryFile).mockResolvedValue({ success: true, file_key: DICT_KEY });
});

afterEach(() => {
  vi.restoreAllMocks();
  setAppState("activeProjectId", null);
});

/** 渲染并等待字典文件加载与自动选中完成 */
async function renderLoaded() {
  const result = render(() => <DictionaryPage />);
  await vi.waitFor(() => {
    expect(vi.mocked(fetchProjectDictionaryManager)).toHaveBeenCalledWith(PID, "config.yaml");
  });
  await vi.waitFor(() => {
    expect(document.querySelector(".dict-textarea")).not.toBeNull();
  });
  return result;
}

/** 在字典 textarea 输入内容（模拟编辑） */
function editDraft(value: string) {
  const ta = document.querySelector(".dict-textarea") as HTMLTextAreaElement;
  fireEvent.input(ta, { target: { value } });
}

describe("DictionaryPage 卸载自动保存", () => {
  it("编辑字典后卸载 → 用挂载项目 pid 保存并 toast.info", async () => {
    const infoSpy = vi.spyOn(toast, "info").mockImplementation(() => "t");
    const { unmount } = await renderLoaded();
    editDraft("新行");
    unmount();
    await vi.waitFor(() => {
      expect(vi.mocked(saveProjectDictionaryFile)).toHaveBeenCalled();
    });
    // 卸载时 textarea blur 的 onBlur 保存与 onCleanup 卸载保存可能并发（幂等），
    // 断言至少一次、目标均为挂载项目
    expect(vi.mocked(saveProjectDictionaryFile)).toHaveBeenCalledWith(
      PID,
      expect.objectContaining({
        file_key: DICT_KEY,
        content: "新行",
        config_file_name: "config.yaml",
      }),
    );
    // 文案经 stripProjectDirMarker 展示（不带 (project_dir) 前缀）
    expect(infoSpy).toHaveBeenCalledWith("已自动保存 GPT字典.txt", 3000);
  });

  it("无编辑卸载 → 不落盘、不提示", async () => {
    const infoSpy = vi.spyOn(toast, "info").mockImplementation(() => "t");
    const errorSpy = vi.spyOn(toast, "error").mockImplementation(() => "t");
    const { unmount } = await renderLoaded();
    unmount();
    await new Promise((r) => setTimeout(r, 20)); // 留出异步保存窗口
    expect(vi.mocked(saveProjectDictionaryFile)).not.toHaveBeenCalled();
    expect(infoSpy).not.toHaveBeenCalled();
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it("切项目场景：项目字典保存目标始终为挂载项目，绝不使用新项目 pid 写错", async () => {
    const { unmount } = await renderLoaded();
    editDraft("新行");
    // 模拟 openProject 切项目：全局 activeProjectId 同步切到新项目
    setAppState({ activeProjectId: "projB", activeView: "translate", activeFilePath: null });
    unmount();
    await new Promise((r) => setTimeout(r, 50)); // 留出 effect 保存与卸载保存窗口
    expect(vi.mocked(saveProjectDictionaryFile)).toHaveBeenCalled();
    // 所有保存目标均为挂载项目 projA（含项目切换 effect 的旧项目保存），
    // 绝无 projB——若 onCleanup 误用运行时 pid 会把旧字典写入新项目
    for (const call of vi.mocked(saveProjectDictionaryFile).mock.calls) {
      expect(call[0]).toBe(PID);
    }
  });
});
