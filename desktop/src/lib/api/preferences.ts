/**
 * Preferences — localStorage-based user preference management.
 *
 * Includes: backend profiles, theme, custom background, translator templates,
 * home limits, cache browser font size, console visibility, prompt template overrides.
 */
import type {
  AppSettings,
  BackendProfilesMap,
  CustomBackgroundPreference,
  PromptTemplateOverride,
  ThemeMode,
  SubmitJobPayload,
} from './types';

// ---- Storage keys ----

const BACKEND_PROFILE_KEY = 'galtransl-backend-profile';
const BACKEND_PROFILES_STORAGE_KEY = 'galtransl-backend-profiles';
const DEFAULT_BACKEND_PROFILE_KEY = 'galtransl-default-backend-profile';
const TRANSLATOR_TEMPLATE_KEY = 'galtransl-project-translator-template';
const HOME_HISTORY_LIMIT_KEY = 'galtransl-home-history-limit';
const HOME_JOB_LIMIT_KEY = 'galtransl-home-job-limit';
const THEME_MODE_KEY = 'galtransl-theme-mode';
const CUSTOM_BACKGROUND_KEY = 'galtransl-custom-background';
const HIDE_BACKEND_CONSOLE_KEY = 'galtransl-hide-backend-console';
const CACHE_BROWSER_FONT_SIZE_KEY = 'galtransl-cache-browser-font-size';
const PROMPT_TEMPLATES_OVERRIDES_KEY = 'galtransl_prompt_templates_overrides';

// ---- Defaults & limits ----

export const HOME_HISTORY_LIMIT_DEFAULT = 20;
export const HOME_JOB_LIMIT_DEFAULT = 20;
export const HOME_LIST_LIMIT_MIN = 1;
export const HOME_LIST_LIMIT_MAX = 200;
export const CUSTOM_BACKGROUND_OPACITY_MIN = 0;
export const CUSTOM_BACKGROUND_OPACITY_MAX = 80;
export const CUSTOM_BACKGROUND_OPACITY_DEFAULT = 35;
export const CUSTOM_BACKGROUND_SURFACE_OPACITY_MIN = 18;
export const CUSTOM_BACKGROUND_SURFACE_OPACITY_MAX = 92;
export const CUSTOM_BACKGROUND_SURFACE_OPACITY_DEFAULT = 33;
export const HIDE_BACKEND_CONSOLE_DEFAULT = true;
export const CACHE_BROWSER_FONT_SIZE_MIN = 11;
export const CACHE_BROWSER_FONT_SIZE_MAX = 20;
export const CACHE_BROWSER_FONT_SIZE_DEFAULT = 14;

// ---- Custom events ----

export const BACKEND_PROFILES_CHANGE_EVENT = 'galtransl:backend-profiles-change';
export const DEFAULT_BACKEND_PROFILE_CHANGE_EVENT = 'galtransl:default-backend-profile-change';
export const HOME_HISTORY_LIMIT_CHANGE_EVENT = 'galtransl:home-history-limit-change';
export const HOME_JOB_LIMIT_CHANGE_EVENT = 'galtransl:home-job-limit-change';
export const THEME_MODE_CHANGE_EVENT = 'galtransl:theme-mode-change';
export const CUSTOM_BACKGROUND_CHANGE_EVENT = 'galtransl:custom-background-change';
export const HIDE_BACKEND_CONSOLE_CHANGE_EVENT = 'galtransl:hide-backend-console-change';
export const CACHE_BROWSER_FONT_SIZE_CHANGE_EVENT = 'galtransl:cache-browser-font-size-change';

// ---- Normalizers ----

function cloneBackendProfile(profile: Record<string, unknown>): Record<string, unknown> {
  return JSON.parse(JSON.stringify(profile ?? {})) as Record<string, unknown>;
}

function normalizeHomeListLimit(value: unknown, fallback: number): number {
  const numeric = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(numeric)) return fallback;
  const integer = Math.trunc(numeric);
  if (integer < HOME_LIST_LIMIT_MIN) return HOME_LIST_LIMIT_MIN;
  if (integer > HOME_LIST_LIMIT_MAX) return HOME_LIST_LIMIT_MAX;
  return integer;
}

