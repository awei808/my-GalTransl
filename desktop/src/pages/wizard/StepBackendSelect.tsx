import {
  getBackendProfileNames,
  getDefaultBackendProfile,
} from "../../lib/api/preferences";

interface StepBackendSelectProps {
  selectedBackend: string;
  onBackendChange: (v: string) => void;
}

export function StepBackendSelect(props: StepBackendSelectProps) {
  const names = getBackendProfileNames();
  const defaultName = getDefaultBackendProfile();

  let hint = "";
  if (props.selectedBackend === "__default__") {
    hint = defaultName
      ? `当前默认配置为「${defaultName}」，可在「翻译后端配置」页面修改`
      : "尚未设置默认配置，请在「翻译后端配置」页面设置";
  } else if (props.selectedBackend) {
    hint = `翻译时将使用全局配置「${props.selectedBackend}」覆盖项目后端设置`;
  } else {
    hint = "将忽略全局配置，使用项目自身后端设置";
  }

  return (
    <div class="wizard-panel">
      <h3 class="wizard-panel-title">翻译后端</h3>
      <p class="wizard-panel-desc">选择翻译后端配置，也可以跳过此步骤在配置编辑中设置。</p>
      <div class="field">
        <span class="field__label">后端配置</span>
        <select
          class="field__input"
          value={props.selectedBackend}
          onChange={(e) => props.onBackendChange(e.currentTarget.value)}
        >
          <option value="__default__">跟随全局默认</option>
          <option value="">不使用（使用项目自身配置）</option>
          {names.map((name) => (<option value={name}>{name}</option>))}
        </select>
        <span class="field__hint">{hint}</span>
      </div>
    </div>
  );
}
