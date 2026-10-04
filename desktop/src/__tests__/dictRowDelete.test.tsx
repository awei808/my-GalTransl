/**
 * 字典页卡片模式行删除：每个非空行提供「删除」按钮（表头「操作」列），
 * 删除只改本地草稿（不直接落盘）；注释行同样可删；空行不渲染因而无按钮。
 * parse 接口替换为固定行桩，验证的是前端行删除与序列化路径。
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
  // 按内容解析的桩（| 切列、// 注释、空行）：删除后 effect 触发的重解析会按新草稿文本
  // 重建行，测试因此能覆盖「删除 → 重解析」闭环而非固定行表；pre 类目额外产出条件/场景行
  return {
    ...actual,
    parseDictContent: vi.fn((content: string, category?: string) =>
      Promise.resolve(PARSE_STUB.parse(content, category)),
    ),
  };
});

import {
  fetchProjectDictionaryManager,
  fetchCommonDictionaryManager,
  fetchNameDict,
  fetchNameTable,
  saveProjectDictionaryFile,
  saveCommonDictionaryFile,
} from "../lib/api/project";

const PID = "projA";
const P_GPT_A = "(project_dir)项目GPT字典.txt";
const P_PRE_A = "(project_dir)预处理字典.txt";

// vi.mock 工厂被提升，桩定义须经 vi.hoisted 提前创建
const PARSE_STUB = vi.hoisted(() => ({
  parse(content: string, category?: string): Array<Record<string, unknown>> {
    return content.split("\n").map((line) => {
      if (!line.trim()) return { type: "blank", values: [], raw: line };
      if (line.trimStart().startsWith("//")) return { type: "comment", values: [line], raw: line };
      const parts = line.split("|");
      // %% 前缀行产出 gpt 类型行（pre 类目下不可混排类型组合的兜底守卫测试桩）
      if (category === "pre" && line.startsWith("%%")) {
        const gptParts = line.slice(2).split("|");
        return { type: "gpt", values: [gptParts[0] ?? "", gptParts[1] ?? "", ""], raw: line };
      }
      // 键名与后端 GalTransl.Dictionary._CONDITIONAL_KEYS / _SITUATION_KEYS 对齐；
      // pre 与 post 同构（替换类词典均支持普通/条件/场景行）
      const isReplaceCat = category === "pre" || category === "post";
      if (
        isReplaceCat &&
        ["pre_src", "post_src", "pre_dst", "post_dst"].includes(parts[0] ?? "")
      ) {
        return {
          type: "conditional",
          values: [
            parts[0] ?? "",
            parts[1] ?? "",
            parts[2] ?? "",
            parts[3] ?? "",
            parts.slice(4).join("|"),
          ],
          raw: line,
          target: parts[0] ?? "",
          condItems: [
            {
              word: parts[1] ?? "",
              op: "",
              negate: false,
              startswith: false,
              endswith: false,
              placeholder: false,
            },
          ],
          splWord: "",
          note: parts.slice(4).join("|"),
        };
      }
      if (isReplaceCat && ["mono", "diag"].includes(parts[0] ?? "")) {
        return {
          type: "situation",
          values: [parts[0] ?? "", parts[1] ?? "", parts[2] ?? "", parts.slice(3).join("|")],
          raw: line,
        };
      }
      return {
        type: isReplaceCat ? "normal" : "gpt",
        values: [parts[0] ?? "", parts[1] ?? "", parts.slice(2).join("|")],
        raw: line,
        isRegex: false,
      };
    });
  },
}));

const INITIAL_TEXT = ["甲词|甲译|甲注", "乙词|乙译", "// 注释", "丙词|丙译", ""].join("\n");
// 预处理 tab：普通行（3 列）与条件行（5 列）混排，行类型不一致 → 卡片视图应自动退回文本
const PRE_MIXED_TEXT = ["搜索A|替换A", "pre_src|有|搜索B|替换B|备注"].join("\n");
const PRE_UNIFORM_TEXT = ["搜索A|替换A", "搜索B|替换B"].join("\n");
const PRE_CONDITIONAL_TEXT = ["pre_src|有|搜索A|替换A", "post_src|无|搜索B|替换B"].join("\n");
const PRE_SITUATION_TEXT = ["mono|搜索A|替换A", "diag|搜索B|替换B"].join("\n");

function buildProjectRes(preFile?: { name: string; text: string }) {
  const preFiles = preFile ? [preFile.name] : [];
  return {
    project_dir: PID,
    config_file_name: "config.yaml",
    pre_dict_files: preFiles,
    gpt_dict_files: [P_GPT_A],
    gpt_dict_files_h: [],
    gpt_dict_files_nh: [P_GPT_A],
    post_dict_files: [],
    h_dict_files: [],
    forbidden_dict_files_h: [],
    forbidden_dict_files_nh: [],
    dict_contents: {
      [P_GPT_A]: { path: P_GPT_A, lines: INITIAL_TEXT.split("\n"), count: 4, mtime: 2 },
      ...(preFile
        ? {
            [preFile.name]: {
              path: preFile.name,
              lines: preFile.text.split("\n"),
              count: preFile.text.split("\n").length,
              mtime: 3,
            },
          }
        : {}),
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

function viewButton(label: string): HTMLButtonElement {
  const btn = Array.from(document.querySelectorAll(".dict-view-btn")).find(
    (b) => b.textContent === label,
  );
  expect(btn, `找不到「${label}」视图按钮`).toBeTruthy();
  return btn as HTMLButtonElement;
}

function tabButton(label: string): HTMLButtonElement {
  const btn = Array.from(document.querySelectorAll(".dict-tab")).find(
    (b) => b.querySelector(".dict-tab-label")?.textContent === label,
  );
  expect(btn, `找不到「${label}」tab`).toBeTruthy();
  return btn as HTMLButtonElement;
}

async function renderCardMode() {
  await renderLoaded();
  fireEvent.click(viewButton("卡片"));
  await vi.waitFor(() => {
    expect(document.querySelector(".dict-table")).not.toBeNull();
  });
}

/** 表格内所有可编辑输入框的当前值（行序 × 列序） */
function cellInputValues(): string[] {
  return Array.from(document.querySelectorAll(".dict-cell-input")).map(
    (el) => (el as HTMLInputElement).value,
  );
}

