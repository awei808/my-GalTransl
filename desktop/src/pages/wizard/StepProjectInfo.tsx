interface StepProjectInfoProps {
  projectName: string;
  projectDir: string;
  previewDir: string;
  projectCreated: boolean;
  onProjectNameChange: (v: string) => void;
  onProjectCreatedChange: (v: boolean) => void;
  onCreateProject: () => void;
}

export function StepProjectInfo(props: StepProjectInfoProps) {
  return (
    <div class="wizard-panel">
      <h3 class="wizard-panel-title">项目位置</h3>
      <p class="wizard-panel-desc">
        输入项目名称，项目将创建在应用程序同目录下的后端工作区根目录（含 gt_input /
        gt_output / transl_cache 与 config.yaml）。
      </p>
      <div class="wizard-form-grid">
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
          <span class="field__hint">建议英文命名，避免空格与特殊字符。</span>
        </div>
        <div class="wizard-path-preview">
          <span class="wizard-path-preview__label">将创建目录</span>
          <code class="wizard-path-preview__path">
            {props.projectDir || props.previewDir || "请输入项目名称以预览完整路径"}
          </code>
          <div class="wizard-path-preview__meta">
            位于应用程序同目录下，包含 gt_input / gt_output / transl_cache 与 config.yaml
          </div>
        </div>
      </div>
      <div class="wizard-actions">
        <button
          class="btn btn--primary"
          disabled={props.projectCreated || !props.projectName.trim()}
          onClick={props.onCreateProject}
        >
          {props.projectCreated ? "已创建 ✓" : "创建项目"}
        </button>
      </div>
    </div>
  );
}
