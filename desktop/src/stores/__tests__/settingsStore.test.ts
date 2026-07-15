import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useSettingsStore } from '../settingsStore';

// Mock localStorage
const mockStorage: Record<string, string> = {};
const localStorageMock = {
  getItem: vi.fn((key: string) => mockStorage[key] ?? null),
  setItem: vi.fn((key: string, value: string) => { mockStorage[key] = value; }),
  removeItem: vi.fn((key: string) => { delete mockStorage[key]; }),
  clear: vi.fn(() => { for (const k of Object.keys(mockStorage)) delete mockStorage[k]; }),
};
vi.stubGlobal('localStorage', localStorageMock);

describe('useSettingsStore', () => {
  beforeEach(() => {
    localStorageMock.clear();
    localStorageMock.getItem.mockClear();
    localStorageMock.setItem.mockClear();
    useSettingsStore.setState({
      themeMode: 'system',
      resolvedTheme: 'light',
      customBackground: { path: null, opacity: 0.85, containerOpacity: 0.92, enabled: false },
      cacheFontSize: 14,
      hideConsole: false,
      homeJobRetention: 20,
    });
  });

  it('starts with defaults', () => {
    const state = useSettingsStore.getState();
    expect(state.themeMode).toBe('system');
    expect(state.cacheFontSize).toBe(14);
    expect(state.hideConsole).toBe(false);
  });

  it('setThemeMode updates and persists', () => {
    useSettingsStore.getState().setThemeMode('dark');
    expect(useSettingsStore.getState().themeMode).toBe('dark');
    expect(localStorageMock.setItem).toHaveBeenCalled();
  });

  it('setCacheFontSize updates and persists', () => {
    useSettingsStore.getState().setCacheFontSize(16);
    expect(useSettingsStore.getState().cacheFontSize).toBe(16);
    expect(localStorageMock.setItem).toHaveBeenCalled();
  });

  it('setCustomBackground merges partial updates', () => {
    useSettingsStore.getState().setCustomBackground({ opacity: 0.5 });
    const bg = useSettingsStore.getState().customBackground;
    expect(bg.opacity).toBe(0.5);
    // Other fields should remain
    expect(bg.containerOpacity).toBe(0.92);
    expect(bg.enabled).toBe(false);
  });

  it('setHideConsole updates and persists', () => {
    useSettingsStore.getState().setHideConsole(true);
    expect(useSettingsStore.getState().hideConsole).toBe(true);
  });

  it('setResolvedTheme updates without persisting (derived state)', () => {
    useSettingsStore.getState().setResolvedTheme('dark');
    expect(useSettingsStore.getState().resolvedTheme).toBe('dark');
    // setResolvedTheme should not call saveToStorage
    expect(localStorageMock.setItem).not.toHaveBeenCalled();
  });

  it('saveToStorage writes all fields', () => {
    useSettingsStore.getState().setThemeMode('dark');
    useSettingsStore.getState().saveToStorage();
    const saved = JSON.parse(mockStorage['galtransl-settings']);
    expect(saved.themeMode).toBe('dark');
    expect(saved.customBackground).toBeDefined();
    expect(saved.cacheFontSize).toBeDefined();
  });

  it('loadFromStorage restores persisted values', () => {
    mockStorage['galtransl-settings'] = JSON.stringify({
      themeMode: 'dark',
      customBackground: { path: '/img/bg.png', opacity: 0.7, containerOpacity: 0.9, enabled: true },
      cacheFontSize: 18,
      hideConsole: true,
      homeJobRetention: 50,
    });

    useSettingsStore.getState().loadFromStorage();

    const state = useSettingsStore.getState();
    expect(state.themeMode).toBe('dark');
    expect(state.customBackground.path).toBe('/img/bg.png');
    expect(state.cacheFontSize).toBe(18);
    expect(state.hideConsole).toBe(true);
    expect(state.homeJobRetention).toBe(50);
  });

  it('loadFromStorage merges partial customBackground with defaults', () => {
    // Simulate old persisted data missing containerOpacity
    mockStorage['galtransl-settings'] = JSON.stringify({
      themeMode: 'light',
      customBackground: { path: null, opacity: 0.6, enabled: true },
      cacheFontSize: 14,
      hideConsole: false,
      homeJobRetention: 20,
    });

    useSettingsStore.getState().loadFromStorage();

    const bg = useSettingsStore.getState().customBackground;
    expect(bg.opacity).toBe(0.6);
    expect(bg.enabled).toBe(true);
    // containerOpacity should fall back to default
    expect(bg.containerOpacity).toBe(0.92);
  });

  it('loadFromStorage uses defaults when no persisted data', () => {
    useSettingsStore.getState().loadFromStorage();
    expect(useSettingsStore.getState().themeMode).toBe('system');
    expect(useSettingsStore.getState().cacheFontSize).toBe(14);
  });

  it('loadFromStorage handles corrupted localStorage gracefully', () => {
    mockStorage['galtransl-settings'] = '{invalid json';
    useSettingsStore.getState().loadFromStorage();
    // Should fall back to defaults without throwing
    expect(useSettingsStore.getState().themeMode).toBe('system');
  });
});
