import {
  createSignal,
  createEffect,
  For,
  Index,
  Show,
  onCleanup,
} from "solid-js";
import { appState } from "../../stores/appStore";
import { toast } from "../../stores/toastStore";
import { confirm } from "../../stores/confirmStore";
import {
  fetchProjectDictionaryManager,
  createProjectDictionaryFile,
  saveProjectDictionaryFile,
  deleteProjectDictionaryFile,
  fetchCommonDictionaryManager,
  createCommonDictionaryFile,
  deleteCommonDictionaryFile,
  fetchNameDict,
  fetchNameTable,
  generateNameTable,
  saveNameTable,
} from "../../lib/api/project";
import { fetchJob } from "../../lib/api/general";
import type {
  ProjectDictionaryManagerResponse,
  DictionaryCategory,
  NameEntry,
} from "../../lib/api/types";
import { getFilesByTab, parseRows, rowsToText, getFieldLabels } from "../../components/dict/dictUtils";
import type { DictTab } from "../../components/dict/dictUtils";

const TABS: { key: string; label: string }[] = [
  { key: "pre", label: "预处理" },
  { key: "gpt", label: "GPT 字典" },
  { key: "post", label: "后处理" },
  { key: "names", label: "人名替换" },
];

export function DictionaryPage() {
  const [data, setData] = createSignal<ProjectDictionaryManagerResponse | null>(null);
  const [, setLoading] = createSignal(false);
  const [activeTab, setActiveTab] = createSignal<string>("gpt");
  const [selectedFile, setSelectedFile] = createSignal<string | null>(null);
  const [draftText, setDraftText] = createSignal("");
  const [creating, setCreating] = createSignal(false);
  const [newFilename, setNewFilename] = createSignal("");

  // 人名替换状态
  const [nameDict, setNameDict] = createSignal<Record<string, string>>({});
  const [nameEntries, setNameEntries] = createSignal<NameEntry[]>([]);
  const [generating, setGenerating] = createSignal(false);

  onCleanup(() => {
  });

  function onDictChange(value: string) {
    setDraftText(value);
  }

  async function doAutoSave() {
    const key = selectedFile();
    if (!key || !pid()) return;
    try {
      await saveProjectDictionaryFile(pid()!, {
        config_file_name: "config.yaml",
        file_key: key,
        content: draftText(),
      });
    } catch (e: any) {
      console.error("自动保存字典失败", e);
    }
  }

  async function doAutoSaveNames() {
    if (!pid()) return;
    try {
      await saveNameTable(pid()!, nameEntries());
    } catch (e: any) {
      console.error("自动保存人名失败", e);
    }
  }

  const pid = () => appState.activeProjectId;
  const isProject = () => !!pid();

  // 视图模式：card（卡片）| text（纯文本）
  const [viewMode, setViewMode] = createSignal<"card" | "text">("text");

  // 使用 dictUtils 解析当前字典文本为结构化行
  const parsedRows = () => parseRows(draftText(), activeTab() as DictTab);

  /** 更新某行的某个字段值 */
  function updateRowValue(ri: number, colIndex: number, value: string) {
    const rows = parsedRows();
    if (ri < 0 || ri >= rows.length) return;
    const row = rows[ri];
    if (row.type === "blank" || row.type === "comment") return;
    const vals = [...row.values];
    vals[colIndex] = value;
    row.values = vals;
    // 利用 rowsToText 序列化
    const all = [...parsedRows()];
    all[ri] = row;
    setDraftText(rowsToText(all));
  }

  /** 卡片字段标签 */
  function cardFields() {
    const tab = activeTab();
    const row = parsedRows().find(r => r.type !== "blank" && r.type !== "comment");
    if (!row) return getFieldLabels("normal", tab as DictTab);
    return getFieldLabels(row.type, tab as DictTab);
  }

  function addEntry() {
    const text = draftText().trim();
    const tab = activeTab();
    if (tab === "gpt") {
      setDraftText(text ? text + "\n||" : "||");
    } else {
      setDraftText(text ? text + "\n|" : "|");
    }
  }

  async function loadData() {
    setLoading(true);
    try {
      if (!pid()) {
        const res = await fetchCommonDictionaryManager();
        setData(res as any);
        return;
      }

      // 如果切到人名 tab，加载人名数据而非字典文件
      if (activeTab() === "names") {
        await loadNameData();
        return;
      }

      const res = await fetchProjectDictionaryManager(pid()!);
      setData(res as any);
      // 自动选择第一个文件
      const files = getFilesByTab(res as any, activeTab() as DictTab);
      if (files.length > 0 && !selectedFile()) {
        const firstKey = `${activeTab()}_dict:${files[0]}`;
        setSelectedFile(firstKey);
        selectFile(firstKey);
      }
    } catch (e: any) {
      toast.error(`加载字典失败: ${e.message}`);
    } finally {
      setLoading(false);
    }
  }

  async function loadNameData() {
    if (!pid()) return;
    try {
      const [dictRes, tableRes] = await Promise.all([
        fetchNameDict(pid()!),
        fetchNameTable(pid()!).catch(() => null),
      ]);
      setNameDict(dictRes.name_dict ?? {});
      setNameEntries(tableRes?.names ?? []);
    } catch (e: any) {
      toast.error(`加载人名替换失败: ${e.message}`);
    }
  }

  async function handleGenerateNames() {
    if (!pid()) return;
    setGenerating(true);
    try {
      // 1. 提交生成任务（后端返回异步 job_id）
      const submitRes = await generateNameTable(pid()!);
      const jobId = (submitRes as any).job_id;
      if (!jobId) {
        toast.error("提交人名提取任务失败：未返回任务 ID");
        return;
      }

      // 2. 轮询等待任务完成
      const POLL_INTERVAL = 2000;
      const TIMEOUT_MS = 10 * 60 * 1000;
      const start = Date.now();
      let finalStatus: string | null = null;

      while (true) {
        if (Date.now() - start > TIMEOUT_MS) {
          toast.error("人名提取超时");
          return;
        }
        await new Promise((r) => setTimeout(r, POLL_INTERVAL));
        try {
          const s = await fetchJob(jobId);
          if (s.status === "completed" || s.status === "failed" || s.status === "cancelled") {
            finalStatus = s.status;
            break;
          }
        } catch {
          // 网络抖动，继续轮询
        }
      }

      if (finalStatus !== "completed") {
        toast.error(`人名提取未成功完成（状态: ${finalStatus}）`);
        return;
      }

      // 3. 从 name-table 接口读取实际结果
      const tableRes = await fetchNameTable(pid()!);
      const names = tableRes.names ?? [];
      setNameEntries(names);
      toast.success(`已提取 ${names.length} 个人名`);
      doAutoSaveNames();
    } catch (e: any) {
      toast.error(`提取人名失败: ${e.message}`);
    } finally {
      setGenerating(false);
    }
  }

  function onNameEntryChange(index: number, field: "src_name" | "dst_name", value: string) {
    const next = [...nameEntries()];
    next[index] = { ...next[index], [field]: value };
    setNameEntries(next);
    doAutoSaveNames();
  }

  createEffect(() => {
    if (pid() || !pid()) loadData();
  });

  function selectFile(fileKey: string) {
    setSelectedFile(fileKey);
    const content = data()?.dict_contents?.[fileKey];
    setDraftText(content ? content.lines.join("\n") : "");
  }

  // 切换 tab 时更新选中文件
  createEffect(() => {
    const tab = activeTab();
    if (tab === "names") {
      // 人名替换不需要文件选择
      setSelectedFile(null);
      setDraftText("");
      loadNameData();
      return;
    }
    if (!data()) return;
    const files = getFilesByTab(data() as any, tab as DictTab);
    if (files.length > 0) {
      const key = `${tab}_dict:${files[0]}`;
      selectFile(key);
    } else {
      setSelectedFile(null);
      setDraftText("");
    }
  });

  async function handleCreate() {
    const name = newFilename().trim();
    if (!name) return;
    setCreating(true);
    try {
      if (pid()) {
        const res = await createProjectDictionaryFile(pid()!, {
          config_file_name: "config.yaml",
          category: activeTab() as DictionaryCategory,
          filename: name,
        });
        setNewFilename("");
        toast.success("文件已创建");
        await loadData();
        selectFile(res.file_key);
      } else {
        const res = await createCommonDictionaryFile({
          category: activeTab() as DictionaryCategory,
          filename: name,
        });
        setNewFilename("");
        toast.success("文件已创建");
        await loadData();
        const key = `${activeTab()}_dict:${res.filename}`;
        selectFile(key);
      }
    } catch (e: any) {
      toast.error(`创建失败: ${e.message}`);
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(fileKey: string) {
    const result = await confirm.show({
      title: "删除字典文件",
      message: `确定要删除「${fileKey}」吗？`,
      tone: "danger",
    });
    if (!result.confirmed) return;
    try {
      if (pid()) {
        await deleteProjectDictionaryFile(pid()!, {
          config_file_name: "config.yaml",
          file_key: fileKey,
          delete_file: true,
        });
      } else {
        const fileName = fileKey.split(":")[1];
        await deleteCommonDictionaryFile({ filename: fileName });
      }
      toast.success("文件已删除");
      setSelectedFile(null);
      setDraftText("");
      await loadData();
    } catch (e: any) {
      toast.error(`删除失败: ${e.message}`);
    }
  }

  const activeFiles = () => getFilesByTab(data() as any, activeTab() as DictTab);

  return (
    <div class="page page-dict">
      <h2 class="page-title">字典管理</h2>
      <p class="page-description">
        {isProject() ? "项目字典" : "公共字典"} — 管理翻译用词对照表
      </p>

      {/* ── Tab 栏 ── */}
      <div class="dict-tabs">
        <For each={TABS}>
          {(t) => (
            <button
              class={`dict-tab ${activeTab() === t.key ? "active" : ""}`}
              onClick={() => setActiveTab(t.key)}
            >
              {t.label}
            </button>
          )}
        </For>
      </div>

      <div class="dict-body">
        <Show
          when={activeTab() !== "names"}
          fallback={
            /* ── 人名替换面板 ── */
            <div class="dict-name-panel">
              <div class="dict-name-toolbar">
                <span class="dict-name-count">
                  {nameEntries().length > 0
                    ? `${nameEntries().length} 个人名条目`
                    : Object.keys(nameDict()).length > 0
                      ? `${Object.keys(nameDict()).length} 个静态映射`
                      : "暂无数据"}
                </span>
                <div class="dict-name-actions">
                  <button
                    class="btn btn--sm"
                    onClick={handleGenerateNames}
                    disabled={generating()}
                  >
                    {generating() ? "提取中…" : "提取人名"}
                  </button>
                </div>
              </div>

              <Show
                when={nameEntries().length > 0}
                fallback={
                  <div class="dict-editor-empty">
                    <Show
                      when={Object.keys(nameDict()).length > 0}
                      fallback={"尚未提取或设置人名替换"}
                    >
                      <div class="name-dict-static">
                        <p class="dict-name-hint">静态人名映射（只读）</p>
                        <For each={Object.entries(nameDict())}>
                          {([src, dst]) => (
                            <div class="name-entry-row">
                              <span class="name-entry-src">{src}</span>
                              <span class="name-entry-arrow">→</span>
                              <span class="name-entry-dst">{dst}</span>
                            </div>
                          )}
                        </For>
                      </div>
                    </Show>
                  </div>
                }
              >
                <div class="name-table-header">
                  <span class="name-col-src">原文</span>
                  <span class="name-col-dst">译文</span>
                  <span class="name-col-count">出现次数</span>
                </div>
                <div class="name-table-body">
                  <Index each={nameEntries()}>
                    {(entrySignal, i) => (
                      <div class="name-entry-row editable">
                        <input
                          class="name-entry-src name-input"
                          value={entrySignal().src_name}
                          onInput={(e) => onNameEntryChange(i, "src_name", e.currentTarget.value)}
                          onBlur={doAutoSaveNames}
                        />
                        <span class="name-entry-arrow">→</span>
                        <input
                          class="name-entry-dst name-input"
                          value={entrySignal().dst_name}
                          onInput={(e) => onNameEntryChange(i, "dst_name", e.currentTarget.value)}
                          onBlur={doAutoSaveNames}
                        />
                        <span class="name-col-count-val">{entrySignal().count}</span>
                      </div>
                    )}
                  </Index>
                </div>
              </Show>
            </div>
          }
        >
        <div class="dict-file-list">
          <div class="dict-file-header">
            文件 ({activeFiles().length})
          </div>
          <For each={activeFiles()}>
            {(f) => {
              const key = `${activeTab()}_dict:${f}`;
              return (
                <div class="dict-file-item">
                  <div
                    class={`dict-file-name ${selectedFile() === key ? "selected" : ""}`}
                    onClick={() => selectFile(key)}
                  >
                    {f}
                  </div>
                  <button
                    class="dict-file-del"
                    onClick={() => handleDelete(key)}
                    title="删除"
                  >
                    ×
                  </button>
                </div>
              );
            }}
          </For>
          {activeFiles().length === 0 && (
            <p class="dict-empty">暂无字典文件</p>
          )}

          {/* ── 新建文件 ── */}
          <div class="dict-create">
            <input
              class="find-input"
              placeholder="新文件名"
              value={newFilename()}
              onInput={(e) => setNewFilename(e.currentTarget.value)}
              onKeyDown={(e) => e.key === "Enter" && handleCreate()}
            />
            <button
              class="btn btn--sm"
              onClick={handleCreate}
              disabled={creating() || !newFilename().trim()}
            >
              {creating() ? "创建中…" : "创建"}
            </button>
          </div>
        </div>

        {/* ── 右侧编辑器 ── */}
        <div class="dict-editor">
          <Show
            when={selectedFile()}
            fallback={
              <div class="dict-editor-empty">
                请选择一个字典文件
              </div>
            }
          >
            <div class="dict-editor-header">
              <span class="dict-editor-filename">{selectedFile()}</span>
              <div class="dict-editor-actions">
                <button
                  class={`btn btn--sm ${viewMode() === "text" ? "btn--primary" : ""}`}
                  onClick={() => setViewMode("text")}
                  title="纯文本模式"
                >
                  文本
                </button>
                <button
                  class={`btn btn--sm ${viewMode() === "card" ? "btn--primary" : ""}`}
                  onClick={() => setViewMode("card")}
                  title="卡片模式"
                >
                  卡片
                </button>
              </div>
            </div>

            <Show when={viewMode() === "text"} fallback={
              /* ── 卡片模式 ── */
              <div class="dict-card-list">
                <Show
                  when={parsedRows().filter(r => r.type !== "blank").length > 0}
                  fallback={
                    <div class="dict-editor-empty">
                      暂无条目，点击下方按钮添加
                    </div>
                  }
                >
                  <Index each={parsedRows()}>
                    {(rowSignal, ri) => (
                      <Show when={rowSignal().type !== "blank"}>
                        <div class="dict-card">
                          <Show when={rowSignal().type === "comment"} fallback={
                            <Index each={rowSignal().values}>
                              {(valSignal, ci) => (
                                <>
                                  <Show when={ci > 0}>
                                    <span class="dict-card-arrow">→</span>
                                  </Show>
                                  <input
                                    class="dict-card-input"
                                    value={valSignal()}
                                    onInput={(e) => updateRowValue(ri, ci, e.currentTarget.value)}
                                    placeholder={cardFields()[ci] || ""}
                                  />
                                </>
                              )}
                            </Index>
                          }>
                            <span class="dict-card-comment">{rowSignal().values[0]}</span>
                          </Show>
                        </div>
                      </Show>
                    )}
                  </Index>
                </Show>
                <button class="btn btn--sm dict-card-add" onClick={addEntry}>
                  + 添加条目
                </button>
              </div>
            }>
              <textarea
                class="dict-textarea"
                value={draftText()}
                onInput={(e) => onDictChange(e.currentTarget.value)}
                onBlur={doAutoSave}
                spellcheck={false}
              />
            </Show>
          </Show>
        </div>
        </Show>
      </div>
    </div>
  );
}
