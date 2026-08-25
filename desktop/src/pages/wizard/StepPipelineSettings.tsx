import { AfterTranslationOrderEditor } from "../../components/AfterTranslationOrderEditor";
import type { AfterTranslationEntry } from "../../lib/afterTranslation";

interface PipelineStageItem {
  key: string;
  label: string;
  hint: string;
  /** 是否支持生成示例文件（仅 JSON 类阶段支持） */
  sampleable?: boolean;
}

const PIPELINE_STAGES: PipelineStageItem[] = [
  {
    key: "enableValidate",
    label: "阶段 0：输入数据校验",
    hint: "校验输入文件的 message 与 name 完整性。关闭后跳过校验直接进入下一阶段（不建议关闭）。",
  },
  {
    key: "enableCompress",
    label: "阶段 1：文本无损压缩",
    hint: "压缩全文供全局分析使用。关闭后阶段 2（全局分析）因无压缩文本将自动跳过。",
  },
  {
    key: "enableGlobalPrompt",
    label: "阶段 2：全局游戏分析",
    hint: "生成游戏名称/剧情概述/角色列表等全局档案（GlobalPrompt.json）。",
    sampleable: true,
  },
  {
    key: "enableGenDic",
    label: "阶段 3：术语表构建",
    hint: "提取项目术语生成 GPT 字典。该阶段未优化，运行效果较差，不建议启用。",
  },
  {
    key: "enableFileMeta",
    label: "阶段 4：文件级元数据",
    hint: "为每个文件生成剧情背景（FileMetaData）。",
    sampleable: true,
  },
  {
    key: "enablePlotRoute",
    label: "阶段 4.5：剧情路线图",
    hint: "基于各文件的剧情摘要生成剧情路线图（PlotRouteMap.json），并标记每个文件所属路线。",
    sampleable: true,
  },
  {
    key: "enableBatchMeta",
    label: "阶段 5：批次级元数据",
    hint: "按剧情分段划分翻译区间。",
    sampleable: true,
  },
  {
    key: "enableTranslate",
    label: "阶段 6：翻译执行",
    hint: "调用 AI 翻译。关闭后流水线只执行前置分析阶段，不进行翻译。",
  },
  {
    key: "enableImprove",
    label: "阶段 7：修复和改进译文",
    hint: "翻译完成后逐文件执行下方「修复和改进译文」中选中的后端（按数字顺序）。关闭后整个阶段跳过。",
  },
];

// 剧情路线图的结构类型（含专业术语通俗说明）
const PLOT_STRUCTURE_TYPES = [
  {
    value: "线性",
    label: "线性（链）",
    desc: "剧情一条线走到底，无分支、无汇合，如单结局线性文字小说。",
  },
  {
    value: "树",
    label: "树（树状分支）",
    desc: "从共同起点不断分出多条路线，各路线有独立结局，分支只分不合。",
  },
  {
    value: "有向无环图",
    label: "有向无环图（DAG）",
    desc: "多条剧情线可以汇合到共同节点，也可中途交叉，但剧情不会回到过去（无循环）。如多条路线最终汇合到同一结局。",
  },
  {
    value: "有向有环图",
    label: "有向有环图（含循环）",
    desc: "允许剧情循环/回溯，如二周目、时间回溯、重复刷事件。适合有轮回/多周目设定的作品。",
  },
  {
    value: "混合",
    label: "混合结构",
    desc: "以上多种结构混合出现，如主线线性推进 + 支线分支 + 结局汇合 + 二周目循环。不确定时选此项。",
  },
] as const;

interface StepPipelineSettingsProps {
  gameInfo: string;
  stageEnabled: Record<string, boolean>;
  /** 勾选了「生成示例文件」的阶段键集合 */
  sampleStages: Set<string>;
  /** 剧情路线图：结构类型与用户大纲（纯文本） */
  plotStructureType: string;
  plotOutline: string;
  /** 修复和改进译文（阶段 7）后处理顺序：有序后端条目数组（字符串 key 或 fix 对象条目） */
  afterTranslationOrder: AfterTranslationEntry[];
  onGameInfoChange: (v: string) => void;
  onStageToggle: (key: string, enabled: boolean) => void;
  onSampleToggle: (key: string, checked: boolean) => void;
  onPlotStructureTypeChange: (v: string) => void;
  onPlotOutlineChange: (v: string) => void;
  onAfterTranslationOrderChange: (order: AfterTranslationEntry[]) => void;
}

/**
 * 受控 textarea 的 Enter 换行处理：preventDefault 后手动在光标处插入换行，
 * 避免受控 value 覆盖浏览器默认换行（方案与项目设置页一致）。
 */
function handleTextareaEnter(e: KeyboardEvent, onChange: (v: string) => void) {
  if (e.key !== "Enter" || e.isComposing) return;
  e.preventDefault();
  const ta = e.currentTarget as HTMLTextAreaElement;
  const pos = ta.selectionStart;
  const newVal = ta.value.slice(0, pos) + "\n" + ta.value.slice(ta.selectionEnd);
  onChange(newVal);
  requestAnimationFrame(() => {
    ta.selectionStart = ta.selectionEnd = pos + 1;
  });
}

