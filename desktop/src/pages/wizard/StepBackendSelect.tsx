import { CustomSelect } from '../../components/CustomSelect';
import { Panel } from '../../components/Panel';

type StepBackendSelectProps = {
  selectedBackend: string;
  onBackendChange: (value: string) => void;
  backendProfileNames: string[];
  defaultBackendName: string;
};

export function StepBackendSelect({ selectedBackend, onBackendChange, backendProfileNames, defaultBackendName }: StepBackendSelectProps) {
  return (
    <Panel title="翻译后端" description="选择翻译后端配置，也可以跳过此步骤在配置编辑中设置。">
      <div className="field">
        <span className="field__label">后端配置</span>
        <CustomSelect value={selectedBackend} onChange={(e) => onBackendChange(e.target.value)}>
          <option value="__default__">跟随全局默认</option>
          <option value="">不使用（使用项目自身配置）</option>
          {backendProfileNames.map((name) => (<option key={name} value={name}>{name}</option>))}
        </CustomSelect>
        <span className="field__hint">
          {selectedBackend === '__default__'
            ? defaultBackendName
              ? `当前默认配置为「${defaultBackendName}」，可在「翻译后端配置」页面修改`
              : '尚未设置默认配置，请在「翻译后端配置」页面设置'
            : selectedBackend
              ? `翻译时将使用全局配置「${selectedBackend}」覆盖项目后端设置`
              : '将忽略全局配置，使用项目自身后端设置'}
        </span>
      </div>
    </Panel>
  );
}
