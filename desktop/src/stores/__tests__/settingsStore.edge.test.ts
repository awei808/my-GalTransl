import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useSettingsStore } from '../settingsStore';

const mockStorage: Record<string, string> = {};
const localStorageMock = {
  getItem: vi.fn((key: string) => mockStorage[key] ?? null),
  setItem: vi.fn((key: string, value: string) => { mockStorage[key] = value; }),
  removeItem: vi.fn((key: string) => { delete mockStorage[key]; }),
  clear: vi.fn(() => { for (const k of Object.keys(mockStorage)) delete mockStorage[k]; }),
};
vi.stubGlobal('localStorage', localStorageMock);

describe('useSettingsStore — edge cases', () => {
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

  describe('setCustomBackground edge cases', () => {
    it('handles empty object {} (no change)', () => {
      const before = useSettingsStore.getState().customBackground;
      useSettingsStore.getState().setCustomBackground({});
      const after = useSettingsStore.getState().customBackground;
      expect(after).toEqual(before);
    });

    it('handles partial update with only path', () => {
      useSettingsStore.getState().setCustomBackground({ path: '/new/bg.png' });
      const bg = useSettingsStore.getState().customBackground;
      expect(bg.path).toBe('/new/bg.png');
      // Other fields preserved
      expect(bg.opacity).toBe(0.85);
      expect(bg.containerOpacity).toBe(0.92);
      expect(bg.enabled).toBe(false);
    });

    it('handles setting path to null', () => {
      useSettingsStore.getState().setCustomBackground({ path: '/bg.png' });
      useSettingsStore.getState().setCustomBackground({ path: null });
      expect(useSettingsStore.getState().customBackground.path).toBeNull();
    });

    it('handles setting opacity to 0', () => {
      useSettingsStore.getState().setCustomBackground({ opacity: 0 });
      expect(useSettingsStore.getState().customBackground.opacity).toBe(0);
    });

    it('handles setting opacity to 1 (max)', () => {
      useSettingsStore.getState().setCustomBackground({ opacity: 1 });
      expect(useSettingsStore.getState().customBackground.opacity).toBe(1);
    });

    it('handles setting opacity > 1 (no validation, stores as-is)', () => {
      useSettingsStore.getState().setCustomBackground({ opacity: 1.5 });
      // Store does not validate — UI should clamp
      expect(useSettingsStore.getState().customBackground.opacity).toBe(1.5);
    });

    it('handles rapid partial updates', () => {
      useSettingsStore.getState().setCustomBackground({ path: '/a' });
      useSettingsStore.getState().setCustomBackground({ opacity: 0.5 });
      useSettingsStore.getState().setCustomBackground({ enabled: true });
      const bg = useSettingsStore.getState().customBackground;
      expect(bg.path).toBe('/a');
      expect(bg.opacity).toBe(0.5);
      expect(bg.enabled).toBe(true);
    });
  });

  describe('setCacheFontSize edge cases', () => {
    it('handles 0', () => {
      useSettingsStore.getState().setCacheFontSize(0);
      expect(useSettingsStore.getState().cacheFontSize).toBe(0);
    });

    it('handles negative', () => {
      useSettingsStore.getState().setCacheFontSize(-5);
      expect(useSettingsStore.getState().cacheFontSize).toBe(-5);
    });

    it('handles very large value', () => {
      useSettingsStore.getState().setCacheFontSize(99999);
      expect(useSettingsStore.getState().cacheFontSize).toBe(99999);
    });

    it('handles fractional value', () => {
      useSettingsStore.getState().setCacheFontSize(14.5);
      expect(useSettingsStore.getState().cacheFontSize).toBe(14.5);
    });
  });

  describe('setHomeJobRetention edge cases', () => {
    it('handles 0', () => {
      useSettingsStore.getState().setHomeJobRetention(0);
      expect(useSettingsStore.getState().homeJobRetention).toBe(0);
    });

    it('handles negative', () => {
      useSettingsStore.getState().setHomeJobRetention(-10);
      expect(useSettingsStore.getState().homeJobRetention).toBe(-10);
    });
  });

  describe('loadFromStorage edge cases', () => {
    it('handles corrupted JSON gracefully', () => {
      mockStorage['galtransl-settings'] = '{invalid json!!!';
      expect(() => useSettingsStore.getState().loadFromStorage()).not.toThrow();
      expect(useSettingsStore.getState().themeMode).toBe('system');
    });

    it('handles empty string in storage', () => {
      mockStorage['galtransl-settings'] = '';
      expect(() => useSettingsStore.getState().loadFromStorage()).not.toThrow();
    });

    it('handles JSON null value', () => {
      mockStorage['galtransl-settings'] = 'null';
      expect(() => useSettingsStore.getState().loadFromStorage()).not.toThrow();
    });

    it('handles JSON array instead of object', () => {
      mockStorage['galtransl-settings'] = '[1,2,3]';
      expect(() => useSettingsStore.getState().loadFromStorage()).not.toThrow();
    });

    it('handles partial object with only themeMode', () => {
      mockStorage['galtransl-settings'] = JSON.stringify({ themeMode: 'dark' });
      useSettingsStore.getState().loadFromStorage();
      expect(useSettingsStore.getState().themeMode).toBe('dark');
      // Other fields should fall back to defaults
      expect(useSettingsStore.getState().cacheFontSize).toBe(14);
    });

    it('handles customBackground with missing fields (spread merge)', () => {
      mockStorage['galtransl-settings'] = JSON.stringify({
        customBackground: { path: '/bg.png' },
      });
      useSettingsStore.getState().loadFromStorage();
      const bg = useSettingsStore.getState().customBackground;
      expect(bg.path).toBe('/bg.png');
      expect(bg.opacity).toBe(0.85);  // default
      expect(bg.containerOpacity).toBe(0.92);  // default
      expect(bg.enabled).toBe(false);  // default
    });

    it('handles customBackground with null value in storage', () => {
      mockStorage['galtransl-settings'] = JSON.stringify({
        customBackground: null,
      });
      useSettingsStore.getState().loadFromStorage();
      // null spreads as { ...DEFAULTS, ...null } → just DEFAULTS
      expect(useSettingsStore.getState().customBackground).toEqual({
        path: null, opacity: 0.85, containerOpacity: 0.92, enabled: false,
      });
    });

    it('handles wrong type for themeMode (number instead of string)', () => {
      mockStorage['galtransl-settings'] = JSON.stringify({ themeMode: 123 });
      useSettingsStore.getState().loadFromStorage();
      // Store doesn't validate types, will store 123 as themeMode
      // This is a known limitation — validation should be done in UI
      expect(useSettingsStore.getState().themeMode).toBe(123 as unknown as string);
    });

    it('handles wrong type for cacheFontSize (string instead of number)', () => {
      mockStorage['galtransl-settings'] = JSON.stringify({ cacheFontSize: 'big' });
      useSettingsStore.getState().loadFromStorage();
      expect(useSettingsStore.getState().cacheFontSize).toBe('big' as unknown as number);
    });
  });

  describe('persistence edge cases', () => {
    it('saveToStorage does not crash when localStorage throws', () => {
      localStorageMock.setItem.mockImplementationOnce(() => {
        throw new Error('QuotaExceededError');
      });
      expect(() => {
        useSettingsStore.getState().setThemeMode('dark');
      }).not.toThrow();
      // State should still be updated even if persistence fails
      expect(useSettingsStore.getState().themeMode).toBe('dark');
    });

    it('loadFromStorage does not crash when getItem throws', () => {
      localStorageMock.getItem.mockImplementationOnce(() => {
        throw new Error('SecurityError');
      });
      expect(() => useSettingsStore.getState().loadFromStorage()).not.toThrow();
    });
  });

  describe('setThemeMode edge cases', () => {
    it('handles invalid mode string (no validation)', () => {
      // TypeScript prevents this at compile time, but runtime has no check
      useSettingsStore.getState().setThemeMode('purple' as unknown as 'dark');
      expect(useSettingsStore.getState().themeMode).toBe('purple' as unknown as 'dark');
    });
  });
});
