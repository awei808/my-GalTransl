/**
 * DictionaryPage 卸载自动保存验证（渲染集成）
 *
 * 覆盖统一自动保存的关键不变式：
 *   1. 编辑字典后卸载页面 → 保存当前字典文件（成功 toast.info）
 *   2. 无编辑卸载 → 不落盘、不提示
 *   3. 切项目场景：项目字典的保存目标始终是「挂载时项目」，绝不使用切走后的新项目 pid
 *   4. 失焦不落盘：自动保存仅发生在切换页面/卸载（方案 A 收敛时机）
 *   5. 人名表：编辑后卸载 / 切出 tab 时保存（不再每键保存）
 *   6. 人名保存在途期间继续输入 → 新编辑不丢失（P1 快照比对）
 *   7. 字典保存在途期间卸载 → 在途保存不被跳过，最终落盘（P3/B 序列化）
 *   8. 人名提取轮询中切项目 → 结果不写新项目（C disposed 早退）
 *   9. 配置名探测完成后卸载 → 用真实配置名保存（D 边界）
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
  createProjectDictionaryFile,
  fetchNameDict,
  fetchNameTable,
  generateNameTable,
  saveNameTable,
} from "../lib/api/project";
import { fetchJob } from "../lib/api/general";

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
  vi.mocked(saveNameTable).mockResolvedValue({ success: true } as never);
});

afterEach(() => {
  vi.useRealTimers(); // 恢复 fake timers（C 用例启用），避免影响后续用例
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

/** 点击指定 tab（按文案匹配） */
function clickTab(label: string) {
  const btn = Array.from(document.querySelectorAll(".dict-tab")).find((b) =>
    b.textContent?.includes(label),
  );
  expect(btn, `找不到 tab ${label}`).toBeTruthy();
  fireEvent.click(btn!);
}

/** 切换到人名 tab 并等待输入框渲染 */
async function gotoNamesTab() {
  vi.mocked(fetchNameTable).mockResolvedValue({
    project_dir: PID,
    source_file: null,
    names: [{ src_name: "旧源", dst_name: "旧译", count: 1 }],
  });
  clickTab("人名替换");
  await vi.waitFor(() => {
    expect(document.querySelectorAll(".name-input").length).toBeGreaterThan(0);
  });
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
    expect(vi.mocked(saveProjectDictionaryFile)).toHaveBeenCalledWith(
      PID,
      expect.objectContaining({
        file_key: DICT_KEY,
        content: "新行",
        config_file_name: "config.yaml",
      }),
    );
    await vi.waitFor(() => {
      expect(infoSpy).toHaveBeenCalledWith("已自动保存 GPT字典.txt", 3000);
    });
  });

  it("无编辑卸载 → 不落盘、不提示", async () => {
    const infoSpy = vi.spyOn(toast, "info").mockImplementation(() => "t");
    const errorSpy = vi.spyOn(toast, "error").mockImplementation(() => "t");
    const { unmount } = await renderLoaded();
    unmount();
    await new Promise((r) => setTimeout(r, 20));
    expect(vi.mocked(saveProjectDictionaryFile)).not.toHaveBeenCalled();
    expect(infoSpy).not.toHaveBeenCalled();
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it("切项目场景：项目字典保存目标始终为挂载项目，绝不使用新项目 pid 写错", async () => {
    const { unmount } = await renderLoaded();
    editDraft("新行");
    setAppState({ activeProjectId: "projB", activeView: "translate", activeFilePath: null });
    unmount();
    await new Promise((r) => setTimeout(r, 50));
    expect(vi.mocked(saveProjectDictionaryFile)).toHaveBeenCalled();
    for (const call of vi.mocked(saveProjectDictionaryFile).mock.calls) {
      expect(call[0]).toBe(PID);
    }
  });
});