function normalizeCustomBackgroundSurfaceOpacity(value: unknown): number {
  const numeric = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(numeric)) return CUSTOM_BACKGROUND_SURFACE_OPACITY_DEFAULT;
  const integer = Math.trunc(numeric);
  if (integer < CUSTOM_BACKGROUND_SURFACE_OPACITY_MIN) return CUSTOM_BACKGROUND_SURFACE_OPACITY_MIN;
  if (integer > CUSTOM_BACKGROUND_SURFACE_OPACITY_MAX) return CUSTOM_BACKGROUND_SURFACE_OPACITY_MAX;
  return integer;
}

function normalizeCustomBackgroundOpacity(value: unknown): number {
  const numeric = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(numeric)) return CUSTOM_BACKGROUND_OPACITY_DEFAULT;
  const integer = Math.trunc(numeric);
  if (integer < CUSTOM_BACKGROUND_OPACITY_MIN) return CUSTOM_BACKGROUND_OPACITY_MIN;
  if (integer > CUSTOM_BACKGROUND_OPACITY_MAX) return CUSTOM_BACKGROUND_OPACITY_MAX;
  return integer;
}

function normalizeThemeMode(value: unknown): ThemeMode {
  if (value === 'light' || value === 'dark' || value === 'system') return value;
  return 'system';
}

function normalizeCacheBrowserFontSize(value: unknown): number {
  const numeric = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(numeric)) return CACHE_BROWSER_FONT_SIZE_DEFAULT;
  const integer = Math.trunc(numeric);
  if (integer < CACHE_BROWSER_FONT_SIZE_MIN) return CACHE_BROWSER_FONT_SIZE_MIN;
  if (integer > CACHE_BROWSER_FONT_SIZE_MAX) return CACHE_BROWSER_FONT_SIZE_MAX;
  return integer;
}

function normalizeHideBackendConsole(value: unknown): boolean {
  if (typeof value === 'boolean') return value;
  if (value === 'true') return true;
  if (value === 'false') return false;
  return HIDE_BACKEND_CONSOLE_DEFAULT;
}

function defaultCustomBackgroundPreference(): CustomBackgroundPreference {
  return {
    imageDataUrl: '',
    imageName: '',
    opacity: CUSTOM_BACKGROUND_OPACITY_DEFAULT,
    surfaceOpacity: CUSTOM_BACKGROUND_SURFACE_OPACITY_DEFAULT,
  };
}

function normalizeCustomBackgroundPreference(value: unknown): CustomBackgroundPreference {
  if (!value || typeof value !== 'object') return defaultCustomBackgroundPreference();
  const preference = value as Partial<CustomBackgroundPreference>;
  return {
    imageDataUrl: typeof preference.imageDataUrl === 'string' ? preference.imageDataUrl : '',
    imageName: typeof preference.imageName === 'string' ? preference.imageName : '',
    opacity: normalizeCustomBackgroundOpacity(preference.opacity),
    surfaceOpacity: normalizeCustomBackgroundSurfaceOpacity(preference.surfaceOpacity),
  };
}

// ---- Backend Profiles storage ----

