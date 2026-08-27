import { createSignal, createEffect, createMemo, untrack, For, Index, Show, onCleanup } from "solid-js";
import { sendLog } from "../../lib/api/log";
import { appState, getActiveConfigFileName } from "../../stores/appStore";
import { toast } from "../../stores/toastStore";
import { confirm } from "../../stores/confirmStore";
import {
  fetchProjectDictionaryManager,
  createProjectDictionaryFile,
  saveProjectDictionaryFile,
  deleteProjectDictionaryFile,
  fetchCommonDictionaryManager,
  createCommonDictionaryFile,
  saveCommonDictionaryFile,
  deleteCommonDictionaryFile,
  fetchNameDict,
  fetchNameTable,
  generateNameTable,
  saveNameTable,
} from "../../lib/api/project";
import { fetchJob } from "../../lib/api/general";
import type {
  ProjectDictionaryManagerResponse,
  CommonDictionaryManagerResponse,
  DictionaryCategory,
  NameEntry,
} from "../../lib/api/types";
import {
  getFilesByTab,
  parseDictContent,
  getFieldLabels,
  getTableColumns,
  stripProjectDirMarker,
  stripTabPrefix,
  condSemanticOf,
  applyCondSemantic,
  parseSearchPrefix,
  serializeSearchPrefix,
  rowsToText,
  COND_SEMANTIC_OPTIONS,
  SEARCH_MODE_OPTIONS,
  PROJECT_DIR_MARKER,
  dictFileScene,
} from "../../components/dict/dictUtils";
import type {
  DictRow,
  DictTab,
  DictColumnDef,
  ConditionItem,
} from "../../components/dict/dictUtils";
import { getErrorMessage } from "../../lib/errors";
import { runPageAutosave, autosaveInfo, autosaveError } from "../../lib/usePageAutosave";

const TABS: { key: string; label: string }[] = [
  { key: "pre", label: "预处理" },
  { key: "gpt", label: "GPT 字典" },
  { key: "post", label: "后处理" },
  { key: "forbidden", label: "禁用词" },
  { key: "names", label: "人名替换" },
];

