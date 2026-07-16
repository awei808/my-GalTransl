import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useSettingsStore } from '../settingsStore';

describe('useSettingsStore', () => {
  beforeEach(() => {
    useSettingsStore.setState({
      themeMode: 'system',
      resolvedTheme: 'light',
      customBackground: {
        path: null,
        opacity: 0.85,
        containerOpacity: 0.92,
        enabled: false,
      },
      cacheFontSize: 14,
      hideConsole: false,
      homeJobRetention: 20,
    });
    localStorage.clear();
  });

  describe('initial state', () => {
    it('defaults to system theme', () => {
      expect(useSettingsStore.getState().themeMode).toBe('system');
    });

    it('defaults resolved theme to light', () => {
      expect(useSettingsStore.getState().resolvedTheme).toBe('light');
    });

    it('defaults custom background to disabled', () => {
      expect(useSettingsStore.getState().customBackground.enabled).toBe(false);
    });

    it('defaults cache font size to 14', () => {
      expect(useSettingsStore.getState().cacheFontSize).toBe(14);
    });

    it('defaults hide console to false', () => {
      expect(useSettingsStore.getState().hideConsole).toBe(false);
    });

    it('defaults home job retention to 20', () => {
      expect(useSettingsStore.getState().homeJobRetention).toBe(20);
    });
  });

  describe('setThemeMode', () => {
    it('sets light theme', () => {
      useSettingsStore.getState().setThemeMode('light');
      expect(useSettingsStore.getState().themeMode).toBe('light');
    });

    it('sets dark theme', () => {
      useSettingsStore.getState().setThemeMode('dark');
      expect(useSettingsStore.getState().themeMode).toBe('dark');
    });

    it('sets system theme', () => {
      useSettingsStore.getState().setThemeMode('system');
      expect(useSettingsStore.getState().themeMode).toBe('system');
    });

    it('persists to localStorage', () => {
      useSettingsStore.getState().setThemeMode('dark');
      const stored = JSON.parse(localStorage.getItem('galtransl-settings') || '{}');
      expect(stored.themeMode).toBe('dark');
    });
  });

  describe('setResolvedTheme', () => {
    it('sets resolved theme to dark', () => {
      useSettingsStore.getState().setResolvedTheme('dark');
      expect(useSettingsStore.getState().resolvedTheme).toBe('dark');
    });
  });

  describe('setCustomBackground', () => {
    it('sets partial properties without losing others', () => {
      useSettingsStore.getState().setCustomBackground({ enabled: true, opacity: 0.5 });
      const bg = useSettingsStore.getState().customBackground;
      expect(bg.enabled).toBe(true);
      expect(bg.opacity).toBe(0.5);
      expect(bg.path).toBeNull(); // unchanged
      expect(bg.containerOpacity).toBe(0.92); // unchanged
    });

    it('merges multiple updates', () => {
      useSettingsStore.getState().setCustomBackground({ enabled: true });
      useSettingsStore.getState().setCustomBackground({ opacity: 0.3 });
      const bg = useSettingsStore.getState().customBackground;
      expect(bg.enabled).toBe(true);
      expect(bg.opacity).toBe(0.3);
    });

    it('persists to localStorage', () => {
      useSettingsStore.getState().setCustomBackground({ enabled: true, path: '/bg.png' });
      const stored = JSON.parse(localStorage.getItem('galtransl-settings') || '{}');
      expect(stored.customBackground?.path).toBe('/bg.png');
    });
  });

  describe('setCacheFontSize', () => {
    it('updates cache font size', () => {
      useSettingsStore.getState().setCacheFontSize(18);
      expect(useSettingsStore.getState().cacheFontSize).toBe(18);
    });

    it('persists to localStorage', () => {
      useSettingsStore.getState().setCacheFontSize(20);
      const stored = JSON.parse(localStorage.getItem('galtransl-settings') || '{}');
      expect(stored.cacheFontSize).toBe(20);
    });

    it('handles minimum size', () => {
      useSettingsStore.getState().setCacheFontSize(1);
      expect(useSettingsStore.getState().cacheFontSize).toBe(1);
    });
  });

  describe('setHideConsole', () => {
    it('hides console', () => {
      useSettingsStore.getState().setHideConsole(true);
      expect(useSettingsStore.getState().hideConsole).toBe(true);
    });

    it('shows console', () => {
      useSettingsStore.getState().setHideConsole(true);
      useSettingsStore.getState().setHideConsole(false);
      expect(useSettingsStore.getState().hideConsole).toBe(false);
    });

    it('persists to localStorage', () => {
      useSettingsStore.getState().setHideConsole(true);
      const stored = JSON.parse(localStorage.getItem('galtransl-settings') || '{}');
      expect(stored.hideConsole).toBe(true);
    });
  });

  describe('setHomeJobRetention', () => {
    it('updates home job retention', () => {
      useSettingsStore.getState().setHomeJobRetention(50);
      expect(useSettingsStore.getState().homeJobRetention).toBe(50);
    });

    it('persists to localStorage', () => {
      useSettingsStore.getState().setHomeJobRetention(30);
      const stored = JSON.parse(localStorage.getItem('galtransl-settings') || '{}');
      expect(stored.homeJobRetention).toBe(30);
    });
  });

  describe('loadFromStorage', () => {
    it('loads persisted settings from localStorage', () => {
      localStorage.setItem('galtransl-settings', JSON.stringify({
        themeMode: 'dark',
        cacheFontSize: 22,
        hideConsole: true,
        homeJobRetention: 40,
        customBackground: { enabled: true, path: '/img.png', opacity: 0.6, containerOpacity: 0.8 },
      }));

      useSettingsStore.getState().loadFromStorage();
      expect(useSettingsStore.getState().themeMode).toBe('dark');
      expect(useSettingsStore.getState().cacheFontSize).toBe(22);
      expect(useSettingsStore.getState().hideConsole).toBe(true);
      expect(useSettingsStore.getState().homeJobRetention).toBe(40);
      expect(useSettingsStore.getState().customBackground.path).toBe('/img.png');
    });

    it('uses defaults when localStorage is empty', () => {
      useSettingsStore.getState().loadFromStorage();
      expect(useSettingsStore.getState().themeMode).toBe('system');
      expect(useSettingsStore.getState().cacheFontSize).toBe(14);
    });

    it('handles corrupted localStorage gracefully', () => {
      localStorage.setItem('galtransl-settings', '{invalid json');
      expect(() => useSettingsStore.getState().loadFromStorage()).not.toThrow();
      expect(useSettingsStore.getState().themeMode).toBe('system'); // default
    });

    it('handles null localStorage value gracefully', () => {
      // JSON.parse('null') returned null before the fix
      localStorage.setItem('galtransl-settings', 'null');
      expect(() => useSettingsStore.getState().loadFromStorage()).not.toThrow();
      expect(useSettingsStore.getState().themeMode).toBe('system');
    });

    it('handles array in localStorage gracefully', () => {
      localStorage.setItem('galtransl-settings', '[]');
      expect(() => useSettingsStore.getState().loadFromStorage()).not.toThrow();
      expect(useSettingsStore.getState().themeMode).toBe('system');
    });

    it('merges partial persisted settings with defaults', () => {
      localStorage.setItem('galtransl-settings', JSON.stringify({ themeMode: 'light' }));
      useSettingsStore.getState().loadFromStorage();
      expect(useSettingsStore.getState().themeMode).toBe('light');
      expect(useSettingsStore.getState().cacheFontSize).toBe(14); // default
    });
  });

  describe('saveToStorage', () => {
    it('writes current state to localStorage', () => {
      useSettingsStore.getState().setThemeMode('dark');
      useSettingsStore.getState().setCacheFontSize(16);

      const stored = JSON.parse(localStorage.getItem('galtransl-settings') || '{}');
      expect(stored.themeMode).toBe('dark');
      expect(stored.cacheFontSize).toBe(16);
    });

    it('does not store resolvedTheme (transient)', () => {
      useSettingsStore.getState().setResolvedTheme('dark');
      useSettingsStore.getState().saveToStorage();
      const stored = JSON.parse(localStorage.getItem('galtransl-settings') || '{}');
      expect(stored.resolvedTheme).toBeUndefined();
    });
  });
});
