interface StepProjectInfoProps {
  parentDir: string;
  projectName: string;
  projectDir: string;
  projectCreated: boolean;
  onSelectParentDir: () => void;
  onParentDirChange: (v: string) => void;
  onProjectNameChange: (v: string) => void;
  onProjectCreatedChange: (v: boolean) => void;
  onCreateProject: () => void;
}

export function StepProjectInfo(props: StepProjectInfoProps) {
  return (
    <div class="wizard-panel">
      <h3 class="wizard-panel-title">项目位置</h3>
      <p class="wizard-panel-desc">选择项目文件夹的保存位置和项目名称，然后创建项目结构。</p>
      <div class="wizard-form-grid">
        <div class="field">
          <span class="field__label">父目录</span>
          <div class="field__row">
            <input
              class="field__input"
              value={props.parentDir}
              onInput={(e) => {
                props.onParentDirChange(e.currentTarget.value);
                props.onProjectCreatedChange(false);
              }}
              placeholder="例如：E:\GalTransl\projects"
            />
            <button class="btn btn--sm" onClick={props.onSelectParentDir}>
              浏览
            </button>
          </div>
          <span class="field__hint">建议选择英文路径，避免空格与特殊字符。</span>
        </div>
        <div class="field">
          <span class="field__label">项目名称</span>
          <input
            class="field__input"
            value={props.projectName}
            onInput={(e) => {
              props.onProjectNameChange(e.currentTarget.value);
              props.onProjectCreatedChange(false);
            }}
            placeholder="例如：MyProject"
          />
        </div>
        <div class="wizard-path-preview">
          <span class="wizard-path-preview__label">将创建目录</span>
          <code class="wizard-path-preview__path">
            {props.projectDir || "请先填写父目录与项目名称"}
          </code>
          <div class="wizard-path-preview__meta">
            包含 gt_input / gt_output / transl_cache 与 config.yaml
          </div>
        </div>
      </div>
      <div class="wizard-actions">
        <button
          class="btn btn--primary"
          disabled={props.projectCreated || !props.parentDir || !props.projectName}
          onClick={props.onCreateProject}
        >
          {props.projectCreated ? "已创建 ✓" : "创建项目"}
        </button>
      </div>
    </div>
  );
}