export function StepPipelineSettings(props: StepPipelineSettingsProps) {
  return (
    <div class="wizard-panel">
      <h3 class="wizard-panel-title">流水线与全局设置</h3>
      <p class="wizard-panel-desc">
        设置全局游戏分析的外部信息，并可选择跳过流水线中的某些阶段，或为该阶段生成示例文件（正式命名，填写后直接生效）。
      </p>
      <div class="wizard-settings-grid">
        <div class="field wizard-settings-grid__full">
          <span class="field__label">外部信息（游戏信息）</span>
          <div class="pc-external-info">
            <textarea
              class="pc-external-info__textarea"
              placeholder={"可选。提供给全局游戏分析（阶段 2）的外部剧情信息，如游戏简介、世界观说明等。\n留空则由 AI 根据游戏文本自行推断。"}
              value={props.gameInfo}
              onInput={(e) => props.onGameInfoChange(e.currentTarget.value)}
              onKeyDown={(e) => handleTextareaEnter(e, props.onGameInfoChange)}
            />
          </div>
          <span class="field__hint">
            写入 config.yaml 的 externals.gameInfo，供 ForGlobalPrompt 全局分析使用。
          </span>
        </div>
        <div class="field wizard-settings-grid__full">
          <span class="field__label">流水线阶段</span>
          <div class="pipeline-stage-list">
            {PIPELINE_STAGES.map((s) => (
              <div class="pipeline-stage-item">
                <label class="pipeline-stage-item__row">
                  <input
                    type="checkbox"
                    checked={props.stageEnabled[s.key] ?? true}
                    onChange={(e) => props.onStageToggle(s.key, e.currentTarget.checked)}
                  />
                  <div class="pipeline-stage-item__body">
                    <span class="pipeline-stage-item__label">{s.label}</span>
                    <span class="pipeline-stage-item__hint">{s.hint}</span>
                  </div>
                </label>
                {s.sampleable && (
                  <label class="pipeline-stage-item__sample">
                    <input
                      type="checkbox"
                      checked={props.sampleStages.has(s.key)}
                      onChange={(e) => props.onSampleToggle(s.key, e.currentTarget.checked)}
                    />
                    <span>生成示例文件</span>
                  </label>
                )}
              </div>
            ))}
          </div>
          <span class="field__hint">
            阶段开关控制该阶段是否在流水线中执行。「生成示例文件」独立于开关：勾选后会在对应缓存目录生成
            GlobalPrompt.json / PlotRouteMap.json / {"文件名.meta.json"} / {"文件名.batch.json"}，
            并自动禁用该阶段（空模板需跳过生成）。填写完示例内容后，请到「后端设置」中重新开启对应阶段。
          </span>
        </div>
        <div class="field wizard-settings-grid__full">
          <span class="field__label">修复和改进译文（阶段 7 后处理顺序）</span>
          <AfterTranslationOrderEditor
            value={props.afterTranslationOrder}
            onChange={props.onAfterTranslationOrderChange}
          />
          <span class="field__hint">
            在数字框中填入数字表示该后端在阶段 7 中的执行顺序（数字几就第几步执行）；留空则不执行。
            点击数字框自动分配当前最小可用序号，清空后其余后端自动紧凑重排。顺序写入
            config.yaml 的 common.gpt.afterTranslation（有序数组）；关闭「阶段 7」开关后此处不生效。
          </span>
        </div>
        <div class="field wizard-settings-grid__full">
          <span class="field__label">剧情路线图设置</span>
          <div class="plotroute-config">
            <div class="plotroute-config__row">
              <span class="field__label plotroute-config__sub">剧情结构类型</span>
              <select
                class="plotroute-config__select"
                value={props.plotStructureType}
                onChange={(e) => props.onPlotStructureTypeChange(e.currentTarget.value)}
              >
                {PLOT_STRUCTURE_TYPES.map((t) => (
                  <option value={t.value}>{t.label}</option>
                ))}
              </select>
              <span class="field__hint">
                当前选择：{PLOT_STRUCTURE_TYPES.find((t) => t.value === props.plotStructureType)?.desc}
              </span>
            </div>
            <div class="plotroute-config__row">
              <span class="field__label plotroute-config__sub">剧情大纲（纯文本，可选）</span>
              <textarea
                class="plotroute-config__textarea"
                placeholder={"大致剧情结构描述，如：\n序章 → 三女主角线（华恋/凛音/学生会）→ 各线汇合 TRUE END\n可写路线名、关键转折、结局走向。留空则由 AI 根据各文件剧情摘要自行归纳。"}
                value={props.plotOutline}
                onInput={(e) => props.onPlotOutlineChange(e.currentTarget.value)}
                onKeyDown={(e) => handleTextareaEnter(e, props.onPlotOutlineChange)}
              />
              <span class="field__hint">
                作为「阶段 4.5 剧情路线图」生成的强先验，供 AI 把每个文件填充到对应路线；写入 config.yaml 的 internals.plotroute.userOutline。
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
