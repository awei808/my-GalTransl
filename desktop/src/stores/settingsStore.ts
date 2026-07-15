import { create } from 'zustand';

/**
 * Settings Store — global user preferences (theme, appearance, display).
 *
 * Persists to localStorage. Will replace the theme/background state
 * currently managed in App.tsx and SettingsPage.tsx (migration in Stage 2+).
 *
 * Theme application (setting data-theme on <html>) is done by a
 * ThemeSyncer component that subscribes to this store, not inside the
 * store itself — keeping the store pure and testable.
 */

export type ThemeMode = 'light' | 'dark' | 'system';

export interface CustomBackground {
  path: string | null;
  opacity: number;
  containerOpacity: number;
  enabled: boolean;
}

export interface SettingsState {
  // Theme
  themeMode: ThemeMode;
  resolvedTheme: 'light' | 'dark';

  // Custom background
  customBackground: CustomBackground;

  // Display preferences
  cacheFontSize: number;
  hideConsole: boolean;

  // Home page retention
  homeJobRetention: number;

  // Actions
  setThemeMode: (mode: ThemeMode) => void;
  setResolvedTheme: (theme: 'light' | 'dark') => void;
  setCustomBackground: (bg: Partial<CustomBackground>) => void;
  setCacheFontSize: (size: number) => void;
  setHideConsole: (hide: boolean) => void;
  setHomeJobRetention: (n: number) => void;

  // Persistence
  loadFromStorage: () => void;
  saveToStorage: () => void;
}

const STORAGE_KEY = 'galtransl-settings';

interface PersistedSettings {
  themeMode: ThemeMode;
  customBackground: CustomBackground;
  cacheFontSize: number;
  hideConsole: boolean;
  homeJobRetention: number;
}

function loadPersisted(): Partial<PersistedSettings> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    // JSON.parse can return null, arrays, or primitives — only use objects
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed;
    }
    return {};
  } catch {
    return {};
  }
}

function persistSettings(state: SettingsState) {
  try {
    const data: PersistedSettings = {
      themeMode: state.themeMode,
      customBackground: state.customBackground,
      cacheFontSize: state.cacheFontSize,
      hideConsole: state.hideConsole,
      homeJobRetention: state.homeJobRetention,
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch {
    // ignore storage errors
  }
}

const DEFAULTS = {
  themeMode: 'system' as ThemeMode,
  resolvedTheme: 'light' as 'light' | 'dark',
  customBackground: {
    path: null,
    opacity: 0.85,
    containerOpacity: 0.92,
    enabled: false,
  },
  cacheFontSize: 14,
  hideConsole: false,
  homeJobRetention: 20,
};

export const useSettingsStore = create<SettingsState>((set, get) => ({
  ...DEFAULTS,

  setThemeMode: (mode) => {
    set({ themeMode: mode });
    get().saveToStorage();
  },

  setResolvedTheme: (theme) => set({ resolvedTheme: theme }),

  setCustomBackground: (bg) => {
    set((state) => ({
      customBackground: { ...state.customBackground, ...bg },
    }));
    get().saveToStorage();
  },

  setCacheFontSize: (size) => {
    set({ cacheFontSize: size });
    get().saveToStorage();
  },

  setHideConsole: (hide) => {
    set({ hideConsole: hide });
    get().saveToStorage();
  },

  setHomeJobRetention: (n) => {
    set({ homeJobRetention: n });
    get().saveToStorage();
  },

  loadFromStorage: () => {
    const persisted = loadPersisted();
    set({
      themeMode: persisted.themeMode ?? DEFAULTS.themeMode,
      customBackground: { ...DEFAULTS.customBackground, ...(persisted.customBackground ?? {}) },
      cacheFontSize: persisted.cacheFontSize ?? DEFAULTS.cacheFontSize,
      hideConsole: persisted.hideConsole ?? DEFAULTS.hideConsole,
      homeJobRetention: persisted.homeJobRetention ?? DEFAULTS.homeJobRetention,
    });
  },

  saveToStorage: () => persistSettings(get()),
}));
