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
];

interface StepPipelineSettingsProps {
  gameInfo: string;
  stageEnabled: Record<string, boolean>;
  /** 勾选了「生成示例文件」的阶段键集合 */
  sampleStages: Set<string>;
  onGameInfoChange: (v: string) => void;
  onStageToggle: (key: string, enabled: boolean) => void;
  onSampleToggle: (key: string, checked: boolean) => void;
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
            GlobalPrompt.json / {"文件名.meta.json"} / {"文件名.batch.json"}，
            并自动禁用该阶段（空模板需跳过生成）。填写完示例内容后，请到「后端设置」中重新开启对应阶段。
          </span>
        </div>
      </div>
    </div>
  );
}
