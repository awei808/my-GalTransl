/**
 * ReviewPage 卸载自动保存验证（渲染集成）
 *
 * 覆盖统一自动保存的关键不变式：
 *   1. 编辑后卸载页面 → 用「挂载时项目/配置名快照」落盘（非卸载时的全局状态）并 toast.info
 *   2. openProject 已清空全局 dirtyFiles 时卸载 → 仍能保存（dirty 判定不依赖 dirtyFiles）
 *   3. 无未保存修改卸载 → 不落盘、不提示（完全静默）
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, fireEvent } from "@solidjs/testing-library";
import { ReviewPage } from "../pages/review/ReviewPage";
import { appState, setAppState, navigateTo } from "../stores/appStore";
import { confirm } from "../stores/confirmStore";
import { toast } from "../stores/toastStore";

vi.mock("../lib/api/project", () => ({
  fetchCacheFile: vi.fn(),
  fetchCacheHranges: vi.fn(),
  saveCacheFile: vi.fn(),
  fetchPerFileMetadata: vi.fn(),
  savePerFileMetadata: vi.fn(),
  checkCacheProblems: vi.fn(),
  fetchNameDict: vi.fn(),
}));

vi.mock("../lib/api/general", () => ({
  fetchProblemTypes: vi.fn(),
}));

vi.mock("../stores/confirmStore", () => ({
  confirm: { show: vi.fn() },
}));

import {
  fetchCacheFile,
  fetchCacheHranges,
  saveCacheFile,
  fetchPerFileMetadata,
  savePerFileMetadata,
  fetchNameDict,
} from "../lib/api/project";
import { fetchProblemTypes } from "../lib/api/general";

const FILE = "pass3_cache/t01.txt.json";
const PID = "projA";
const META_FILE = "pass1_cache/t01.txt.json.meta.json";
const META_SRC = "t01.txt.json";

beforeEach(() => {
  vi.clearAllMocks();
  setAppState({
    activeProjectId: PID,
    activeConfigFileName: "config.yaml",
    activeFilePath: FILE,
    dirtyFiles: [],
    configNameDetecting: false,
    activeView: "review", // navigateTo 切页拦截依赖当前视图为 review
    pendingView: null, // 重置上次测试的待确认目标
  });
  // 渲染期依赖的 API 全部 resolve
  vi.mocked(fetchCacheFile).mockResolvedValue({
    project_dir: PID,
    filename: FILE,
    entries: [{ index: 1, name: "", pre_src: "原文", post_src: "原文", pre_dst: "旧译文" }],
  });
  vi.mocked(fetchCacheHranges).mockResolvedValue({ h_ranges: [], batch_exists: false, has_h: false });
  vi.mocked(fetchNameDict).mockResolvedValue({ project_dir: PID, name_dict: {} });
  vi.mocked(fetchProblemTypes).mockResolvedValue([]);
  vi.mocked(saveCacheFile).mockResolvedValue({ success: true, filename: FILE });
});

afterEach(() => {
  vi.restoreAllMocks();
  setAppState("activeFilePath", null);
  setAppState("dirtyFiles", []);
});

/** 渲染并等待缓存文件加载完成 */
async function renderLoaded() {
  const result = render(() => <ReviewPage />);
  // 等待 loadFile effect 完成（fetchCacheFile + fetchCacheHranges 已 resolve）
  await vi.waitFor(() => {
    expect(vi.mocked(fetchCacheFile)).toHaveBeenCalledWith(PID, FILE);
  });
  // 等待 setEntries 生效并渲染条目
  await vi.waitFor(() => {
    expect(document.querySelector(".entry-dst-input")).not.toBeNull();
  });
  return result;
}

/** 在主译文框输入并失焦提交草稿（模拟真实编辑） */
function editDraft(value: string) {
  const ta = document.querySelector(".entry-dst-input") as HTMLTextAreaElement;
  ta.focus();
  fireEvent.input(ta, { target: { value } });
  fireEvent.blur(ta); // 失焦提交草稿到 entries 并标脏
}