describe("字典卡片模式行删除", () => {
  it("每个非空行都有删除按钮，表头末列为「操作」，注释行结构为文字格 + 操作格", async () => {
    await renderCardMode();
    // 3 个数据行 + 1 个注释行；空行不渲染，故无删除按钮
    expect(document.querySelectorAll(".dict-row-del")).toHaveLength(4);
    const headers = Array.from(document.querySelectorAll(".dict-table thead th")).map(
      (th) => th.textContent,
    );
    expect(headers).toEqual(["原文", "译文", "解释(可空)", "操作"]);
    // 数据行：3 个数据格 + 1 个操作格（colspan=1），总列数与表头一致
    const dataCells = document
      .querySelector(".dict-table tbody tr.dict-row--gpt")!
      .querySelectorAll("td");
    expect(dataCells).toHaveLength(4);
    expect(dataCells[3].getAttribute("colspan")).toBe("1");
    // 注释行：整行文字格（colspan）+ 操作格，总列数与表头一致
    const commentRow = document.querySelector(".dict-row--comment")!;
    const commentCells = commentRow.querySelectorAll("td");
    expect(commentCells).toHaveLength(2);
    expect(commentCells[0].getAttribute("colspan")).toBe("3");
    expect(commentCells[1].querySelector(".dict-row-del")).not.toBeNull();
  });

  it("删除数据行后该行消失、其余保留，且只改草稿不落盘", async () => {
    await renderCardMode();
    fireEvent.click(document.querySelectorAll(".dict-row-del")[0]);
    await vi.waitFor(() => {
      expect(document.querySelectorAll(".dict-row-del")).toHaveLength(3);
    });
    // 首行（甲词）被移除，乙/丙数据行原样保留
    expect(cellInputValues()).toEqual(["乙词", "乙译", "", "丙词", "丙译", ""]);
    // 删除仅改草稿：落盘仍由保存按钮/自动保存统一负责
    expect(saveProjectDictionaryFile).not.toHaveBeenCalled();
    expect(saveCommonDictionaryFile).not.toHaveBeenCalled();
    // 显式保存：按删除后的草稿落盘（dirty 比对路径不漏写）
    fireEvent.click(
      document.querySelector(
        '.dict-editor-actions button[title="保存当前字典文件的修改"]',
      ) as HTMLButtonElement,
    );
    await vi.waitFor(() => {
      expect(saveProjectDictionaryFile).toHaveBeenCalledTimes(1);
    });
    const payload = vi.mocked(saveProjectDictionaryFile).mock.calls[0][1];
    expect(payload.content).not.toContain("甲词");
    expect(payload.content).toContain("乙词|乙译");
    // 切回文本模式：草稿文本已不含被删行
    fireEvent.click(viewButton("文本"));
    const ta = document.querySelector(".dict-textarea") as HTMLTextAreaElement;
    expect(ta.value).toContain("乙词|乙译");
    expect(ta.value).not.toContain("甲词");
  });

  it("注释行可删除，删除后草稿文本不再包含该注释行", async () => {
    await renderCardMode();
    // DOM 行序：甲 / 乙 / 注释 / 丙
    fireEvent.click(document.querySelectorAll(".dict-row-del")[2]);
    await vi.waitFor(() => {
      expect(document.querySelectorAll(".dict-row-del")).toHaveLength(3);
    });
    expect(document.querySelector(".dict-row--comment")).toBeNull();
    fireEvent.click(viewButton("文本"));
    const ta = document.querySelector(".dict-textarea") as HTMLTextAreaElement;
    expect(ta.value).not.toContain("// 注释");
    expect(ta.value).toContain("丙词|丙译");
  });

  it("删除全部行后回到空态提示，且添加按钮仍可用", async () => {
    await renderCardMode();
    for (let i = 0; i < 4; i++) {
      fireEvent.click(document.querySelectorAll(".dict-row-del")[0]);
    }
    await vi.waitFor(() => {
      expect(document.querySelector(".dict-table")).toBeNull();
    });
    expect(document.querySelector(".dict-table-wrap .dict-editor-empty")?.textContent).toContain(
      "暂无条目",
    );
    expect(document.querySelector(".dict-table-add")).not.toBeNull();
    // 草稿文本被清空（仅剩的 blank 行序列化为空串）
    fireEvent.click(viewButton("文本"));
    const ta = document.querySelector(".dict-textarea") as HTMLTextAreaElement;
    expect(ta.value).toBe("");
  });

  it("预处理 tab：普通行与条件行混排时使用统一 4 列布局，卡片模式可用", async () => {
    vi.mocked(fetchProjectDictionaryManager).mockResolvedValue(
      buildProjectRes({ name: P_PRE_A, text: "// 分区注释\n" + PRE_MIXED_TEXT }),
    );
    await renderLoaded();
    fireEvent.click(tabButton("预处理"));
    await vi.waitFor(() => {
      expect((document.querySelector(".dict-textarea") as HTMLTextAreaElement).value).toContain(
        "pre_src|有|搜索B",
      );
    });
    fireEvent.click(viewButton("卡片"));
    await vi.waitFor(() => {
      expect(document.querySelector(".dict-table")).not.toBeNull();
    });
    // 统一 4 列布局：目标 | 正则搜索（条件+搜索合并） | 替换 | 备注
    const headers = Array.from(document.querySelectorAll(".dict-table thead th")).map(
      (th) => th.textContent,
    );
    expect(headers).toEqual(["目标", "正则搜索", "替换", "备注", "操作"]);
    // 普通行：目标格为固定「普通」占位，合并格内仅搜索输入框
    const normalRow = document.querySelector(".dict-table tbody tr.dict-row--normal")!;
    expect(normalRow.querySelector(".dict-cell-fixed")?.textContent).toBe("普通");
    expect(normalRow.querySelector(".dict-cell-regexsearch .dict-cell-conds")).toBeNull();
    // 条件行：合并格内含条件 chips 与搜索输入框
    const condRow = document.querySelector(".dict-table tbody tr.dict-row--conditional")!;
    expect(condRow.querySelector(".dict-cell-regexsearch .dict-cell-conds")).not.toBeNull();
    expect(condRow.querySelector(".dict-cell-regexsearch .dict-cell-input")).not.toBeNull();
    // 混排下注释行仍可删除：文字格 colspan=4（表头列数）+ 操作格
    const commentRow = document.querySelector(".dict-row--comment")!;
    expect(commentRow.querySelector("td.dict-cell-comment")?.getAttribute("colspan")).toBe("4");
    expect(commentRow.querySelector(".dict-row-del")).not.toBeNull();
    // 两个数据行 + 1 个注释行均渲染删除按钮；删除条件行后其余保留
    expect(document.querySelectorAll(".dict-row-del")).toHaveLength(3);
    fireEvent.click(
      document.querySelector(".dict-table tbody tr.dict-row--conditional .dict-row-del")!,
    );
    await vi.waitFor(() => {
      expect(document.querySelectorAll(".dict-row-del")).toHaveLength(2);
    });
    expect(document.querySelector(".dict-table tbody tr.dict-row--conditional")).toBeNull();
    expect(document.querySelector(".dict-table tbody tr.dict-row--normal")).not.toBeNull();
  });

  it("混排含不可映射类型（普通 + gpt）时卡片视图退回文本模式（兜底守卫）", async () => {
    vi.mocked(fetchProjectDictionaryManager).mockResolvedValue(
      buildProjectRes({ name: P_PRE_A, text: "%%原文|译文\n搜索A|替换A" }),
    );
    await renderLoaded();
    fireEvent.click(tabButton("预处理"));
    await vi.waitFor(() => {
      expect((document.querySelector(".dict-textarea") as HTMLTextAreaElement).value).toContain(
        "%%原文",
      );
    });
    fireEvent.click(viewButton("卡片"));
    await vi.waitFor(() => {
      expect(document.querySelector(".dict-table")).toBeNull();
    });
    expect(viewButton("文本").classList.contains("active")).toBe(true);
  });

  it("预处理 tab：列数一致时卡片模式可用，删除按钮照常渲染", async () => {
    vi.mocked(fetchProjectDictionaryManager).mockResolvedValue(
      buildProjectRes({ name: P_PRE_A, text: PRE_UNIFORM_TEXT }),
    );
    await renderLoaded();
    fireEvent.click(tabButton("预处理"));
    await vi.waitFor(() => {
      expect((document.querySelector(".dict-textarea") as HTMLTextAreaElement).value).toContain(
        "搜索A|替换A",
      );
    });
    fireEvent.click(viewButton("卡片"));
    await vi.waitFor(() => {
      expect(document.querySelector(".dict-table")).not.toBeNull();
    });
    const headers = Array.from(document.querySelectorAll(".dict-table thead th")).map(
      (th) => th.textContent,
    );
    expect(headers).toEqual(["搜索", "替换", "备注", "操作"]);
    expect(document.querySelectorAll(".dict-row-del")).toHaveLength(2);
    fireEvent.click(document.querySelectorAll(".dict-row-del")[0]);
    await vi.waitFor(() => {
      expect(cellInputValues()).toEqual(["搜索B", "替换B", ""]);
    });
  });

  it("预处理 tab：文本模式改整齐后一次点击即可进卡片（陈旧解析结果不误判）", async () => {
    vi.mocked(fetchProjectDictionaryManager).mockResolvedValue(
      buildProjectRes({ name: P_PRE_A, text: PRE_MIXED_TEXT }),
    );
    await renderLoaded();
    fireEvent.click(tabButton("预处理"));
    const ta = () => document.querySelector(".dict-textarea") as HTMLTextAreaElement;
    await vi.waitFor(() => {
      expect(ta().value).toContain("pre_src|有|搜索B");
    });
    // 文本模式删掉条件行：此时 parsedRows 仍是旧的混排行集，守卫不应据此挡住卡片视图
    fireEvent.input(ta(), { target: { value: PRE_UNIFORM_TEXT } });
    fireEvent.click(viewButton("卡片"));
    await vi.waitFor(() => {
      expect(document.querySelectorAll(".dict-table tbody tr.dict-row--normal")).toHaveLength(2);
    });
    expect(viewButton("文本").classList.contains("active")).toBe(false);
  });

  it("预处理 tab：条件行文件添加条目时追加同构模板，留在卡片模式", async () => {
    vi.mocked(fetchProjectDictionaryManager).mockResolvedValue(
      buildProjectRes({ name: P_PRE_A, text: PRE_CONDITIONAL_TEXT }),
    );
    await renderLoaded();
    fireEvent.click(tabButton("预处理"));
    await vi.waitFor(() => {
      expect((document.querySelector(".dict-textarea") as HTMLTextAreaElement).value).toContain(
        "pre_src|有|搜索A",
      );
    });
    fireEvent.click(viewButton("卡片"));
    await vi.waitFor(() => {
      expect(document.querySelectorAll(".dict-table tbody tr.dict-row--conditional")).toHaveLength(
        2,
      );
    });
    fireEvent.click(document.querySelector(".dict-table-add") as HTMLButtonElement);
    // 新行沿用主体行的目标键（pre_src），避免追加普通行破坏行类型一致性
    await vi.waitFor(() => {
      expect(document.querySelectorAll(".dict-table tbody tr.dict-row--conditional")).toHaveLength(
        3,
      );
    });
    const targetValues = Array.from(
      document.querySelectorAll(".dict-table tbody tr.dict-row--conditional"),
    ).map((row) => (row.querySelector("input.dict-cell-input") as HTMLInputElement).value);
    expect(targetValues).toEqual(["pre_src", "post_src", "pre_src"]);
    expect(viewButton("文本").classList.contains("active")).toBe(false);
    // 追加的模板行确实写进草稿
    fireEvent.click(viewButton("文本"));
    expect((document.querySelector(".dict-textarea") as HTMLTextAreaElement).value).toContain(
      "pre_src||||",
    );
  });

  it("预处理 tab：场景行文件添加条目时追加同构模板（mono），留在卡片模式", async () => {
    vi.mocked(fetchProjectDictionaryManager).mockResolvedValue(
      buildProjectRes({ name: P_PRE_A, text: PRE_SITUATION_TEXT }),
    );
    await renderLoaded();
    fireEvent.click(tabButton("预处理"));
    await vi.waitFor(() => {
      expect((document.querySelector(".dict-textarea") as HTMLTextAreaElement).value).toContain(
        "mono|搜索A",
      );
    });
    fireEvent.click(viewButton("卡片"));
    await vi.waitFor(() => {
      expect(document.querySelectorAll(".dict-table tbody tr.dict-row--situation")).toHaveLength(2);
    });
    fireEvent.click(document.querySelector(".dict-table-add") as HTMLButtonElement);
    // 新行沿用主体行的场景键（mono），保持 4 列结构一致
    await vi.waitFor(() => {
      expect(document.querySelectorAll(".dict-table tbody tr.dict-row--situation")).toHaveLength(3);
    });
    const sceneValues = Array.from(
      document.querySelectorAll(".dict-table tbody tr.dict-row--situation"),
    ).map((row) => (row.querySelector("input.dict-cell-input") as HTMLInputElement).value);
    expect(sceneValues).toEqual(["mono", "diag", "mono"]);
    expect(viewButton("文本").classList.contains("active")).toBe(false);
  });

  it("预处理 tab：文本模式改后仍混排时，解析落地后仍渲染混排 4 列（陈旧行集先放行）", async () => {
    vi.mocked(fetchProjectDictionaryManager).mockResolvedValue(
      buildProjectRes({ name: P_PRE_A, text: PRE_MIXED_TEXT }),
    );
    await renderLoaded();
    fireEvent.click(tabButton("预处理"));
    const ta = () => document.querySelector(".dict-textarea") as HTMLTextAreaElement;
    await vi.waitFor(() => {
      expect(ta().value).toContain("pre_src|有|搜索B");
    });
    // 只改一处错字：草稿变了但仍为混排，点卡片会先用陈旧行集渲染，
    // 解析落地后须复评并保持混排 4 列卡片（依赖 parsedRows 订阅，不误退文本）
    fireEvent.input(ta(), {
      target: { value: "搜索A改|替换A\npre_src|有|搜索B|替换B|备注" },
    });
    fireEvent.click(viewButton("卡片"));
    await vi.waitFor(() => {
      const headers = Array.from(document.querySelectorAll(".dict-table thead th")).map(
        (th) => th.textContent,
      );
      expect(headers).toEqual(["目标", "正则搜索", "替换", "备注", "操作"]);
    });
    expect(viewButton("文本").classList.contains("active")).toBe(false);
  });

  it("后处理 tab：卡片按钮可用，混排文件渲染统一 4 列布局", async () => {
    // 文件挂在 post_dict_files（dict_contents 复用同一文件桩）
    const baseRes = buildProjectRes({ name: P_PRE_A, text: PRE_MIXED_TEXT });
    vi.mocked(fetchProjectDictionaryManager).mockResolvedValue({
      ...baseRes,
      pre_dict_files: [],
      post_dict_files: [P_PRE_A],
    });
    await renderLoaded();
    fireEvent.click(tabButton("后处理"));
    await vi.waitFor(() => {
      expect((document.querySelector(".dict-textarea") as HTMLTextAreaElement).value).toContain(
        "pre_src|有|搜索B",
      );
    });
    // 后处理 tab 不再隐藏卡片按钮
    expect(viewButton("卡片")).toBeTruthy();
    fireEvent.click(viewButton("卡片"));
    await vi.waitFor(() => {
      expect(document.querySelector(".dict-table")).not.toBeNull();
    });
    const headers = Array.from(document.querySelectorAll(".dict-table thead th")).map(
      (th) => th.textContent,
    );
    expect(headers).toEqual(["目标", "正则搜索", "替换", "备注", "操作"]);
  });
});
