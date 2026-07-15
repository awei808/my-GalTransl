import { Button } from '../../components/Button';
import { Panel } from '../../components/Panel';
import { Icon } from '../../components/icons';

type StepProjectInfoProps = {
  parentDir: string;
  projectName: string;
  projectDir: string;
  projectCreated: boolean;
  onSelectParentDir: () => void;
  onParentDirChange: (value: string) => void;
  onProjectNameChange: (value: string) => void;
  onProjectCreatedChange: (created: boolean) => void;
  onCreateProject: () => void;
};

export function StepProjectInfo({
  parentDir, projectName, projectDir, projectCreated,
  onSelectParentDir, onParentDirChange, onProjectNameChange,
  onProjectCreatedChange, onCreateProject,
}: StepProjectInfoProps) {
  return (
    <Panel title="项目位置" description="选择项目文件夹的保存位置和项目名称，然后创建项目结构。">
      <div className="wizard-form-grid">
        <div className="field">
          <span className="field__label">父目录</span>
          <div className="field__row">
            <input
              className="field__input"
              autoComplete="off"
              value={parentDir}
              onChange={(e) => { onParentDirChange(e.target.value); onProjectCreatedChange(false); }}
              placeholder="例如：E:\GalTransl\projects"
            />
            <Button className="field__browse-button" variant="secondary" onClick={onSelectParentDir}>浏览</Button>
          </div>
          <span className="field__hint">建议选择英文路径，避免空格与特殊字符。</span>
        </div>
        <div className="field">
          <span className="field__label">项目名称</span>
          <input
            className="field__input"
            autoComplete="off"
            value={projectName}
            onChange={(e) => { onProjectNameChange(e.target.value); onProjectCreatedChange(false); }}
            placeholder="例如：MyProject"
          />
        </div>
        <div className="wizard-path-preview">
          <span className="wizard-path-preview__label">将创建目录</span>
          <code className="wizard-path-preview__path">{projectDir || '请先填写父目录与项目名称'}</code>
          <div className="wizard-path-preview__meta">包含 `gt_input` / `gt_output` / `transl_cache` 与 `config.yaml`</div>
        </div>
      </div>
      <div className="wizard-actions">
        <Button disabled={projectCreated || !parentDir || !projectName} onClick={onCreateProject}>
          {projectCreated ? '已创建 <Icon name="check" size={14} />' : '创建项目'}
        </Button>
      </div>
    </Panel>
  );
}
