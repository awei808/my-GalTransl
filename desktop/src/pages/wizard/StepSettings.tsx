import type { PluginInfo } from "../../lib/api/types";

interface StepSettingsProps {
  selectedFilePlugin: string;
  selectedTextPlugin: string;
  workersPerProject: number;
  numPerRequest: number;
  language: string;
  translationGuideline: string;
  guidelines: string[];
  filePlugins: PluginInfo[];
  textPlugins: PluginInfo[];
  onFilePluginChange: (v: string) => void;
  onTextPluginChange: (v: string) => void;
  onWorkersChange: (v: number) => void;
  onNumPerRequestChange: (v: number) => void;
  onLanguageChange: (v: string) => void;
  onGuidelineChange: (v: string) => void;
  onSaveSettings: () => void;
}

export function StepSettings(props: StepSettingsProps) {
  return (
    <div class="wizard-panel">
      <h3 class="wizard-panel-title">常用设置</h3>
      <p class="wizard-panel-desc">设置项目的基本翻译参数。</p>
      <div class="wizard-settings-grid">
        <div class="field wizard-settings-grid__full">
          <span class="field__label">文件插件</span>
          <select
            class="field__input"
            value={props.selectedFilePlugin}
            onChange={(e) => props.onFilePluginChange(e.currentTarget.value)}
          >
            {props.filePlugins.length > 0
              ? props.filePlugins.map((p) => (
                  <option value={p.name}>
                    {p.display_name} ({p.name})
                  </option>
                ))
              : <option value={props.selectedFilePlugin}>{props.selectedFilePlugin}</option>
            }
          </select>
          <span class="field__hint">用于识别与解析源文件格式。</span>
        </div>
        <div class="field wizard-settings-grid__full">
          <span class="field__label">文本插件</span>
          <div class="text-plugins-selector">
            {props.textPlugins.length > 0
              ? props.textPlugins.map((p) => (
                  <label class="text-plugin-chip">
                    <input
                      type="radio"
                      name="text-plugin"
                      checked={props.selectedTextPlugin === p.name}
                      onChange={() => props.onTextPluginChange(p.name)}
                    />
                    <span>{p.display_name} ({p.name})</span>
                  </label>
                ))
              : <span class="field__hint">未加载到文本插件列表</span>
            }
          </div>
          <span class="field__hint">按顺序执行的文本处理插件，可多选。</span>
        </div>
        <div class="field">
          <span class="field__label">并发文件数</span>
          <input
            class="field__input"
            type="number"
            min={1}
            value={props.workersPerProject}
            onInput={(e) => props.onWorkersChange(Number(e.currentTarget.value))}
          />
        </div>
        <div class="field">
          <span class="field__label">单次翻译句数</span>
          <input
            class="field__input"
            type="number"
            min={1}
            value={props.numPerRequest}
            onInput={(e) => props.onNumPerRequestChange(Number(e.currentTarget.value))}
          />
        </div>
        <div class="field wizard-settings-grid__full">
          <span class="field__label">目标语言</span>
          <select
            class="field__input"
            value={props.language}
            onChange={(e) => props.onLanguageChange(e.currentTarget.value)}
          >
            <option value="zh-cn">简体中文</option>
            <option value="zh-tw">繁体中文</option>
            <option value="en">English</option>
            <option value="ja">日本語</option>
            <option value="ko">한국어</option>
          </select>
        </div>
        <div class="field wizard-settings-grid__full">
          <span class="field__label">翻译规范</span>
          <select
            class="field__input"
            value={props.translationGuideline}
            onChange={(e) => props.onGuidelineChange(e.currentTarget.value)}
          >
            {props.guidelines.length === 0 && props.translationGuideline === ""
              ? <option value="">（未找到翻译规范文件）</option>
              : null}
            {props.translationGuideline && !props.guidelines.includes(props.translationGuideline)
              ? <option value={props.translationGuideline}>{props.translationGuideline}</option>
              : null}
            {props.guidelines.map((g) => (
              <option value={g}>{g}</option>
            ))}
          </select>
        </div>
      </div>
    </div>
  );
}
