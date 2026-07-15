import { Button } from '../../components/Button';
import { CustomSelect } from '../../components/CustomSelect';
import { getSelectedBackendProfile } from '../../lib/api';

type NamePageAiPopoverProps = {
  show: boolean;
  profileNames: string[];
  selectedProfile: string;
  modelMap: Record<string, string>;
  projectDir: string;
  onProfileChange: (profile: string) => void;
  onTranslate: () => void;
};

export function NamePageAiPopover({
  show, profileNames, selectedProfile, modelMap, projectDir,
  onProfileChange, onTranslate,
}: NamePageAiPopoverProps) {
  if (!show) return null;

  const def = getSelectedBackendProfile(projectDir);
  const sorted = def && profileNames.includes(def)
    ? [def, ...profileNames.filter((n) => n !== def)]
    : profileNames;

  return (
    <div className="name-page__ai-popover">
      <div className="name-page__ai-popover-title">选择翻译后端</div>
      {profileNames.length === 0 ? (
        <div className="name-page__ai-popover-empty">未找到后端配置，请先在「后端配置」页添加 OpenAI 兼容接口</div>
      ) : (
        <>
          <CustomSelect className="name-page__ai-popover-select" value={selectedProfile} onChange={(e) => onProfileChange(e.target.value)}>
            {sorted.map((name) => {
              const model = modelMap[name];
              const suffix = name === def ? '（默认）' : '';
              return <option key={name} value={name}>{model ? `${name} - ${model}${suffix}` : `${name}${suffix}`}</option>;
            })}
          </CustomSelect>
          <Button variant="primary" onClick={onTranslate} disabled={!selectedProfile}>开始翻译</Button>
        </>
      )}
    </div>
  );
}