describe("DictionaryPage 自动保存时机收敛（方案 A）", () => {
  it("textarea 失焦不落盘（仅切换页面/卸载时才保存）", async () => {
    const { unmount } = await renderLoaded();
    editDraft("新行");
    const ta = document.querySelector(".dict-textarea") as HTMLTextAreaElement;
    fireEvent.blur(ta);
    await new Promise((r) => setTimeout(r, 20));
    expect(vi.mocked(saveProjectDictionaryFile)).not.toHaveBeenCalled();
    unmount();
    await vi.waitFor(() => {
      expect(vi.mocked(saveProjectDictionaryFile)).toHaveBeenCalled();
    });
  });

  it("人名输入不再每键保存：编辑人名后失焦不落盘，卸载时统一保存", async () => {
    const infoSpy = vi.spyOn(toast, "info").mockImplementation(() => "t");
    const { unmount } = await renderLoaded();
    await gotoNamesTab();
    const inputs = document.querySelectorAll(".name-input");
    fireEvent.input(inputs[0], { target: { value: "新源" } });
    await new Promise((r) => setTimeout(r, 20));
    fireEvent.blur(inputs[0]);
    await new Promise((r) => setTimeout(r, 20));
    expect(vi.mocked(saveNameTable)).not.toHaveBeenCalled();
    unmount();
    await vi.waitFor(() => {
      expect(vi.mocked(saveNameTable)).toHaveBeenCalled();
    });
    expect(vi.mocked(saveNameTable)).toHaveBeenCalledWith(
      PID,
      expect.arrayContaining([expect.objectContaining({ src_name: "新源" })]),
    );
    await vi.waitFor(() => {
      expect(infoSpy).toHaveBeenCalledWith("已自动保存 人名表", 3000);
    });
  });

  it("names tab 编辑人名后切出 tab → 保存人名表", async () => {
    const { unmount } = await renderLoaded();
    await gotoNamesTab();
    const inputs = document.querySelectorAll(".name-input");
    fireEvent.input(inputs[0], { target: { value: "新源" } });
    clickTab("GPT 字典");
    await vi.waitFor(() => {
      expect(vi.mocked(saveNameTable)).toHaveBeenCalled();
    });
    expect(vi.mocked(saveNameTable)).toHaveBeenCalledWith(
      PID,
      expect.arrayContaining([expect.objectContaining({ src_name: "新源" })]),
    );
    unmount();
  });
});