function readBackendProfilesStorage(): BackendProfilesMap {
  try {
    const raw = localStorage.getItem(BACKEND_PROFILES_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const profiles: BackendProfilesMap = {};
    for (const [name, value] of Object.entries(parsed as Record<string, unknown>)) {
      if (!name.trim()) continue;
      if (value && typeof value === 'object' && !Array.isArray(value)) {
        profiles[name] = cloneBackendProfile(value as Record<string, unknown>);
      }
    }
    return profiles;
  } catch {
    return {};
  }
}

function writeBackendProfilesStorage(profiles: BackendProfilesMap) {
  try {
    localStorage.setItem(BACKEND_PROFILES_STORAGE_KEY, JSON.stringify(profiles));
    window.dispatchEvent(new CustomEvent(BACKEND_PROFILES_CHANGE_EVENT, { detail: Object.keys(profiles) }));
  } catch {
    // ignore storage errors
  }
}

export function getBackendProfile(name: string): Record<string, unknown> | null {
  const trimmedName = name.trim();
  if (!trimmedName) return null;
  const profiles = readBackendProfilesStorage();
  return profiles[trimmedName] ? cloneBackendProfile(profiles[trimmedName]) : null;
}

export function getBackendProfileNames(): string[] {
  return Object.keys(readBackendProfilesStorage());
}

export function resolveSelectedBackendProfile(projectDir: string): { name: string; profile: Record<string, unknown> | null } {
  const name = getSelectedBackendProfile(projectDir);
  if (!name) return { name: '', profile: null };
  return { name, profile: getBackendProfile(name) };
}

export function getSelectedBackendProfileJobPayload(projectDir: string): Pick<SubmitJobPayload, 'backend_profile' | 'backend_profile_data'> {
  const { name, profile } = resolveSelectedBackendProfile(projectDir);
  if (!profile) return {};
  return {
    ...(name ? { backend_profile: name } : {}),
    ...(profile ? { backend_profile_data: profile } : {}),
  };
}

export function getDefaultBackendProfile(): string {
  try {
    return localStorage.getItem(DEFAULT_BACKEND_PROFILE_KEY) || '';
  } catch {
    return '';
  }
}

export function setDefaultBackendProfile(name: string) {
  try {
    if (name) {
      localStorage.setItem(DEFAULT_BACKEND_PROFILE_KEY, name);
    } else {
      localStorage.removeItem(DEFAULT_BACKEND_PROFILE_KEY);
    }
    window.dispatchEvent(new CustomEvent(DEFAULT_BACKEND_PROFILE_CHANGE_EVENT, { detail: name }));
  } catch {
    // ignore storage errors
  }
}

export function getSelectedBackendProfile(projectDir: string): string {
  try {
    const map = JSON.parse(localStorage.getItem(BACKEND_PROFILE_KEY) || '{}');
    if (map[projectDir] !== undefined) return map[projectDir];
    return getDefaultBackendProfile();
  } catch {
    return getDefaultBackendProfile();
  }
}

export function getSelectedBackendProfileDisplay(projectDir: string): string {
  try {
    const map = JSON.parse(localStorage.getItem(BACKEND_PROFILE_KEY) || '{}');
    if (map[projectDir] !== undefined) return map[projectDir];
    return '__default__';
  } catch {
    return '__default__';
  }
}

export function setSelectedBackendProfile(projectDir: string, profileName: string) {
  try {
    const map = JSON.parse(localStorage.getItem(BACKEND_PROFILE_KEY) || '{}');
    if (profileName === '__default__') {
      delete map[projectDir];
    } else {
      map[projectDir] = profileName;
    }
    localStorage.setItem(BACKEND_PROFILE_KEY, JSON.stringify(map));
  } catch {
    // ignore storage errors
  }
}

export function hasExplicitBackendProfile(projectDir: string): boolean {
  try {
    const map = JSON.parse(localStorage.getItem(BACKEND_PROFILE_KEY) || '{}');
    return projectDir in map;
  } catch {
    return false;
  }
}

// ---- Backend Profile CRUD ----

export async function fetchBackendProfiles() {
  return { profiles: readBackendProfilesStorage() };
}

export async function fetchBackendProfile(name: string) {
  const profile = getBackendProfile(name);
  if (!profile) throw new Error(`profile not found: ${name}`);
  return { name, profile };
}

export async function createBackendProfile(name: string, profile: Record<string, unknown>) {
  const trimmedName = name.trim();
  if (!trimmedName) throw new Error('profile name is required');
  const profiles = readBackendProfilesStorage();
  profiles[trimmedName] = cloneBackendProfile(profile);
  writeBackendProfilesStorage(profiles);
  return { success: true, name: trimmedName };
}

export async function updateBackendProfile(name: string, profile: Record<string, unknown>) {
  return createBackendProfile(name, profile);
}

export async function deleteBackendProfile(name: string) {
  const trimmedName = name.trim();
  if (!trimmedName) throw new Error('profile name is required');
  const profiles = readBackendProfilesStorage();
  if (!(trimmedName in profiles)) throw new Error(`profile not found: ${trimmedName}`);
  delete profiles[trimmedName];
  writeBackendProfilesStorage(profiles);
  if (getDefaultBackendProfile() === trimmedName) {
    setDefaultBackendProfile('');
  }
  return { success: true, name: trimmedName };
}

// ---- Translator Template Selection ----

export function getSelectedTranslatorTemplate(projectDir: string): string {
  try {
    const map = JSON.parse(localStorage.getItem(TRANSLATOR_TEMPLATE_KEY) || '{}');
    return typeof map[projectDir] === 'string' ? map[projectDir] : '';
  } catch {
    return '';
  }
}

export function setSelectedTranslatorTemplate(projectDir: string, translatorName: string) {
  try {
    const map = JSON.parse(localStorage.getItem(TRANSLATOR_TEMPLATE_KEY) || '{}');
    map[projectDir] = translatorName;
    localStorage.setItem(TRANSLATOR_TEMPLATE_KEY, JSON.stringify(map));
  } catch {
    // ignore storage errors
  }
}

// ---- Home retention limits ----

export function getHomeHistoryRetentionLimit(): number {
  try {
    const raw = localStorage.getItem(HOME_HISTORY_LIMIT_KEY);
    return normalizeHomeListLimit(raw, HOME_HISTORY_LIMIT_DEFAULT);
  } catch {
    return HOME_HISTORY_LIMIT_DEFAULT;
  }
}

export function setHomeHistoryRetentionLimit(limit: number): number {
  const normalized = normalizeHomeListLimit(limit, HOME_HISTORY_LIMIT_DEFAULT);
  try {
    localStorage.setItem(HOME_HISTORY_LIMIT_KEY, String(normalized));
    window.dispatchEvent(new CustomEvent(HOME_HISTORY_LIMIT_CHANGE_EVENT, { detail: normalized }));
  } catch {
    // ignore storage errors
  }
  return normalized;
}

export function getHomeJobRetentionLimit(): number {
  try {
    const raw = localStorage.getItem(HOME_JOB_LIMIT_KEY);
    return normalizeHomeListLimit(raw, HOME_JOB_LIMIT_DEFAULT);
  } catch {
    return HOME_JOB_LIMIT_DEFAULT;
  }
}

export function setHomeJobRetentionLimit(limit: number): number {
  const normalized = normalizeHomeListLimit(limit, HOME_JOB_LIMIT_DEFAULT);
  try {
    localStorage.setItem(HOME_JOB_LIMIT_KEY, String(normalized));
    window.dispatchEvent(new CustomEvent(HOME_JOB_LIMIT_CHANGE_EVENT, { detail: normalized }));
  } catch {
    // ignore storage errors
  }
  return normalized;
}

// ---- Cache browser font size ----

export function getCacheBrowserFontSizePreference(): number {
  try {
    const raw = localStorage.getItem(CACHE_BROWSER_FONT_SIZE_KEY);
    return normalizeCacheBrowserFontSize(raw);
  } catch {
    return CACHE_BROWSER_FONT_SIZE_DEFAULT;
  }
}

export function setCacheBrowserFontSizePreference(size: number): number {
  const normalized = normalizeCacheBrowserFontSize(size);
  try {
    localStorage.setItem(CACHE_BROWSER_FONT_SIZE_KEY, String(normalized));
    window.dispatchEvent(new CustomEvent(CACHE_BROWSER_FONT_SIZE_CHANGE_EVENT, { detail: normalized }));
  } catch {
    // ignore storage errors
  }
  return normalized;
}

// ---- Console visibility ----

export function getHideBackendConsolePreference(): boolean {
  try {
    const raw = localStorage.getItem(HIDE_BACKEND_CONSOLE_KEY);
    return normalizeHideBackendConsole(raw);
  } catch {
    return HIDE_BACKEND_CONSOLE_DEFAULT;
  }
}

export function setHideBackendConsolePreference(enabled: boolean): boolean {
  const normalized = normalizeHideBackendConsole(enabled);
  try {
    localStorage.setItem(HIDE_BACKEND_CONSOLE_KEY, String(normalized));
    window.dispatchEvent(new CustomEvent(HIDE_BACKEND_CONSOLE_CHANGE_EVENT, { detail: normalized }));
  } catch {
    // ignore storage errors
  }
  return normalized;
}

// ---- Theme mode ----

export function getThemeModePreference(): ThemeMode {
  try {
    const raw = localStorage.getItem(THEME_MODE_KEY);
    return normalizeThemeMode(raw);
  } catch {
    return 'system';
  }
}

export function setThemeModePreference(mode: ThemeMode): ThemeMode {
  const normalized = normalizeThemeMode(mode);
  try {
    localStorage.setItem(THEME_MODE_KEY, normalized);
    window.dispatchEvent(new CustomEvent(THEME_MODE_CHANGE_EVENT, { detail: normalized }));
  } catch {
    // ignore storage errors
  }
  return normalized;
}

// ---- Custom background ----

export function getCustomBackgroundPreference(): CustomBackgroundPreference {
  try {
    const raw = localStorage.getItem(CUSTOM_BACKGROUND_KEY);
    if (!raw) return defaultCustomBackgroundPreference();
    const parsed = JSON.parse(raw) as unknown;
    return normalizeCustomBackgroundPreference(parsed);
  } catch {
    return defaultCustomBackgroundPreference();
  }
}

export function setCustomBackgroundPreference(preference: CustomBackgroundPreference): CustomBackgroundPreference {
  const normalized = normalizeCustomBackgroundPreference(preference);
  localStorage.setItem(CUSTOM_BACKGROUND_KEY, JSON.stringify(normalized));
  window.dispatchEvent(new CustomEvent(CUSTOM_BACKGROUND_CHANGE_EVENT, { detail: normalized }));
  return normalized;
}

export function clearCustomBackgroundPreference(): CustomBackgroundPreference {
  const cleared = defaultCustomBackgroundPreference();
  try {
    localStorage.removeItem(CUSTOM_BACKGROUND_KEY);
    window.dispatchEvent(new CustomEvent(CUSTOM_BACKGROUND_CHANGE_EVENT, { detail: cleared }));
  } catch {
    // ignore storage errors
  }
  return cleared;
}

// ---- Prompt Template Overrides ----

export function loadPromptTemplateOverrides(): Record<string, PromptTemplateOverride> {
  try {
    const raw = localStorage.getItem(PROMPT_TEMPLATES_OVERRIDES_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (typeof parsed === 'object' && parsed !== null) {
      return parsed as Record<string, PromptTemplateOverride>;
    }
    return {};
  } catch {
    return {};
  }
}

export function savePromptTemplateOverrides(overrides: Record<string, PromptTemplateOverride>): void {
  try {
    localStorage.setItem(PROMPT_TEMPLATES_OVERRIDES_KEY, JSON.stringify(overrides));
  } catch {
    // ignore storage errors
  }
}

export function getPromptTemplateOverride(name: string): PromptTemplateOverride | null {
  const overrides = loadPromptTemplateOverrides();
  const override = overrides[name];
  if (override && typeof override === 'object') return override;
  return null;
}

export function setPromptTemplateOverride(name: string, override: PromptTemplateOverride): void {
  const overrides = loadPromptTemplateOverrides();
  overrides[name] = override;
  savePromptTemplateOverrides(overrides);
}

export function deletePromptTemplateOverride(name: string): void {
  const overrides = loadPromptTemplateOverrides();
  delete overrides[name];
  savePromptTemplateOverrides(overrides);
}

export function getPromptTemplateOverridesForJob(translator: string): Record<string, PromptTemplateOverride> {
  const overrides = loadPromptTemplateOverrides();
  const override = overrides[translator];
  if (!override) return {};
  return { [translator]: override };
}

// ---- Re-export AppSettings type for convenience ----
export type { AppSettings };

// ---- Problem detection enabled types ----

const PROBLEM_TYPES_KEY = 'galtransl:enabled-problem-types';

export function getEnabledProblemTypes(): string[] {
  try {
    const raw = localStorage.getItem(PROBLEM_TYPES_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

export function setEnabledProblemTypes(types: string[]): void {
  localStorage.setItem(PROBLEM_TYPES_KEY, JSON.stringify(types));
}
