import { describe, it, expect } from 'vitest';
import {
  type ConfigSectionKey,
  type ConfigSectionDef,
  CONFIG_SECTIONS,
  ConfigSectionNav,
} from '../ConfigSectionNav';
import { render } from '@testing-library/react';

// ── ConfigSectionKey type validation ──

describe('ConfigSectionKey — type acceptance', () => {
  it('accepts legacy section keys', () => {
    const keys: ConfigSectionKey[] = ['common', 'backendSpecific', 'plugin', 'dictionary', 'problemAnalyze', 'retranslKey'];
    expect(keys).toHaveLength(6);
  });

  it('accepts new stage-4 section keys', () => {
    const keys: ConfigSectionKey[] = ['backendProfiles', 'prompts'];
    expect(keys).toHaveLength(2);
  });

  it('all 8 sections have unique keys', () => {
    const allKeys: ConfigSectionKey[] = ['common', 'backendSpecific', 'backendProfiles', 'prompts', 'plugin', 'dictionary', 'problemAnalyze', 'retranslKey'];
    expect(new Set(allKeys).size).toBe(allKeys.length);
  });
});

// ── CONFIG_SECTIONS array ──

describe('CONFIG_SECTIONS — entries', () => {
  it('has 8 entries after stage-4', () => {
    expect(CONFIG_SECTIONS).toHaveLength(8);
  });

  it('every entry has required fields', () => {
    for (const section of CONFIG_SECTIONS) {
      expect(section.key).toBeTruthy();
      expect(section.label).toBeTruthy();
      expect(section.icon).toBeTruthy();
    }
  });

  it('new entries have correct icons', () => {
    const profiles = CONFIG_SECTIONS.find((s) => s.key === 'backendProfiles');
    expect(profiles).toBeDefined();
    expect(profiles!.icon).toBe('server');

    const prompts = CONFIG_SECTIONS.find((s) => s.key === 'prompts');
    expect(prompts).toBeDefined();
    expect(prompts!.icon).toBe('file-text');
  });

  it('all icons reference strings (no undefined)', () => {
    for (const section of CONFIG_SECTIONS) {
      expect(typeof section.icon).toBe('string');
      expect(section.icon.length).toBeGreaterThan(0);
    }
  });

  it('all keys are from ConfigSectionKey union', () => {
    const validKeys: ConfigSectionKey[] = ['common', 'backendSpecific', 'backendProfiles', 'prompts', 'plugin', 'dictionary', 'problemAnalyze', 'retranslKey'];
    const validSet = new Set(validKeys);
    for (const section of CONFIG_SECTIONS) {
      expect(validSet.has(section.key)).toBe(true);
    }
  });
});

// ── ConfigSectionNav render ──

describe('ConfigSectionNav — render', () => {
  const defaultProps = {
    activeSection: 'common' as ConfigSectionKey,
    onSectionChange: () => {},
    yamlView: false,
    onYamlToggle: () => {},
    onSave: () => {},
    saving: false,
    dirty: false,
  };

  it('renders all 8 section buttons + YAML toggle (9 total)', () => {
    const { container } = render(<ConfigSectionNav {...defaultProps} />);
    // 8 section buttons + 1 YAML toggle = 9; save button has different class
    const buttons = container.querySelectorAll('.project-config-page__section-btn');
    expect(buttons).toHaveLength(9);
  });

  it('renders backendProfiles button with label "后端配置"', () => {
    const { getByText } = render(<ConfigSectionNav {...defaultProps} />);
    expect(getByText('后端配置')).toBeDefined();
  });

  it('renders prompts button with label "提示词模板"', () => {
    const { getByText } = render(<ConfigSectionNav {...defaultProps} />);
    expect(getByText('提示词模板')).toBeDefined();
  });

  it('highlights active section', () => {
    const { container } = render(<ConfigSectionNav {...defaultProps} activeSection="backendProfiles" />);
    const activeBtns = container.querySelectorAll('.project-config-page__section-btn--active');
    expect(activeBtns).toHaveLength(1);
    expect(activeBtns[0].textContent).toContain('后端配置');
  });

  it('highlights YAML button when yamlView is true', () => {
    const { container } = render(<ConfigSectionNav {...defaultProps} yamlView={true} />);
    // All 10 buttons with section-btn class; the YAML one is the last and should be active
    const allBtns = container.querySelectorAll('.project-config-page__section-btn--active');
    const yamlBtn = Array.from(allBtns).find((b) => b.textContent?.includes('YAML源码'));
    expect(yamlBtn).toBeDefined();
  });

  it('calls onSectionChange when clicking a section', () => {
    let changed = '';
    const { container } = render(
      <ConfigSectionNav {...defaultProps} onSectionChange={(s) => { changed = s; }} />,
    );
    const promptsBtn = Array.from(container.querySelectorAll('.project-config-page__section-btn'))
      .find((b) => b.textContent?.includes('提示词模板'));
    promptsBtn?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(changed).toBe('prompts');
  });

  it('dirty indicator renders when dirty and not saving', () => {
    const { container } = render(<ConfigSectionNav {...defaultProps} dirty={true} saving={false} />);
    const saveBtn = container.querySelector('.project-config-page__save-btn');
    expect(saveBtn?.textContent).toMatch(/保存配置/);
    // React converts inline hex colors to rgb() in innerHTML
    expect(saveBtn?.innerHTML).toContain('rgb(229, 62, 62)');
  });

  it('save button shows "保存中…" when saving', () => {
    const { container } = render(<ConfigSectionNav {...defaultProps} saving={true} />);
    expect(container.querySelector('.project-config-page__save-btn')?.textContent).toBe('保存中…');
  });
});
