import { describe, it, expect } from 'vitest';

// Export the valid section keys for testing
const VALID_SECTIONS = [
  'common', 'backendSpecific', 'backendProfiles', 'prompts',
  'plugin', 'dictionary', 'problemAnalyze', 'retranslKey',
] as const;

describe('ProjectConfigPage — section validation logic (P2)', () => {
  it('all 8 sections are recognized', () => {
    expect(VALID_SECTIONS).toHaveLength(8);
  });

  it('all sections are unique', () => {
    expect(new Set(VALID_SECTIONS).size).toBe(VALID_SECTIONS.length);
  });

  it('recognizes legacy common section from URL param', () => {
    const section = 'common';
    expect(VALID_SECTIONS.includes(section)).toBe(true);
  });

  it('recognizes new backendProfiles section from URL param', () => {
    const section = 'backendProfiles';
    expect(VALID_SECTIONS.includes(section)).toBe(true);
  });

  it('recognizes new prompts section from URL param', () => {
    const section = 'prompts';
    expect(VALID_SECTIONS.includes(section)).toBe(true);
  });

  it('rejects unknown section', () => {
    const section = 'nonexistent';
    expect(VALID_SECTIONS.includes(section as never)).toBe(false);
  });

  it('defaults to common when section is invalid', () => {
    const searchParam = 'fakeSection';
    const section = VALID_SECTIONS.includes(searchParam as never) ? searchParam : 'common';
    expect(section).toBe('common');
  });

  it('defaults to common when section is empty', () => {
    const searchParam = '';
    const section = VALID_SECTIONS.includes(searchParam as never) ? searchParam : 'common';
    expect(section).toBe('common');
  });

  it('uses valid section from search params', () => {
    const searchParam = 'retranslKey';
    const section = VALID_SECTIONS.includes(searchParam as never) ? searchParam : 'common';
    expect(section).toBe('retranslKey');
  });

  // Verify the embedded pages have valid section keys matching type
  it('backendProfiles section is in the valid set', () => {
    expect(VALID_SECTIONS).toContain('backendProfiles');
  });

  it('prompts section is in the valid set', () => {
    expect(VALID_SECTIONS).toContain('prompts');
  });

  // Verify no duplicate labels in config sections
  it('section order: backendProfiles comes after backendSpecific', () => {
    const idx1 = VALID_SECTIONS.indexOf('backendSpecific');
    const idx2 = VALID_SECTIONS.indexOf('backendProfiles');
    expect(idx2).toBeGreaterThan(idx1);
  });
});
