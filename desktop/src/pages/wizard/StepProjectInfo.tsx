import { Show } from "solid-js";

interface StepProjectInfoProps {
  projectName: string;
  projectDir: string;
  previewDir: string;
  projectCreated: boolean;
  isDesktop: boolean;
  useCustomLocation: boolean;
  customParentDir: string;
  onUseCustomLocationChange: (v: boolean) => void;
  onCustomParentDirChange: (v: string) => void;
  onBrowseParentDir: () => void;
  onProjectNameChange: (v: string) => void;
  onInvalidateCreated: () => void;
  onCreateProject: () => void;
}

export function StepProjectInfo(props: StepProjectInfoProps) {
  const createDisabled = () =>
    props.projectCreated ||
    !props.projectName.trim() ||
    (props.useCustomLocation && !props.customParentDir.trim());

  const previewPlaceholder = () =>
    props.useCustomLocation ? "请选择或输入父目录与项目名称以预览完整路径" : "请输入项目名称以预览完整路径";

  return (
    <div class="wizard-panel">
      <h3 class="wizard-panel-title">项目位置</h3>
      <p class="wizard-panel-desc">
        输入项目名称并选择创建位置，项目将包含 gt_input / gt_output / transl_cache 与
        config.yaml。
      </p>
      <div class="wizard-form-grid">
        <div class="field">
          <span class="field__label">项目名称</span>
          <input
            class="field__input"
            value={props.projectName}
            onInput={(e) => {
              props.onProjectNameChange(e.currentTarget.value);
              props.onInvalidateCreated();
            }}
            placeholder="例如：MyProject"
          />
          <span class="field__hint">建议英文命名，避免空格与特殊字符。</span>
        </div>
        <div class="field">
          <span class="field__label">创建位置</span>
          <div class="text-plugins-selector">
            <label class="text-plugin-chip">
              <input
                type="radio"
                name="wizard-project-location"
                checked={!props.useCustomLocation}
                onChange={() => {
                  props.onUseCustomLocationChange(false);
                  props.onInvalidateCreated();
                }}
              />
              <span>应用程序目录</span>
            </label>
            <label class="text-plugin-chip">
              <input
                type="radio"
                name="wizard-project-location"
                checked={props.useCustomLocation}
                onChange={() => {
                  props.onUseCustomLocationChange(true);
                  props.onInvalidateCreated();
                }}
              />
              <span>自定义位置</span>
            </label>
          </div>
          <span class="field__hint">
            {props.useCustomLocation
              ? "选择磁盘上任意已存在的文件夹，项目将在其中创建。"
              : "创建在后端工作区根目录（应用程序同目录下）。"}
          </span>
        </div>
        <Show when={props.useCustomLocation}>
          <div class="field">
            <span class="field__label">父目录（项目将创建在该文件夹内）</span>
            <div class="wizard-parent-dir-row">
              <input
                class="field__input"
                value={props.customParentDir}
                onInput={(e) => {
                  props.onCustomParentDirChange(e.currentTarget.value);
                  props.onInvalidateCreated();
                }}
                placeholder="例如：D:\\GalTranslProjects"
              />
              <Show when={props.isDesktop}>
                <button class="btn" onClick={props.onBrowseParentDir}>
                  浏览…
                </button>
              </Show>
            </div>
            <span class="field__hint">必须是已存在的文件夹的绝对路径。</span>
          </div>
        </Show>
        <div class="wizard-path-preview">
          <span class="wizard-path-preview__label">将创建目录</span>
          <code class="wizard-path-preview__path">
            {props.projectDir || props.previewDir || previewPlaceholder()}
          </code>
          <div class="wizard-path-preview__meta">
            {props.useCustomLocation ? "位于自定义位置，" : "位于应用程序同目录下，"}
            包含 gt_input / gt_output / transl_cache 与 config.yaml
          </div>
        </div>
      </div>
      <div class="wizard-actions">
        <button
          class="btn btn--primary"
          disabled={createDisabled()}
          onClick={props.onCreateProject}
        >
          {props.projectCreated ? "已创建 ✓" : "创建项目"}
        </button>
      </div>
    </div>
  );
}