describe("ReviewPage 卸载自动保存", () => {
  it("编辑后卸载 → 用挂载时 pid/配置名快照落盘并 toast.info", async () => {
    const infoSpy = vi.spyOn(toast, "info").mockImplementation(() => "t");
    const { unmount } = await renderLoaded();
    editDraft("新译文");
    // 卸载触发 onCleanup 同步快照（此刻 loadedFile/entries 仍属挂载时项目）；
    // 卸载完成后全局状态切到别的项目（openProject 场景），验证落盘仍用挂载快照
    unmount();
    setAppState({ activeProjectId: "projB", activeFilePath: null });
    await vi.waitFor(() => {
      expect(vi.mocked(saveCacheFile)).toHaveBeenCalled();
    });
    expect(vi.mocked(saveCacheFile)).toHaveBeenCalledWith(
      PID, // 挂载时项目，而非卸载后的 projB
      FILE,
      expect.arrayContaining([expect.objectContaining({ index: 1, pre_dst: "新译文" })]),
      "config.yaml",
    );
    expect(infoSpy).toHaveBeenCalledWith(`已自动保存 ${FILE}`, 3000);
  });

  it("openProject 清空全局 dirtyFiles 后卸载 → 仍能落盘（不依赖 dirtyFiles）", async () => {
    const { unmount } = await renderLoaded();
    editDraft("新译文");
    // 模拟 openProject 同步重置：dirtyFiles 被清空，但组件内基线仍判定有未保存修改
    setAppState({ dirtyFiles: [] });
    unmount();
    await vi.waitFor(() => {
      expect(vi.mocked(saveCacheFile)).toHaveBeenCalled();
    });
    expect(vi.mocked(saveCacheFile)).toHaveBeenCalledWith(
      PID,
      FILE,
      expect.arrayContaining([expect.objectContaining({ index: 1, pre_dst: "新译文" })]),
      "config.yaml",
    );
  });

  it("无未保存修改卸载 → 不落盘、不提示", async () => {
    const infoSpy = vi.spyOn(toast, "info").mockImplementation(() => "t");
    const errorSpy = vi.spyOn(toast, "error").mockImplementation(() => "t");
    const { unmount } = await renderLoaded();
    unmount();
    await new Promise((r) => setTimeout(r, 20)); // 留出异步保存窗口
    expect(vi.mocked(saveCacheFile)).not.toHaveBeenCalled();
    expect(infoSpy).not.toHaveBeenCalled();
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it("卸载保存失败 → toast.error 提示", async () => {
    const errorSpy = vi.spyOn(toast, "error").mockImplementation(() => "t");
    vi.mocked(saveCacheFile).mockRejectedValue(new Error("网络中断"));
    const { unmount } = await renderLoaded();
    editDraft("新译文");
    unmount();
    await vi.waitFor(() => {
      expect(errorSpy).toHaveBeenCalledWith(`自动保存 ${FILE} 失败：网络中断`);
    });
  });
});

describe("ReviewPage 卸载自动保存（metadata 路）", () => {
  beforeEach(() => {
    setAppState({ activeFilePath: META_FILE });
    vi.mocked(fetchPerFileMetadata).mockResolvedValue({
      exists: true,
      type: "filemeta",
      filename: META_SRC,
      entry: { id: "filemeta", name: "t01.txt.json", content: "旧内容" },
    });
    vi.mocked(savePerFileMetadata).mockResolvedValue({
      success: true,
      type: "filemeta",
      filename: META_SRC,
      path: "",
    });
  });

  /** 渲染 metadata 模式并等待元数据加载完成 */
  async function renderMetaLoaded() {
    const result = render(() => <ReviewPage />);
    await vi.waitFor(() => {
      expect(vi.mocked(fetchPerFileMetadata)).toHaveBeenCalledWith(PID, "filemeta", META_SRC);
    });
    await vi.waitFor(() => {
      expect(document.querySelector(".meta-content-textarea")).not.toBeNull();
    });
    return result;
  }

  it("metadata 编辑后切页卸载 → 由 saveMeta 落盘且不并发双写（captureUnmountSnapshot 跳过在途）", async () => {
    const { unmount } = await renderMetaLoaded();
    const ta = document.querySelector(".meta-content-textarea") as HTMLTextAreaElement;
    ta.focus();
    fireEvent.input(ta, {
      target: { value: '{"id":"filemeta","content":"新内容"}' },
    });
    // 不失焦直接卸载：onCleanup blur 触发 saveMeta(true)（在途），
    // captureUnmountSnapshot 的 metadata 分支应因 metaSavePending 跳过，避免并发双写
    unmount();
    await vi.waitFor(() => {
      expect(vi.mocked(savePerFileMetadata)).toHaveBeenCalled();
    });
    expect(vi.mocked(savePerFileMetadata)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(savePerFileMetadata)).toHaveBeenCalledWith(
      PID,
      "filemeta",
      META_SRC,
      expect.objectContaining({ content: "新内容" }),
    );
    // metadata 模式卸载不得误走 translate 分支（loadedFile 是源文件名，非缓存路径）
    expect(vi.mocked(saveCacheFile)).not.toHaveBeenCalled();
  });

  it("metadata 无编辑卸载 → 不落盘", async () => {
    const { unmount } = await renderMetaLoaded();
    unmount();
    await new Promise((r) => setTimeout(r, 20)); // 留出异步保存窗口
    expect(vi.mocked(savePerFileMetadata)).not.toHaveBeenCalled();
    expect(vi.mocked(saveCacheFile)).not.toHaveBeenCalled();
  });

  it("metadata 编辑后失焦保存成功 → 卸载不重复保存（saveMeta 的 metaDirty 检查）", async () => {
    const { unmount } = await renderMetaLoaded();
    const ta = document.querySelector(".meta-content-textarea") as HTMLTextAreaElement;
    ta.focus();
    fireEvent.input(ta, {
      target: { value: '{"id":"filemeta","content":"新内容"}' },
    });
    fireEvent.blur(ta); // 失焦 → saveMeta(true) 落盘
    await vi.waitFor(() => {
      expect(vi.mocked(savePerFileMetadata)).toHaveBeenCalledTimes(1);
    });
    // 卸载：onCleanup 的 blur() 兜底不应再触发保存（metaDirty 已复位 false）
    unmount();
    await new Promise((r) => setTimeout(r, 20)); // 留出异步保存窗口
    expect(vi.mocked(savePerFileMetadata)).toHaveBeenCalledTimes(1); // 不重复落盘
    expect(vi.mocked(saveCacheFile)).not.toHaveBeenCalled(); // 不误走 translate 保存
  });
});

describe("ReviewPage 切页确认（pendingView）", () => {
  /** 构造确认弹窗返回并渲染 translate 模式 */
  async function setupWithConfirm(result: { confirmed: boolean; action?: string }) {
    vi.mocked(confirm.show).mockResolvedValue(result as never);
    const rendered = await renderLoaded();
    return rendered;
  }

  it("有未保存修改时切页 → 确认「保存」→ 落盘并切换到目标页", async () => {
    const infoSpy = vi.spyOn(toast, "success").mockImplementation(() => "t");
    const { unmount } = await setupWithConfirm({ confirmed: true, action: "confirm" });
    editDraft("新译文");
    // navigateTo 拦截：置 pendingView，不直接切换
    navigateTo("settings");
    expect(appState.activeView).toBe("review");
    expect(appState.pendingView).toBe("settings");
    // 确认保存后：落盘 + 切换到 settings
    await vi.waitFor(() => {
      expect(vi.mocked(saveCacheFile)).toHaveBeenCalled();
    });
    await vi.waitFor(() => {
      expect(appState.activeView).toBe("settings");
    });
    expect(appState.pendingView).toBeNull();
    expect(infoSpy).toHaveBeenCalledWith(`已保存 ${FILE}`);
    unmount();
  });

  it("有未保存修改时切页 → 确认「取消」→ 停留 review，不保存", async () => {
    const { unmount } = await setupWithConfirm({ confirmed: false, action: "extra" });
    editDraft("新译文");
    navigateTo("settings");
    await vi.waitFor(() => {
      expect(appState.pendingView).toBeNull();
    });
    expect(appState.activeView).toBe("review"); // 取消后停留
    expect(vi.mocked(saveCacheFile)).not.toHaveBeenCalled();
    unmount();
  });

  it("有未保存修改时切页 → 确认「不保存」→ 丢弃并切换到目标页", async () => {
    const { unmount } = await setupWithConfirm({ confirmed: false, action: "cancel" });
    editDraft("新译文");
    navigateTo("settings");
    await vi.waitFor(() => {
      expect(appState.activeView).toBe("settings");
    });
    expect(appState.pendingView).toBeNull();
    expect(vi.mocked(saveCacheFile)).not.toHaveBeenCalled(); // 未落盘
    expect(appState.dirtyFiles).not.toContain(FILE); // 已清 dirty
    unmount();
  });

  it("无未保存修改时切页 → 直接切换，不弹确认", async () => {
    const { unmount } = await renderLoaded();
    navigateTo("settings");
    expect(appState.activeView).toBe("settings");
    expect(appState.pendingView).toBeNull();
    expect(vi.mocked(confirm.show)).not.toHaveBeenCalled();
    unmount();
  });

  it("确认「不保存」译文 → 卸载不重存（baselineKey 已重置，丢弃的编辑不被写盘）", async () => {
    vi.mocked(confirm.show).mockResolvedValue({ confirmed: false, action: "cancel" } as never);
    const { unmount } = await renderLoaded();
    editDraft("新译文");
    navigateTo("settings");
    await vi.waitFor(() => {
      expect(appState.activeView).toBe("settings");
    });
    // 卸载：captureUnmountSnapshot 因「不保存」重置 baselineKey 判 clean，不重存丢弃的编辑
    unmount();
    await new Promise((r) => setTimeout(r, 20)); // 留出卸载自动保存窗口
    expect(vi.mocked(saveCacheFile)).not.toHaveBeenCalled();
  });
});

describe("ReviewPage 卸载自动保存（metadata → translate 切换回归）", () => {
  beforeEach(() => {
    setAppState({ activeFilePath: META_FILE });
    vi.mocked(fetchPerFileMetadata).mockResolvedValue({
      exists: true,
      type: "filemeta",
      filename: META_SRC,
      entry: { id: "filemeta", name: "t01.txt.json", content: "旧内容" },
    });
  });

  it("打开过 metadata 后切译文并编辑 → 裸卸载仍自动保存译文（metaLoadedFullPath 已随 loadFile 重置）", async () => {
    // 渲染 metadata 模式：metaLoadedFullPath 置位（beforeEach 已设 activeFilePath=META_FILE）
    const first = render(() => <ReviewPage />);
    await vi.waitFor(() => {
      expect(vi.mocked(fetchPerFileMetadata)).toHaveBeenCalledWith(PID, "filemeta", META_SRC);
    });
    await vi.waitFor(() => {
      expect(document.querySelector(".meta-content-textarea")).not.toBeNull();
    });
    // 切到译文文件：loadFile 应重置 metaLoadedFullPath，卸载快照回到译文分支
    setAppState({ activeFilePath: FILE });
    await vi.waitFor(() => {
      expect(document.querySelector(".entry-dst-input")).not.toBeNull();
    });
    editDraft("新译文");
    // 裸卸载（模拟 openProject/closeProject：组件先卸载、卸载后全局状态才重置；
    // 测试环境无 MainArea，若先改全局会触发 effect 清空 loadedFile，故卸载后再重置）
    first.unmount();
    setAppState({ activeProjectId: "projB", activeFilePath: null, dirtyFiles: [] });
    // 译文编辑不得因 metaLoadedFullPath 残留而静默丢失
    await vi.waitFor(() => {
      expect(vi.mocked(saveCacheFile)).toHaveBeenCalled();
    });
    expect(vi.mocked(saveCacheFile)).toHaveBeenCalledWith(
      PID, // 挂载时项目
      FILE,
      expect.arrayContaining([expect.objectContaining({ index: 1, pre_dst: "新译文" })]),
      "config.yaml",
    );
  });
});
