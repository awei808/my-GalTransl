import { Panel } from '../../components/Panel';

type StepExtractNamesProps = {
  nameJobStatus: 'idle' | 'running' | 'completed' | 'failed';
  nameJobMessage: string;
};

export function StepExtractNames({ nameJobStatus, nameJobMessage }: StepExtractNamesProps) {
  return (
    <Panel title="提取人名" description="自动从项目文件中提取人名表。">
      {nameJobStatus === 'running' && (
        <div className="wizard-progress">
          <div className="wizard-progress__bar"><div className="wizard-progress__fill" /></div>
          <div className="wizard-progress__text">正在提取人名...</div>
        </div>
      )}
      {nameJobStatus === 'completed' && (
        <div className="wizard-message wizard-message--success">
          {nameJobMessage}
          <br />
          <span className="wizard-message__hint">可在项目的「人名翻译」菜单中使用 AI 翻译人名。</span>
        </div>
      )}
      {nameJobStatus === 'failed' && (
        <div className="wizard-message wizard-message--error">提取失败: {nameJobMessage}</div>
      )}
    </Panel>
  );
}
