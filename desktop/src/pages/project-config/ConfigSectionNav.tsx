import type { ReactNode } from 'react';
import { Icon } from '../../components/icons';

export type ConfigSectionKey = 'common' | 'backendSpecific' | 'plugin' | 'dictionary' | 'problemAnalyze' | 'retranslKey' | 'backendProfiles' | 'prompts';

export interface ConfigSectionDef {
  key: ConfigSectionKey;
  label: string;
  icon: string;
}

export const CONFIG_SECTIONS: ConfigSectionDef[] = [
  { key: 'common', label: '通用设置', icon: 'settings' },
  { key: 'backendSpecific', label: '翻译后端', icon: 'cpu' },
  { key: 'backendProfiles', label: '后端配置', icon: 'server' },
  { key: 'prompts', label: '提示词模板', icon: 'file-text' },
  { key: 'plugin', label: '插件设置', icon: 'puzzle' },
  { key: 'dictionary', label: '字典设置', icon: 'book' },
  { key: 'problemAnalyze', label: '问题分析', icon: 'search' },
  { key: 'retranslKey', label: '重翻关键字', icon: 'refresh' },
];

interface ConfigSectionNavProps {
  activeSection: ConfigSectionKey;
  onSectionChange: (section: ConfigSectionKey) => void;
  yamlView: boolean;
  onYamlToggle: () => void;
  onSave: () => void;
  saving: boolean;
  dirty: boolean;
  disabled?: boolean;
  extraActions?: ReactNode;
}

export function ConfigSectionNav({
  activeSection,
  onSectionChange,
  yamlView,
  onYamlToggle,
  onSave,
  saving,
  dirty,
  disabled = false,
}: ConfigSectionNavProps) {
  return (
    <aside className="project-config-page__sidebar">
      {CONFIG_SECTIONS.map((section) => (
        <button
          type="button"
          key={section.key}
          className={`project-config-page__section-btn ${activeSection === section.key ? 'project-config-page__section-btn--active' : ''}`}
          onClick={() => { onSectionChange(section.key); }}
        >
          <span className="project-config-page__section-icon"><Icon name={section.icon} size={18} /></span>
          <span>{section.label}</span>
        </button>
      ))}
      <button
        type="button"
        className="project-config-page__save-btn"
        onClick={onSave}
        disabled={saving || disabled}
      >
        <span className="project-config-page__section-icon"><Icon name="download" size={18} /></span>
        <span>{saving ? '保存中…' : '保存配置'}{dirty && !saving && <span style={{ color: '#e53e3e', marginLeft: 4 }}>●</span>}</span>
      </button>
      <div className="project-config-page__section-divider" />
      <button
        type="button"
        className={`project-config-page__section-btn ${yamlView ? 'project-config-page__section-btn--active' : ''}`}
        onClick={onYamlToggle}
      >
        <span className="project-config-page__section-icon"><Icon name="file-text" size={18} /></span>
        <span>YAML源码</span>
      </button>
    </aside>
  );
}
