import type { PluginInfo } from '../../lib/api';
import { Button } from '../../components/Button';
import { CustomSelect } from '../../components/CustomSelect';
import { Panel } from '../../components/Panel';

type StepSettingsProps = {
  filePlugins: PluginInfo[];
  selectedFilePlugin: string;
  workersPerProject: number;
  numPerRequest: number;
  dynamicNumPerRequest: boolean;
  dynamicNumPerRequestMin: number;
  dynamicNumPerRequestMax: number;
  language: string;
  translationGuideline: string;
  guidelines: string[];
  settingsSaved: boolean;
  onFilePluginChange: (v: string) => void;
  onWorkersChange: (v: number) => void;
  onNumPerRequestChange: (v: number) => void;
  onDynamicNumChange: (v: boolean) => void;
  onDynamicMinChange: (v: number) => void;
  onDynamicMaxChange: (v: number) => void;
  onLanguageChange: (v: string) => void;
  onGuidelineChange: (v: string) => void;
  onSaveSettings: () => void;
};

export function StepSettings({
  filePlugins, selectedFilePlugin, workersPerProject, numPerRequest,
  dynamicNumPerRequest, dynamicNumPerRequestMin, dynamicNumPerRequestMax,
  language, translationGuideline, guidelines, settingsSaved,
  onFilePluginChange, onWorkersChange, onNumPerRequestChange,
  onDynamicNumChange, onDynamicMinChange, onDynamicMaxChange,
  onLanguageChange, onGuidelineChange, onSaveSettings,
}: StepSettingsProps) {
  return (
    <Panel title="常用设置" description="设置项目的基本翻译参数。">
      <div className="wizard-settings-grid">
        <div className="field wizard-settings-grid__full">
          <span className="field__label">文件插件</span>
          <CustomSelect value={selectedFilePlugin} onChange={(e) => onFilePluginChange(e.target.value)}>
            {filePlugins.length > 0
              ? filePlugins.map((p) => (<option key={p.name} value={p.name}>{p.display_name} ({p.name})</option>))
              : <option value={selectedFilePlugin}>{selectedFilePlugin}</option>}
          </CustomSelect>
          <span className="field__hint">用于识别与解析源文件格式。</span>
        </div>
        <div className="field">
          <span className="field__label">并发文件数</span>
          <input className="field__input" type="number" min={1} value={workersPerProject} onChange={(e) => onWorkersChange(Number(e.target.value))} />
        </div>
        <div className="field">
          <span className="field__label">单次翻译句数</span>
          <input className="field__input" type="number" min={1} value={numPerRequest} onChange={(e) => onNumPerRequestChange(Number(e.target.value))} />
        </div>
        <div className="field">
          <span className="field__label">动态句数调整</span>
          <CustomSelect value={String(dynamicNumPerRequest)} onChange={(e) => onDynamicNumChange(e.target.value === 'true')}>
            <option value="false">关闭</option>
            <option value="true">开启</option>
          </CustomSelect>
        </div>
        <div className="field">
          <span className="field__label">动态最小句数</span>
          <input className="field__input" type="number" min={1} value={dynamicNumPerRequestMin} onChange={(e) => onDynamicMinChange(Number(e.target.value))} />
        </div>
        <div className="field">
          <span className="field__label">动态最大句数</span>
          <input className="field__input" type="number" min={1} value={dynamicNumPerRequestMax} onChange={(e) => onDynamicMaxChange(Number(e.target.value))} />
        </div>
        <div className="field wizard-settings-grid__full">
          <span className="field__label">目标语言</span>
          <CustomSelect value={language} onChange={(e) => onLanguageChange(e.target.value)}>
            <option value="zh-cn">简体中文</option>
            <option value="zh-tw">繁体中文</option>
            <option value="en">English</option>
            <option value="ja">日本語</option>
            <option value="ko">한국어</option>
          </CustomSelect>
        </div>
        <div className="field wizard-settings-grid__full">
          <span className="field__label">翻译规范</span>
          <CustomSelect value={translationGuideline} onChange={(e) => onGuidelineChange(e.target.value)}>
            {guidelines.length === 0 && translationGuideline === '' ? <option value="">（未找到翻译规范文件）</option> : null}
            {translationGuideline && !guidelines.includes(translationGuideline) ? <option value={translationGuideline}>{translationGuideline}</option> : null}
            {guidelines.map((g) => (<option key={g} value={g}>{g}</option>))}
          </CustomSelect>
        </div>
      </div>
      <div className="wizard-actions">
        <Button disabled={settingsSaved} onClick={onSaveSettings}>{settingsSaved ? '已保存 ✓' : '保存设置'}</Button>
      </div>
    </Panel>
  );
}