export function DictionaryPage() {
  const [data, setData] = createSignal<ProjectDictionaryManagerResponse | null>(null);
  const [commonData, setCommonData] = createSignal<CommonDictionaryManagerResponse | null>(null);
  const [, setLoading] = createSignal(false);
  const [activeTab, setActiveTab] = createSignal<string>("gpt");
  const [selectedFile, setSelectedFile] = createSignal<string | null>(null);
  const [draftText, setDraftText] = createSignal("");
  const [parsedRows, setParsedRows] = createSignal<DictRow[]>([]);
  const [creating, setCreating] = createSignal(false);
  const [newFilename, setNewFilename] = createSignal("");

  // 人名替换状态
  const [nameDict, setNameDict] = createSignal<Record<string, string>>({});
  const [nameEntries, setNameEntries] = createSignal<NameEntry[]>([]);
  const [generating, setGenerating] = createSignal(false);
  // 卸载自动保存用：挂载时刻的项目 id 快照（切项目时全局 activeProjectId 会先被
  // openProject 重置为新项目，onCleanup 若读运行时 pid 会把旧项目字典保存到新项目，
  // 必须用挂载快照作为保存目标身份，与 ReviewPage 的 mountPid 模式一致）
  // 卸载标志：onCleanup 置位后丢弃飞行中的加载/重试链，避免写回已卸载组件
  let disposed = false;
  const mountPid = appState.activeProjectId;
  // 配置名快照：挂载时回退 config.yaml；openProject 的配置名探测完成后
  //（configNameDetecting 变 false）更新为真实配置名，避免卸载自动保存用错配置名写盘。
  // 边界：探测中（configNameDetecting 仍为 true）卸载会以回退的 config.yaml 落盘，
  // config.inc.yaml 项目此时会写错配置；该窗口仅探测请求数毫秒，接受并靠后续手动保存兜底
  let configNameSnapshot = getActiveConfigFileName();
  createEffect(() => {
    if (!appState.configNameDetecting) {
      configNameSnapshot = getActiveConfigFileName();
    }
  });

  // 人名表是否含未落盘编辑（每键不再落盘，切换/卸载时统一保存）
  let namesDirty = false;

  // 保存序列化：同一页面所有落盘操作排队串行，避免并发双写同一文件
  let pendingSaves = 0;
  let saveChain: Promise<unknown> = Promise.resolve();

  function enqueueSave(task: () => Promise<unknown>): Promise<unknown> {
    // 入队时同步递增在途计数：waitForPendingSave 在入队后立即调用时也能看到在途任务，
    // 避免递增延迟到微任务导致漏检并发双写
    pendingSaves++;
    const run = saveChain.then(async () => {
      try {
        return await task();
      } finally {
        pendingSaves--;
      }
    });
    saveChain = run.then(
      () => {},
      () => {},
    );
    return run;
  }

  /** 等待在途保存全部完成（卸载/切换前调用，带超时兜底，避免双写或漏写） */
  async function waitForPendingSave(): Promise<void> {
    const deadline = Date.now() + 3000;
    while (pendingSaves > 0 && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 30));
    }
  }

  // 卸载自动保存：切页/切项目离开时兜底落盘（对齐全书 runPageAutosave 统一骨架）。
  // 同步段一次性快照保存目标与数据，异步落盘全程用快照，不读运行时全局状态。
  type UnmountSnapshot = {
    dirtyDict: boolean;
    dirtyNames: boolean;
    fileKey: string | null;
    text: string;
    isProjectFile: boolean;
    pid: string | null;
    configName: string;
    nameEntries: NameEntry[];
  };

  function captureUnmountSnapshot(): UnmountSnapshot {
    const key = selectedFile();
    let dirtyDict = false;
    let text = "";
    let isProjectFile = false;
    if (key) {
      text = draftText();
      const fileKey = stripTabPrefix(key);
      isProjectFile = fileKey.includes(PROJECT_DIR_MARKER);
      const snapshot = isProjectFile ? data() : commonData();
      const snapshotEntry = snapshot?.dict_contents?.[fileKey];
      dirtyDict = !(
        snapshotEntry !== undefined &&
        normForCompare(snapshotEntry.lines.join("\n")) === normForCompare(text)
      );
    }
    return {
      dirtyDict,
      dirtyNames: namesDirty && nameEntries().length > 0,
      fileKey: key,
      text,
      isProjectFile,
      pid: mountPid,
      configName: configNameSnapshot,
      nameEntries: nameEntries(),
    };
  }

  /** 卸载落盘：按快照保存字典文件与人名表，任一步失败返回 false（由骨架提示）。 */
  function saveUnmountSnapshot(snap: UnmountSnapshot): Promise<boolean> {
    // 入队串行：即使 waitForPendingSave 超时返回，本保存也排在在途任务之后，避免并发双写
    return enqueueSave(() => saveUnmountSnapshotInner(snap)).then(
      (ok) => ok as boolean,
      () => false,
    );
  }

  async function saveUnmountSnapshotInner(snap: UnmountSnapshot): Promise<boolean> {
    let ok = true;
    if (snap.dirtyDict && snap.fileKey) {
      const fileKey = stripTabPrefix(snap.fileKey);
      try {
        if (snap.pid && snap.isProjectFile) {
          await saveProjectDictionaryFile(snap.pid, {
            config_file_name: snap.configName,
            file_key: fileKey,
            content: snap.text,
          });
        } else {
          await saveCommonDictionaryFile({ filename: fileKey, content: snap.text });
        }
      } catch (e) {
        sendLog(`自动保存失败: ${e}`, "error");
        ok = false;
      }
    }
    if (snap.dirtyNames && snap.pid) {
      try {
        await saveNameTable(snap.pid, snap.nameEntries);
      } catch (e) {
        sendLog(`自动保存人名失败: ${e}`, "error");
        ok = false;
      }
    }
    return ok;
  }

  onCleanup(() => {
    disposed = true;
    const snap = captureUnmountSnapshot();
    void runPageAutosave({
      waitForReady: waitForPendingSave,
      isDirty: () => snap.dirtyDict || snap.dirtyNames,
      save: () => saveUnmountSnapshot(snap),
      successMessage: () => {
        const parts: string[] = [];
        if (snap.dirtyDict && snap.fileKey) parts.push(displayFileName(snap.fileKey));
        if (snap.dirtyNames) parts.push("人名表");
        return parts.length > 0 ? `已自动保存 ${parts.join("、")}` : "";
      },
      failMessage: () => "自动保存失败",
    });
  });

  function onDictChange(value: string) {
    setDraftText(value);
  }

  // 内容比对规范化：忽略 \r（后端 lines 可能保留 CRLF，textarea 已规范化为 LF，避免恒判"有变化"）
  function normForCompare(s: string): string {
    return s.replace(/\r/g, "");
  }

  /** 主动保存当前字典文件（切 tab/切文件/加载前/显式保存按钮调用，串行执行防并发双写）；
      successMessage 可覆盖成功文案（显式保存按钮传「已保存 xxx」）。 */
  function doAutoSave(
    targetPid?: string | null,
    targetConfigName?: string,
    successMessage?: string,
  ): Promise<void> {
    // 调用时同步捕获保存目标快照：切项目 effect 随后会清空 selectedFile/draftText，
    // 若延迟到队列执行再读取会读到已清空的状态导致漏保存
    const key = selectedFile();
    const text = draftText();
    const configName = targetConfigName ?? getActiveConfigFileName();
    return enqueueSave(() =>
      doAutoSaveInner(key, text, configName, targetPid, successMessage),
    ).then(() => {});
  }

  async function doAutoSaveInner(
    key: string,
    text: string,
    configName: string,
    targetPid?: string | null,
    successMessage?: string,
  ) {
    if (!key) return;
    try {
      // 剥离 "{tab}_dict:" 前缀: "gpt_dict:(project_dir)xxx.txt" → "(project_dir)xxx.txt"
      // 公共字典: "gpt_dict:文件名.txt" → "文件名.txt"
      const fileKey = stripTabPrefix(key);
      const isProjectFile = fileKey.includes(PROJECT_DIR_MARKER);
      // 显式指定 targetPid 时用于项目切换场景保存旧项目；null/undefined 回退当前 pid()
      const pidToUse = targetPid ?? pid();
      // 与磁盘快照比对：无实际变化时不落盘、不提示（切 tab/切文件/项目切换等重复保存场景静默，
      // 避免每次切换都发请求与刷 toast）
      const snapshot = isProjectFile ? data() : commonData();
      const snapshotEntry = snapshot?.dict_contents?.[fileKey];
      const unchanged =
        snapshotEntry !== undefined &&
        normForCompare(snapshotEntry.lines.join("\n")) === normForCompare(text);
      if (unchanged) return;
      if (pidToUse && isProjectFile) {
        await saveProjectDictionaryFile(pidToUse, {
          config_file_name: configName,
          file_key: fileKey,
          content: text,
        });
      } else {
        await saveCommonDictionaryFile({
          filename: fileKey,
          content: text,
        });
      }
      autosaveInfo(successMessage ?? `已自动保存 ${displayFileName(key)}`);
      // 常规路径（非项目切换保存）才原地更新快照，避免跨项目保存污染新数据
      if (targetPid === undefined) {
        const snapshot = isProjectFile ? data() : commonData();
        if (snapshot && snapshot.dict_contents) {
          const entry = snapshot.dict_contents[fileKey];
          if (entry) {
            entry.lines = text.split("\n");
            entry.count = entry.lines.length;
          }
        }
      }
    } catch (e) {
      sendLog(`自动保存失败: ${e}`, "error");
      autosaveError("自动保存失败", e);
    }
  }

  /** 保存人名表；targetPid 显式指定时用于切项目/卸载保存旧项目（此时不清 namesDirty）。
      showToast 为字符串时用作成功文案（显式保存按钮传「已保存人名表」）。 */
  function doAutoSaveNames(
    targetPid?: string | null,
    showToast: boolean | string = true,
  ): Promise<void> {
    // 调用时同步捕获发送快照：入队延迟执行期间用户可能继续编辑，须以发送时数据落盘
    const sentNames = nameEntries();
    return enqueueSave(() =>
      doAutoSaveNamesInner(sentNames, targetPid, showToast),
    ).then(() => {});
  }

  async function doAutoSaveNamesInner(
    sentNames: NameEntry[],
    targetPid?: string | null,
    showToast: boolean | string = true,
  ) {
    const pidToUse = targetPid ?? pid();
    if (!pidToUse) return;
    try {
      await saveNameTable(pidToUse, sentNames);
      // 仅在保存期间无新编辑时才清 dirty：比对发送快照与当前数组引用（setNameEntries 恒创建新数组），
      // 保存在途期间继续输入时保留 dirty，避免新编辑被误判为已落盘
      if (targetPid === undefined && sentNames === nameEntries()) namesDirty = false;
      if (showToast)
        autosaveInfo(typeof showToast === "string" ? showToast : "已自动保存人名表");
    } catch (e) {
      sendLog(`自动保存人名失败: ${e}`, "error");
      if (showToast) autosaveError("自动保存人名表失败", e);
    }
  }

  // 显式保存进行中标志（用于保存按钮禁用与文案）
  const [manualSaving, setManualSaving] = createSignal(false);

  /** 显式保存当前编辑内容（工具栏「保存」按钮）：非人名 tab 保存字典文件，人名 tab 保存人名表 */
  async function handleManualSave(): Promise<void> {
    if (manualSaving()) return;
    if (activeTab() === "names") {
      // 与卸载快照口径一致：无条目时不视为有可保存修改
      if (!namesDirty || nameEntries().length === 0) {
        toast.info("没有需要保存的修改");
        return;
      }
      setManualSaving(true);
      try {
        await doAutoSaveNames(undefined, "已保存人名表");
      } finally {
        setManualSaving(false);
      }
      return;
    }
    if (!selectedFile()) {
      toast.info("请先选择一个字典文件");
      return;
    }
    const snap = captureUnmountSnapshot();
    if (!snap.dirtyDict) {
      toast.info("没有需要保存的修改");
      return;
    }
    setManualSaving(true);
    try {
      const displayName = snap.fileKey ? displayFileName(snap.fileKey) : "字典文件";
      await doAutoSave(undefined, undefined, `已保存 ${displayName}`);
    } finally {
      setManualSaving(false);
    }
  }

  /** 切换 tab 前保存当前编辑：字典文件始终保存；从人名 tab 切出时额外保存人名表 */
  async function saveBeforeSwitch() {
    if (activeTab() === "names") {
      if (namesDirty) await doAutoSaveNames();
    } else {
      await doAutoSave();
    }
  }

  const pid = () => appState.activeProjectId;
  const isProject = () => !!pid();

  // 视图模式：card（卡片）| text（纯文本）
  const [viewMode, setViewMode] = createSignal<"card" | "text">("text");

  // 解析请求序列号：仅接受最新一次解析结果，避免异步竞态覆盖编辑态
  let parseSeq = 0;

  // 解析当前字典文本为结构化行（走后端，本地不再解析）
  async function refreshParsed(): Promise<void> {
    // 人名替换行格式与 pre/gpt/post 不同，后端 parse 接口不支持 names category，
    // 且 names 视图由 nameEntries 渲染、不走 parsedRows，直接跳过
    if (activeTab() === "names") return;
    const seq = ++parseSeq;
    try {
      const rows = await parseDictContent(draftText(), activeTab() as DictTab);
      if (seq !== parseSeq) return;  // 丢弃过期响应，避免覆盖最新编辑/切换结果
      setParsedRows(rows);
    } catch {
      // 解析失败保留上一次结果，避免编辑态崩溃
    }
  }

  // 输入法组合状态：组合中（isComposing）不回写 draftText。
  // 否则每次按键都重序列化整篇文本并重置受控 value，会打断中文 / 日文等 IME。
  const composing = new Map<string, boolean>();

  /** 更新某行的某个字段值。结构化字段（target/condItems/note/search）走专属路径，其余走 values[col]。 */
  function updateRowValue(
    ri: number,
    field:
      | number
      | { kind: "condItem"; index: number }
      | { kind: "condSemantic"; index: number }
      | { kind: "splWord" }
      | { kind: "searchMode" }
      | { kind: "searchWord" }
      | "target"
      | "note",
    value: string,
  ) {
    parseSeq++;  // 本地已编辑，作废飞行中的解析响应，避免其返回后覆盖本次按键
    const rows = parsedRows();
    if (ri < 0 || ri >= rows.length) return;
    const row = rows[ri];
    if (row.type === "blank" || row.type === "comment") return;
    // 安全防护：词/目标/搜索字段过滤 `|`（会破坏行分隔结构；过滤后更新保证 DOM 同步）
    const isWordField =
      field === "target" ||
      (typeof field === "object" &&
        (field.kind === "condItem" || field.kind === "searchWord")) ||
      typeof field === "number";
    if (isWordField && value.includes("|")) {
      value = value.replace(/\|/g, "");
    }
    // 搜索词非空校验：空搜索词会触发引擎 replace("") 的危险行为（搜索词 onInput 负责弹回 DOM）
    if (typeof field === "object" && field.kind === "searchWord" && value.trim() === "") return;
    let next: DictRow = row;
    if (field === "target") {
      next = { ...row, target: value, values: row.values.map((v, i) => (i === 0 ? value : v)) };
    } else if (field === "note") {
      const rest = value ? `//${value}` : "";
      next = { ...row, note: value, values: [...row.values.slice(0, 4), rest] };
    } else if (typeof field === "object" && field.kind === "condItem") {
      const condItems = (row.condItems ?? []).map((c, i) =>
        i === field.index ? { ...c, word: value } : c,
      );
      next = { ...row, condItems };
    } else if (typeof field === "object" && field.kind === "condSemantic") {
      const condItems = (row.condItems ?? []).map((c, i) =>
        i === field.index
          ? applyCondSemantic(c, value as Parameters<typeof applyCondSemantic>[1])
          : c,
      );
      next = { ...row, condItems };
    } else if (typeof field === "object" && field.kind === "splWord") {
      // 切换条件连接符：更新 splWord 并同步各条件项的 op（首个无 op）
      const splWord: "and" | "or" = value === "and" ? "and" : "or";
      const condItems = (row.condItems ?? []).map(
        (c, i): ConditionItem =>
          i === 0 ? { ...c, op: "" } : { ...c, op: splWord },
      );
      next = { ...row, splWord, condItems };
    } else if (
      typeof field === "object" &&
      (field.kind === "searchMode" || field.kind === "searchWord")
    ) {
      // 搜索词：读当前前缀解析，改模式或词后重建 values[2]
      const cur = parseSearchPrefix(row.values[2] ?? "");
      const mode =
        field.kind === "searchMode"
          ? (value as Parameters<typeof serializeSearchPrefix>[0])
          : cur.mode;
      const word = field.kind === "searchWord" ? value : cur.word;
      const vals = [...row.values];
      vals[2] = serializeSearchPrefix(mode, word);
      next = { ...row, values: vals };
    } else {
      const colIndex = field as number;
      const vals = [...row.values];
      vals[colIndex] = value;
      next = { ...row, values: vals };
    }
    const all = [...rows];
    all[ri] = next;
    setParsedRows(all);
    setDraftText(rowsToText(all));
  }

  /** 卡片字段标签 */
  function cardFields() {
    const tab = activeTab();
    const row = parsedRows().find((r) => r.type !== "blank" && r.type !== "comment");
    if (!row) return getFieldLabels("normal", tab as DictTab);
    return getFieldLabels(row.type, tab as DictTab);
  }

  /** 表格单元格渲染：按列定义的编辑器类型分发（含条件行结构化控件，保留原卡片编辑能力） */
  function dictCell(ri: number, col: DictColumnDef, row: DictRow): JSX.Element {
    // 常规/词库/场景等可编辑值列：有行内注释时该列渲染只读注释，否则渲染输入框
    if (col.editor === "noteOrPlain" || col.editor === "plain") {
      const isNoteLike = col.editor === "noteOrPlain";
      if (isNoteLike && row.note) {
        return <span class="dict-cell-note" title="行内注释">{`// ${row.note}`}</span>;
      }
      return (
        <input
          class="dict-cell-input"
          value={row.values[col.valueIndex ?? 0] ?? ""}
          placeholder={col.label}
          onCompositionStart={() => composing.set(`${ri}:${col.key}`, true)}
          onCompositionEnd={(e) => {
            composing.set(`${ri}:${col.key}`, false);
            updateRowValue(ri, col.valueIndex ?? 0, e.currentTarget.value);
          }}
          onInput={(e) => {
            if (e.isComposing) return;
            updateRowValue(ri, col.valueIndex ?? 0, e.currentTarget.value);
          }}
        />
      );
    }
    if (col.editor === "target") {
      return (
        <input
          class="dict-cell-input"
          value={row.target ?? row.values[0] ?? ""}
          placeholder="目标字段"
          onCompositionStart={() => composing.set(`${ri}:target`, true)}
          onCompositionEnd={(e) => {
            composing.set(`${ri}:target`, false);
            updateRowValue(ri, "target", e.currentTarget.value);
          }}
          onInput={(e) => {
            if (e.isComposing) return;
            updateRowValue(ri, "target", e.currentTarget.value);
          }}
        />
      );
    }
    if (col.editor === "condItems") {
      return (
        <div class="dict-cell-conds">
          <For each={row.condItems ?? []}>
            {(c, csi) => (
              <>
                <Show when={csi() > 0}>
                  <select
                    class="dict-cell-select dict-cell-select--connector"
                    value={row.splWord === "and" ? "and" : "or"}
                    onChange={(e) =>
                      updateRowValue(ri, { kind: "splWord" }, e.currentTarget.value)
                    }
                    title="条件连接符"
                  >
                    <option value="and">且</option>
                    <option value="or">或</option>
                  </select>
                </Show>
                <select
                  class="dict-cell-select"
                  value={condSemanticOf(c)}
                  onChange={(e) =>
                    updateRowValue(ri, { kind: "condSemantic", index: csi() }, e.currentTarget.value)
                  }
                >
                  <For each={COND_SEMANTIC_OPTIONS}>
                    {(o) => <option value={o.value}>{o.label}</option>}
                  </For>
                </select>
                <Show
                  when={!c.placeholder}
                  fallback={<span class="dict-cell-fixed">同上</span>}
                >
                  <input
                    class="dict-cell-input dict-cell-input--cond"
                    value={c.word}
                    placeholder="条件词"
                    onCompositionStart={() => composing.set(`${ri}:cond:${csi()}`, true)}
                    onCompositionEnd={(e) => {
                      composing.set(`${ri}:cond:${csi()}`, false);
                      updateRowValue(ri, { kind: "condItem", index: csi() }, e.currentTarget.value);
                    }}
                    onInput={(e) => {
                      if (e.isComposing) return;
                      updateRowValue(ri, { kind: "condItem", index: csi() }, e.currentTarget.value);
                    }}
                  />
                </Show>
              </>
            )}
          </For>
          <Show when={(row.condItems ?? []).length === 0}>
            <span class="dict-cell-fixed">（无条件）</span>
          </Show>
        </div>
      );
    }
    if (col.editor === "search") {
      return (
        <div class="dict-cell-search">
          <select
            class="dict-cell-select"
            value={parseSearchPrefix(row.values[2] ?? "").mode}
            onChange={(e) =>
              updateRowValue(ri, { kind: "searchMode" }, e.currentTarget.value)
            }
          >
            <For each={SEARCH_MODE_OPTIONS}>
              {(o) => <option value={o.value}>{o.label}</option>}
            </For>
          </select>
          <input
            class="dict-cell-input"
            value={parseSearchPrefix(row.values[2] ?? "").word}
            placeholder="搜索词"
            onCompositionStart={() => composing.set(`${ri}:searchWord`, true)}
            onCompositionEnd={(e) => {
              composing.set(`${ri}:searchWord`, false);
              updateRowValue(ri, { kind: "searchWord" }, e.currentTarget.value);
            }}
            onInput={(e) => {
              if (e.isComposing) return;
              const raw = e.currentTarget.value;
              const cleaned = raw.replace(/\|/g, "");
              if (cleaned !== raw) e.currentTarget.value = cleaned;
              if (cleaned.trim() === "") {
                e.currentTarget.value = parseSearchPrefix(row.values[2] ?? "").word;
                return;
              }
              updateRowValue(ri, { kind: "searchWord" }, cleaned);
            }}
          />
        </div>
      );
    }
    if (col.editor === "replace") {
      return (
        <input
          class="dict-cell-input"
          value={row.values[3] ?? ""}
          placeholder="替换词"
          onCompositionStart={() => composing.set(`${ri}:3`, true)}
          onCompositionEnd={(e) => {
            composing.set(`${ri}:3`, false);
            updateRowValue(ri, 3, e.currentTarget.value);
          }}
          onInput={(e) => {
            if (e.isComposing) return;
            updateRowValue(ri, 3, e.currentTarget.value);
          }}
        />
      );
    }
    if (col.editor === "note") {
      return row.note ? (
        <span class="dict-cell-note" title="行内注释">{`// ${row.note}`}</span>
      ) : (
        <span class="dict-cell-empty" />
      );
    }
    // 未知编辑器类型：返回空单元格，避免破坏表格结构
    return <span />;
  }

  function addEntry() {
    const text = draftText().trim();
    const tab = activeTab();
    if (tab === "gpt") {
      setDraftText(text ? text + "\n||" : "||");
    } else {
      setDraftText(text ? text + "\n|" : "|");
    }
    if (viewMode() === "card") {
      refreshParsed();
    }
  }

  // 后端重启窗口的加载失败自动重试（单次固定延时 600ms，避免 RESET/REFUSED 后字典页停留空态需手动刷新）
  const LOAD_RETRY_DELAY_MS = [600];
  // 加载轮次序列号：仅接受最新一轮，切项目/卸载后过期链直接丢弃，避免旧链写回污染新状态
  let loadSeq = 0;
  async function loadDataWithRetry(attempt = 0, seq?: number): Promise<void> {
    if (disposed || (seq !== undefined && seq !== loadSeq)) return;
    try {
      await loadData();
    } catch (e) {
      if (disposed || (seq !== undefined && seq !== loadSeq)) return;
      if (attempt < LOAD_RETRY_DELAY_MS.length) {
        await new Promise((r) => setTimeout(r, LOAD_RETRY_DELAY_MS[attempt]));
        await loadDataWithRetry(attempt + 1, seq);
      } else {
        toast.error(`加载字典失败: ${getErrorMessage(e)}`);
      }
    }
  }

  async function loadData() {
    // 捕获发起轮次：写回前校验 disposed/mySeq，丢弃切项目/卸载后的陈旧加载，避免污染新状态
    const mySeq = loadSeq;
    // 组件已卸载时直接放弃：避免用运行时（可能已切换）的 pid 把旧项目草稿保存到新项目
    if (disposed || mySeq !== loadSeq) return;
    await doAutoSave();
    setLoading(true);
    try {
      if (disposed || mySeq !== loadSeq) return;
      if (!pid()) {
        const res = await fetchCommonDictionaryManager();
        if (disposed || mySeq !== loadSeq) return;
        setData(null);
        setCommonData(res);
        return;
      }

      // 如果切到人名 tab，加载人名数据而非字典文件
      if (activeTab() === "names") {
        await loadNameData();
        return;
      }

      const [projRes, commRes] = await Promise.all([
        fetchProjectDictionaryManager(pid()!, getActiveConfigFileName()),
        fetchCommonDictionaryManager().catch(() => null),
      ]);
      if (disposed || mySeq !== loadSeq) return;
      setData(projRes);
      setCommonData(commRes);
      // 自动选择第一个文件
      const files = getFilesByTab(projRes, activeTab() as DictTab);
      const commFiles = commRes ? getFilesByTab(commRes, activeTab() as DictTab) : [];
      if ((files.length > 0 || commFiles.length > 0) && !selectedFile()) {
        const first = files.length > 0 ? files[0] : commFiles[0];
        const firstKey = `${activeTab()}_dict:${first}`;
        if (disposed || mySeq !== loadSeq) return;
        setSelectedFile(firstKey);
        selectFile(firstKey);
      }
    } finally {
      // 异常上抛给 loadDataWithRetry 重试
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
    } catch (e) {
      toast.error(`加载人名替换失败: ${getErrorMessage(e)}`);
    }
  }

  async function handleGenerateNames() {
    if (!pid()) return;
    setGenerating(true);
    try {
      // 1. 提交生成任务（后端返回异步 job_id）；config.inc.yaml 项目须传真实配置名
      const submitRes = await generateNameTable(pid()!, getActiveConfigFileName());
      const jobId = submitRes.job_id;
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
        // 切项目/卸载后放弃轮询，避免完成后用运行时（可能已切换）的 pid 把结果写到新项目
        if (disposed) return;
        if (Date.now() - start > TIMEOUT_MS) {
          toast.error("人名提取超时");
          return;
        }
        await new Promise((r) => setTimeout(r, POLL_INTERVAL));
        try {
          const s = await fetchJob(jobId);
          if (disposed) return;
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
      if (disposed) return;
      const tableRes = await fetchNameTable(pid()!);
      const names = tableRes.names ?? [];
      setNameEntries(names);
      toast.success(`已提取 ${names.length} 个人名`);
      doAutoSaveNames();
    } catch (e) {
      toast.error(`提取人名失败: ${getErrorMessage(e)}`);
    } finally {
      setGenerating(false);
    }
  }

  function onNameEntryChange(index: number, field: "src_name" | "dst_name", value: string) {
    const next = [...nameEntries()];
    next[index] = { ...next[index], [field]: value };
    setNameEntries(next);
    namesDirty = true; // 仅标记脏，切换 tab/切项目/卸载时统一保存
  }

  let _prevPid: string | null = null;
  let _prevConfigName: string = getActiveConfigFileName();
  createEffect(() => {
    const p = pid();
    const oldPid = _prevPid;
    const oldConfigName = _prevConfigName;
    const pidChanged = p !== oldPid;
    _prevPid = p;
    _prevConfigName = getActiveConfigFileName();
    // 在 untrack 外读取，保留本意依赖的追踪：pid / configName / configNameDetecting 变化才驱动重载
    const detecting = appState.configNameDetecting;
    // 用 untrack 包裹全部副作用：loadData 内部经 doAutoSave 同步读取 data()/commonData()/
    // selectedFile()/draftText()，若被 effect 依赖收集，则 loadData 完成写回 setData/setCommonData
    // 时会反触发本 effect，形成每秒上百次的重载自激循环（commit 93a3296 引入）。untrack 切断此反向依赖。
    untrack(() => {
      if (pidChanged) {
        // 项目切换：先用旧项目身份与旧配置名保存未落盘编辑（fire-and-forget，不阻塞切换），
        // 再清空选中文件与草稿，避免残留旧项目状态污染新项目加载。
        // 组件已卸载（onCleanup 的 runPageAutosave 已按挂载快照保存）时跳过，避免重复请求/toast
        if (!disposed) {
          doAutoSave(oldPid, oldConfigName);
          if (namesDirty) doAutoSaveNames(oldPid, true);
        }
        setSelectedFile(null);
        setDraftText("");
      }
      // 无项目：直接加载公共字典
      if (!p) {
        loadDataWithRetry(0, ++loadSeq);
        return;
      }
      // 有项目：等真实配置名探测完成再加载，避免用回退名 config.yaml 请求 404
      if (!detecting) loadDataWithRetry(0, ++loadSeq);
    });
  });

  function selectFile(fileKey: string) {
    setSelectedFile(fileKey);
    // dict_contents 的 key 不带 "{tab}_dict:" 前缀，查表前剥离
    const lookupKey = stripTabPrefix(fileKey);
    const content = data()?.dict_contents?.[lookupKey]
      ?? commonData()?.dict_contents?.[lookupKey];
    const text = content ? content.lines.join("\n") : "";
    setDraftText(text);
    refreshParsed();
    // 切换文件后让 textarea 重获焦点，光标置顶
    if (_taRef) {
      _taRef.focus();
      _taRef.selectionStart = _taRef.selectionEnd = 0;
    }
  }

  // 切换 tab 时更新选中文件
  let _prevTab: string = activeTab();
  createEffect(() => {
    const tab = activeTab();
    const tabChanged = tab !== _prevTab;
    _prevTab = tab;

    if (tab === "names") {
      setSelectedFile(null);
      setDraftText("");
      loadNameData();
      return;
    }
    if (!data() && !commonData()) return;
    // 仅当切 Tab 或当前无选中文件时才自动选择第一个
    if (!tabChanged && selectedFile()) return;
    const projFiles = getFilesByTab(data(), tab as DictTab);
    const commFiles = getFilesByTab(commonData(), tab as DictTab);
    if (projFiles.length > 0) {
      const key = `${tab}_dict:${projFiles[0]}`;
      selectFile(key);
    } else if (commFiles.length > 0) {
      const key = `${tab}_dict:${commFiles[0]}`;
      selectFile(key);
    } else {
      setSelectedFile(null);
      setDraftText("");
    }
  });

  // 后处理(post)字典暂不支持表格视图：强制文本模式，规避混类型行在表格中的列错位。
  // 临时规避开关，后续若实现每行自适应列数可移除（对照问题1）。
  createEffect(() => {
    if (activeTab() === "post" && viewMode() === "card") {
      setViewMode("text");
    }
  });

  // 切到卡片模式时基于当前文本重新解析（text 模式编辑后切换需刷新）
  createEffect(() => {
    if (viewMode() === "card") {
      refreshParsed();
    }
  });

  let _taRef: HTMLTextAreaElement | undefined;

  const [createSource, setCreateSource] = createSignal<"project" | "common">("project");

  /**
   * 由 tab + 文件名后缀推导实际后端 category。
   * - forbidden 合成 tab：_h → forbiddenh，否则 forbiddennh；
   * - gpt tab：显式 _h 后缀 → gpth（h 场景 GPT），否则 gptnh（非 h / 无后缀）；
   * - 其余 tab 原样透传。
   */
  function resolveCreateCategory(tab: string, filename: string): DictionaryCategory {
    // h/非h 判定统一走 dictFileScene（includes("_h") && !includes("_非h")），与展示分组口径一致
    if (tab === "forbidden") return dictFileScene(filename) === "h" ? "forbiddenh" : "forbiddennh";
    if (tab === "gpt") return dictFileScene(filename) === "h" ? "gpth" : "gptnh";
    return tab as DictionaryCategory;
  }

  async function handleCreate() {
    await doAutoSave();
    const name = newFilename().trim();
    if (!name) return;
    setCreating(true);
    try {
      if (pid() && createSource() === "project") {
        const res = await createProjectDictionaryFile(pid()!, {
          config_file_name: getActiveConfigFileName(),
          category: resolveCreateCategory(activeTab(), name),
          filename: name,
        });
        setNewFilename("");
        toast.success("项目字典文件已创建");
        await loadDataWithRetry(0, ++loadSeq);
        // 后端返回的 file_key 不带 "{tab}_dict:" 前缀，补全以匹配列表选中高亮
        selectFile(`${activeTab()}_dict:${res.file_key}`);
      } else {
        const res = await createCommonDictionaryFile({
          category: resolveCreateCategory(activeTab(), name),
          filename: name,
        });
        setNewFilename("");
        toast.success("公共字典文件已创建");
        await loadDataWithRetry(0, ++loadSeq);
        const key = `${activeTab()}_dict:${res.filename}`;
        selectFile(key);
      }
    } catch (e) {
      toast.error(`创建失败: ${getErrorMessage(e)}`);
    } finally {
      setCreating(false);
    }
  }

  function displayFileName(fileKey: string): string {
    // fileKey 格式: "gpt_dict:(project_dir)文件名.txt" 或 "gpt_dict:文件名.txt"
    return stripProjectDirMarker(stripTabPrefix(fileKey));
  }

  async function handleDelete(fileKey: string) {
    await doAutoSave();
    const result = await confirm.show({
      title: "删除字典文件",
      message: `确定要删除「${displayFileName(fileKey)}」吗？`,
      tone: "danger",
    });
    if (!result.confirmed) return;
    try {
      const bareKey = stripTabPrefix(fileKey);
      const isProjectFile = bareKey.includes(PROJECT_DIR_MARKER);
      if (pid() && isProjectFile) {
        await deleteProjectDictionaryFile(pid()!, {
          config_file_name: getActiveConfigFileName(),
          file_key: bareKey,
          delete_file: true,
        });
      } else {
        const fileName = bareKey;
        await deleteCommonDictionaryFile({ filename: fileName });
      }
      toast.success("文件已删除");
      setSelectedFile(null);
      setDraftText("");
      await loadDataWithRetry(0, ++loadSeq);
    } catch (e) {
      toast.error(`删除失败: ${getErrorMessage(e)}`);
    }
  }

  // Tab 文件数：放在 memo 中让 data/commonData 变化时自动重算（For 的 keyed 语义不会因内部 signal 变化重跑 mapper）
  const tabCounts = createMemo<Record<string, number>>(() => {
    const counts: Record<string, number> = {};
    for (const t of TABS) {
      counts[t.key] =
        t.key === "names"
          ? nameEntries().length
          : getFilesByTab(data(), t.key as DictTab).length
              + getFilesByTab(commonData(), t.key as DictTab).length;
    }
    return counts;
  });

  type FileEntry = { name: string; source: "project" | "common" };
  const activeFiles = (): FileEntry[] => {
    const tab = activeTab() as DictTab;
    const proj = getFilesByTab(data(), tab);
    const comm = getFilesByTab(commonData(), tab);
    return [
      ...proj.map((f) => ({ name: f, source: "project" as const })),
      ...comm.map((f) => ({ name: f, source: "common" as const })),
    ];
  };

  /** GPT 字典按 h/非h 场景分组（供文件列表分组展示）；仅 gpt tab 使用 */
  const gptSceneGroups = (): { label: string; files: FileEntry[] }[] => {
    const all = activeFiles();
    const hFiles = all.filter((e) => dictFileScene(e.name) === "h");
    const nhFiles = all.filter((e) => dictFileScene(e.name) === "nh");
    return [
      { label: "h 场景", files: hFiles },
      { label: "非 h 场景", files: nhFiles },
    ];
  };

  /** 按 fileKey（`{tab}_dict:{name}`）取文件条目数 */
  const fileCountOf = (key: string): number => {
    const lookupKey = stripTabPrefix(key);
    return (
      data()?.dict_contents?.[lookupKey]?.count ??
      commonData()?.dict_contents?.[lookupKey]?.count ??
      0
    );
  };



  return (
    <div class="page page-dict">
      <h2 class="page-title">字典管理</h2>
      <p class="page-description">{isProject() ? "项目字典" : "公共字典"} — 管理翻译用词对照表</p>
      <p class="dict-scene-hint">
        GPT 字典文件名以 <code>_h</code> 结尾（如 <code>GPT字典_h.txt</code>）表示 h 场景词典，以 <code>_非h</code> 结尾（如 <code>GPT字典_非h.txt</code>）表示非 h 场景词典；未带后缀的视为非 h 场景。
      </p>

      {/* ── Tab 栏 ── */}
      <div class="dict-tabs">
        <For each={TABS}>
          {(t) => (
            <button
              class={`dict-tab ${activeTab() === t.key ? "active" : ""}`}
              onClick={async () => { await saveBeforeSwitch(); setActiveTab(t.key); }}
            >
              <span class="dict-tab-label">{t.label}</span>
              <span class="dict-tab-count">{tabCounts()[t.key] ?? 0}</span>
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
                  <button class="btn btn--sm" onClick={handleGenerateNames} disabled={generating()}>
                    {generating() ? "提取中…" : "提取人名"}
                  </button>
                  <button
                    class="btn btn--sm"
                    title="保存人名表修改"
                    onClick={() => void handleManualSave()}
                    disabled={manualSaving()}
                  >
                    {manualSaving() ? "保存中…" : "保存"}
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
                        />
                        <span class="name-entry-arrow">→</span>
                        <input
                          class="name-entry-dst name-input"
                          value={entrySignal().dst_name}
                          onInput={(e) => onNameEntryChange(i, "dst_name", e.currentTarget.value)}
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
            <div class="dict-file-header">文件 ({activeFiles().length})</div>
            <div class="dict-file-body">
              <Show when={activeTab() === "gpt" && activeFiles().length > 0} fallback={
                <>
                  <For each={activeFiles()}>
                    {(entry) => (
                      <div
                        class={`dict-file-item ${selectedFile() === `${activeTab()}_dict:${entry.name}` ? "selected" : ""}`}
                        onClick={async () => { await doAutoSave(); selectFile(`${activeTab()}_dict:${entry.name}`); }}
                      >
                        <div class="dict-file-name">
                          <span class="dict-file-badge">{entry.source === "project" ? "项目" : "公共"}</span>
                          <span class="dict-file-name-text">{stripProjectDirMarker(entry.name)}</span>
                        </div>
                        <span class="dict-file-count">{fileCountOf(`${activeTab()}_dict:${entry.name}`)} 条</span>
                        <button class="dict-file-del" onClick={(e) => { e.stopPropagation(); handleDelete(`${activeTab()}_dict:${entry.name}`); }} title="删除此字典文件">
                          删除
                        </button>
                      </div>
                    )}
                  </For>
                  {activeFiles().length === 0 && <p class="dict-empty">暂无字典文件</p>}
                </>
              }>
                {/* GPT 字典按 h/非h 场景分组展示 */}
                <For each={gptSceneGroups()}>
                  {(group) => (
                    <>
                      <div class="dict-file-group-label">{group.label}（{group.files.length}）</div>
                      <For each={group.files}>
                        {(entry) => (
                          <div
                            class={`dict-file-item ${selectedFile() === `gpt_dict:${entry.name}` ? "selected" : ""}`}
                            onClick={async () => { await doAutoSave(); selectFile(`gpt_dict:${entry.name}`); }}
                          >
                            <div class="dict-file-name">
                              <span class="dict-file-badge">{entry.source === "project" ? "项目" : "公共"}</span>
                              <span class="dict-file-name-text">{stripProjectDirMarker(entry.name)}</span>
                            </div>
                            <span class="dict-file-count">{fileCountOf(`gpt_dict:${entry.name}`)} 条</span>
                            <button class="dict-file-del" onClick={(e) => { e.stopPropagation(); handleDelete(`gpt_dict:${entry.name}`); }} title="删除此字典文件">
                              删除
                            </button>
                          </div>
                        )}
                      </For>
                    </>
                  )}
                </For>
              </Show>
            </div>

            {/* ── 新建文件 ── */}
            <div class="dict-create">
              <Show when={pid()}>
                <select
                  class="find-select"
                  value={createSource()}
                  onChange={(e) => setCreateSource(e.currentTarget.value as "project" | "common")}
                >
                  <option value="project">项目</option>
                  <option value="common">公共</option>
                </select>
              </Show>
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
              fallback={<div class="dict-editor-empty">请选择一个字典文件</div>}
            >
              <div class="dict-editor-header">
                <div class="dict-editor-title">
                  <span class="dict-editor-filename">{displayFileName(selectedFile()!)}</span>
                  <span class="dict-editor-stats">
                    {parsedRows().filter((r) => r.type !== "blank").length} 条有效条目
                  </span>
                </div>
                <div class="dict-editor-actions">
                  <button
                    class="btn btn--sm"
                    title="保存当前字典文件的修改"
                    onClick={() => void handleManualSave()}
                    disabled={manualSaving()}
                  >
                    {manualSaving() ? "保存中…" : "保存"}
                  </button>
                  <div class="dict-view-seg">
                    <button
                      class={`dict-view-btn ${viewMode() === "text" ? "active" : ""}`}
                      onClick={() => setViewMode("text")}
                      title="纯文本模式"
                    >
                      文本
                    </button>
                    <Show when={activeTab() !== "post"}>
                      <button
                        class={`dict-view-btn ${viewMode() === "card" ? "active" : ""}`}
                        onClick={() => setViewMode("card")}
                        title="卡片模式"
                      >
                        卡片
                      </button>
                    </Show>
                  </div>
                </div>
              </div>

              <Show
                when={viewMode() === "text"}
                fallback={
                  /* ── 表格模式 ── */
                  <div class="dict-table-wrap">
                    <Show
                      when={parsedRows().filter((r) => r.type !== "blank").length > 0}
                      fallback={<div class="dict-editor-empty">暂无条目，点击下方按钮添加</div>}
                    >
                      <table class="dict-table">
                        <thead>
                          <tr>
                            {cardFields().map((label, i) => (
                              <th key={i}>{label}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          <Index each={parsedRows()}>
                            {(rowSignal, ri) => (
                              <Show when={rowSignal().type !== "blank"}>
                                <Show
                                  when={rowSignal().type === "comment"}
                                  fallback={
                                    <tr class={`dict-row dict-row--${rowSignal().type}`}>
                                      <Index each={getTableColumns(rowSignal().type, activeTab() as DictTab)}>
                                        {(colSignal) => (
                                          <td>
                                            {dictCell(ri, colSignal(), rowSignal())}
                                          </td>
                                        )}
                                      </Index>
                                    </tr>
                                  }
                                >
                                  <tr class="dict-row dict-row--comment">
                                    <td colspan={cardFields().length} class="dict-cell-comment">
                                      {rowSignal().values[0]}
                                    </td>
                                  </tr>
                                </Show>
                              </Show>
                            )}
                          </Index>
                        </tbody>
                      </table>
                    </Show>
                    <button class="btn btn--sm dict-table-add" onClick={addEntry}>
                      + 添加条目
                    </button>
                  </div>
                }
              >
                <textarea
                  class="dict-textarea"
                  ref={(el) => (_taRef = el)}
                  value={draftText()}
                  onInput={(e) => onDictChange(e.currentTarget.value)}
                  onKeyDown={(e) => {
                    // isComposing：输入法组合中按 Enter 是确认候选词，不触发换行插入
                    if (e.key !== "Enter" || e.isComposing) return;
                    e.preventDefault();
                    const ta = e.currentTarget as HTMLTextAreaElement;
                    const pos = ta.selectionStart;
                    const before = ta.value.slice(0, pos);
                    const after = ta.value.slice(ta.selectionEnd);
                    const newVal = before + "\n" + after;
                    setDraftText(newVal);
                    requestAnimationFrame(() => {
                      ta.selectionStart = ta.selectionEnd = pos + 1;
                    });
                  }}
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