describe("DictionaryPage 竞态与边界（P1/P3/B/C/D）", () => {
  it("人名保存在途期间继续输入 → 新编辑不丢失（dirty 保留并再次落盘）", async () => {
    let resolveSave!: (v: never) => void;
    vi.mocked(saveNameTable).mockImplementation(
      () => new Promise((r) => (resolveSave = r)) as never,
    );
    const { unmount } = await renderLoaded();
    await gotoNamesTab();
    const inputs = () => document.querySelectorAll(".name-input");
    fireEvent.input(inputs()[0], { target: { value: "新源1" } });
    // 切出 names tab：触发人名保存（pending，tab 尚未切换，输入框仍在）
    clickTab("GPT 字典");
    await vi.waitFor(() => {
      expect(vi.mocked(saveNameTable)).toHaveBeenCalled();
    });
    // 在途保存期间继续输入
    fireEvent.input(inputs()[0], { target: { value: "新源2" } });
    // 完成在途保存：dirty 应保留（保存期间有新编辑）
    resolveSave({ success: true } as never);
    await new Promise((r) => setTimeout(r, 30));
    // 卸载：再次保存最新数据
    unmount();
    await vi.waitFor(() => {
      expect(vi.mocked(saveNameTable).mock.calls.length).toBeGreaterThanOrEqual(2);
    });
    const calls = vi.mocked(saveNameTable).mock.calls;
    expect(calls[0][1]).toEqual(
      expect.arrayContaining([expect.objectContaining({ src_name: "新源1" })]),
    );
    expect(calls[calls.length - 1][1]).toEqual(
      expect.arrayContaining([expect.objectContaining({ src_name: "新源2" })]),
    );
  });

  it("字典保存在途期间卸载 → 在途保存不被跳过，最终落盘", async () => {
    let resolveSave!: (v: never) => void;
    vi.mocked(saveProjectDictionaryFile).mockImplementation(
      () => new Promise((r) => (resolveSave = r)) as never,
    );
    const { unmount } = await renderLoaded();
    editDraft("新行");
    // 切 tab 触发字典保存（保存在途 pending）
    clickTab("预处理");
    await vi.waitFor(() => {
      expect(vi.mocked(saveProjectDictionaryFile)).toHaveBeenCalled();
    });
    // 在途保存未完成即卸载：doAutoSave 已同步捕获快照，resolve 后必须落盘
    unmount();
    resolveSave({ success: true, file_key: DICT_KEY } as never);
    await vi.waitFor(() => {
      expect(vi.mocked(saveProjectDictionaryFile)).toHaveBeenCalledWith(
        PID,
        expect.objectContaining({ content: "新行", file_key: DICT_KEY }),
      );
    });
  });

  it("人名提取轮询中切项目卸载 → 结果不写新项目", async () => {
    const { unmount } = await renderLoaded();
    vi.useFakeTimers();
    vi.mocked(generateNameTable).mockResolvedValue({ success: true, job_id: "job1" } as never);
    // 可控 fetchJob：首次 running，第二次起 completed，推进轮询直至可能写入结果
    const jobResults: Array<{ status: string }> = [{ status: "running" }, { status: "completed" }];
    const fetchJobMock = vi.mocked(fetchJob).mockImplementation(
      () => Promise.resolve(jobResults.shift() ?? { status: "running" }) as never,
    );
    clickTab("人名替换");
    // 推进微任务使 names 面板渲染
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(0);
    const btn = Array.from(document.querySelectorAll(".dict-name-actions .btn")).find((b) =>
      b.textContent?.includes("提取人名"),
    );
    fireEvent.click(btn!);
    // 推进首轮轮询间隔 → fetchJob#1 (running)
    await vi.advanceTimersByTimeAsync(2000);
    expect(fetchJobMock).toHaveBeenCalledTimes(1);
    // 切项目（模拟 openProject）并卸载：下一轮 fetchJob#2 返回 completed，
    // 但组件已 disposed → 应早退，不读取结果也不保存到新项目
    setAppState({ activeProjectId: "projB", activeView: "translate", activeFilePath: null });
    unmount();
    await vi.advanceTimersByTimeAsync(2000);
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchJobMock).toHaveBeenCalledTimes(2);
    // 早退生效：completed 后不读取结果、不保存（若失效会用新 pid 调 saveNameTable 写错项目）
    expect(vi.mocked(saveNameTable)).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  it("配置名探测中不加载，探测完成后卸载 → 用真实配置名保存", async () => {
    setAppState({ activeProjectId: PID, activeConfigFileName: null, configNameDetecting: true });
    const { unmount } = render(() => <DictionaryPage />);
    await new Promise((r) => setTimeout(r, 30));
    expect(vi.mocked(fetchProjectDictionaryManager)).not.toHaveBeenCalled();
    // 探测完成
    setAppState({ activeConfigFileName: "config.inc.yaml", configNameDetecting: false });
    await vi.waitFor(() => {
      expect(document.querySelector(".dict-textarea")).not.toBeNull();
    });
    editDraft("新行");
    unmount();
    await vi.waitFor(() => {
      expect(vi.mocked(saveProjectDictionaryFile)).toHaveBeenCalled();
    });
    expect(vi.mocked(saveProjectDictionaryFile)).toHaveBeenCalledWith(
      PID,
      expect.objectContaining({ config_file_name: "config.inc.yaml" }),
    );
  });
});

describe("DictionaryPage 显式保存按钮", () => {
  function editorSaveButton(): HTMLButtonElement {
    const btn = document.querySelector<HTMLButtonElement>(
      'button[title="保存当前字典文件的修改"]',
    );
    expect(btn, "找不到字典保存按钮").toBeTruthy();
    return btn!;
  }

  it("编辑字典后点击保存按钮 → 落盘并提示「已保存」", async () => {
    const infoSpy = vi.spyOn(toast, "info").mockImplementation(() => "t");
    const { unmount } = await renderLoaded();
    editDraft("新行");
    fireEvent.click(editorSaveButton());
    await vi.waitFor(() => {
      expect(vi.mocked(saveProjectDictionaryFile)).toHaveBeenCalledWith(
        PID,
        expect.objectContaining({
          file_key: DICT_KEY,
          content: "新行",
          config_file_name: "config.yaml",
        }),
      );
    });
    expect(infoSpy).toHaveBeenCalledWith("已保存 GPT字典.txt", 3000);
    unmount();
  });

  it("创建项目字典文件后 → 列表选中新文件（selectFile 补全 tab 前缀）", async () => {
    const { unmount } = await renderLoaded();
    const newKey = "(project_dir)新字典.txt";
    vi.mocked(createProjectDictionaryFile).mockResolvedValue({
      success: true,
      file_key: newKey,
      path: "",
    } as never);
    // 创建成功后 loadDataWithRetry 重新拉取：返回含新文件的列表
    vi.mocked(fetchProjectDictionaryManager).mockResolvedValue({
      project_dir: PID,
      config_file_name: "config.yaml",
      pre_dict_files: [],
      gpt_dict_files: [DICT_KEY, newKey],
      gpt_dict_files_h: [],
      gpt_dict_files_nh: [],
      post_dict_files: [],
      h_dict_files: [],
      forbidden_dict_files_h: [],
      forbidden_dict_files_nh: [],
      dict_contents: {
        [DICT_KEY]: { path: DICT_KEY, lines: ["旧行"], count: 1, mtime: 1 },
        [newKey]: { path: newKey, lines: [], count: 0, mtime: 1 },
      },
    });
    const input = document.querySelector<HTMLInputElement>('input[placeholder="新文件名"]')!;
    fireEvent.input(input, { target: { value: "新字典.txt" } });
    const createBtn = Array.from(document.querySelectorAll("button")).find(
      (b) => b.textContent?.trim() === "创建",
    )!;
    fireEvent.click(createBtn);
    await vi.waitFor(() => {
      expect(vi.mocked(createProjectDictionaryFile)).toHaveBeenCalled();
    });
    await vi.waitFor(() => {
      const selected = document.querySelector(".dict-file-item.selected");
      expect(selected?.textContent).toContain("新字典.txt");
    });
    unmount();
  });

  it("无编辑点击保存按钮 → 提示无修改且不落盘", async () => {
    const infoSpy = vi.spyOn(toast, "info").mockImplementation(() => "t");
    const { unmount } = await renderLoaded();
    fireEvent.click(editorSaveButton());
    await vi.waitFor(() => {
      expect(infoSpy).toHaveBeenCalledWith("没有需要保存的修改");
    });
    expect(vi.mocked(saveProjectDictionaryFile)).not.toHaveBeenCalled();
    unmount();
  });

  it("人名 tab 编辑后点击保存按钮 → 保存人名表并提示「已保存人名表」", async () => {
    const infoSpy = vi.spyOn(toast, "info").mockImplementation(() => "t");
    const { unmount } = await renderLoaded();
    await gotoNamesTab();
    const inputs = document.querySelectorAll(".name-input");
    fireEvent.input(inputs[0], { target: { value: "新源" } });
    const btn = document.querySelector<HTMLButtonElement>('button[title="保存人名表修改"]');
    expect(btn, "找不到人名表保存按钮").toBeTruthy();
    fireEvent.click(btn!);
    await vi.waitFor(() => {
      expect(vi.mocked(saveNameTable)).toHaveBeenCalledWith(
        PID,
        expect.arrayContaining([expect.objectContaining({ src_name: "新源" })]),
      );
    });
    expect(infoSpy).toHaveBeenCalledWith("已保存人名表", 3000);
    unmount();
  });
});
