import { createSignal, createEffect, onMount, Show } from "solid-js";
import { appState, setAppState, navigateTo, getActiveConfigFileName } from "../../stores/appStore";
import {
  getThemeModePreference,
  setThemeModePreference,
  getHideBackendConsolePreference,
  setHideBackendConsolePreference,
  getShowShortcutButtonsPreference,
  setShowShortcutButtonsPreference,
  getCustomBackgroundPreference,
  setCustomBackgroundPreference,
  clearCustomBackgroundPreference,
  getCacheBrowserFontSizePreference,
  setCacheBrowserFontSizePreference,
  getCachePageSizePreference,
  setCachePageSizePreference,
  getHomeHistoryRetentionLimit,
  setHomeHistoryRetentionLimit,
  getHomeJobRetentionLimit,
  setHomeJobRetentionLimit,
  CUSTOM_BACKGROUND_OPACITY_MIN,
  CUSTOM_BACKGROUND_OPACITY_MAX,
  CUSTOM_BACKGROUND_SURFACE_OPACITY_MIN,
  CUSTOM_BACKGROUND_SURFACE_OPACITY_MAX,
  HOME_LIST_LIMIT_MIN,
  HOME_LIST_LIMIT_MAX,
  CACHE_BROWSER_FONT_SIZE_MIN,
  CACHE_BROWSER_FONT_SIZE_MAX,
  CACHE_PAGE_SIZE_MIN,
  CACHE_PAGE_SIZE_MAX,
} from "../../lib/api/preferences";
import { fetchVersion, fetchVersionCheck, fetchAppSettings, updateAppSettings } from "../../lib/api/general";
import { APP_SETTINGS_TAXONOMY } from "../../lib/settings-taxonomy";
import { fetchProjectConfig, updateProjectConfig } from "../../lib/api/project";
import type { ThemeMode } from "../../lib/api/types";
import { applyThemePreference } from "../../lib/theme";
import { compressImageToDataUrl } from "./imageCompress";
import { getErrorMessage } from "../../lib/errors";

const PROJECT_HOMEPAGE = "https://github.com/awei808/my-GalTransL";

export function SettingsPage() {
  // ── 外观 ──
  const [themeMode, setTheme] = createSignal<ThemeMode>(getThemeModePreference());
  const [hideConsole, setHideConsole] = createSignal(getHideBackendConsolePreference());
  const [showShortcutButtons, setShowShortcutButtons] = createSignal(getShowShortcutButtonsPreference());
  const [bgDataUrl, setBgDataUrl] = createSignal(getCustomBackgroundPreference().imageDataUrl);
  const [bgName, setBgName] = createSignal(getCustomBackgroundPreference().imageName);
  const [bgOpacity, setBgOpacity] = createSignal(String(getCustomBackgroundPreference().opacity));
  const [bgSurfaceOpacity, setBgSurfaceOpacity] = createSignal(
    String(getCustomBackgroundPreference().surfaceOpacity),
  );
  const [fontSize, setFontSize] = createSignal(String(getCacheBrowserFontSizePreference()));
  const [pageSizeLimit, setPageSizeLimit] = createSignal(String(getCachePageSizePreference()));
  const [bgBusy, setBgBusy] = createSignal(false);
  const [bgError, setBgError] = createSignal("");

  // ── 首页记忆 ──
  const [historyLimit, setHistoryLimit] = createSignal(String(getHomeHistoryRetentionLimit()));
  const [jobLimit, setJobLimit] = createSignal(String(getHomeJobRetentionLimit()));

  // ── 关于 ──
  const [coreVersion, setCoreVersion] = createSignal("");
  const [coreAuthor, setCoreAuthor] = createSignal("");
  const [latestVersion, setLatestVersion] = createSignal("");
  const [updateAvail, setUpdateAvail] = createSignal(false);
  const [checkingVer, setCheckingVer] = createSignal(true);
  const [verError, setVerError] = createSignal("");

  // ── 日志开关 ──
  // GalTransl.log：写在当前打开项目的 config.common.saveLog（项目级）
  const [saveGalTranslLog, setSaveGalTranslLog] = createSignal(false);
  const [galLogLoading, setGalLogLoading] = createSignal(false);
  const [galLogError, setGalLogError] = createSignal("");
  // 日志级别：写在当前打开项目的 config.common.loggingLevel（项目级）
  const [loggingLevel, setLoggingLevel] = createSignal("info");
  const [logLevelLoading, setLogLevelLoading] = createSignal(false);
  const [logLevelError, setLogLevelError] = createSignal("");
  // api_calls.log：写在后端全局 AppSettings.writeApiCallLog（默认不写，与后端一致）
  const [writeApiCallLog, setWriteApiCallLog] = createSignal(false);
  const [apiLogLoading, setApiLogLoading] = createSignal(false);
  const [apiLogSaving, setApiLogSaving] = createSignal(false);
  const [apiLogError, setApiLogError] = createSignal("");

  onMount(() => {
    fetchVersion()
      .then((v) => {
        setCoreVersion(v.version);
        setCoreAuthor(v.author ?? "awei808");
      })
      .catch(() => {});

    fetchVersionCheck()
      .then((res) => {
        setCoreVersion(res.version);
        setLatestVersion(res.latest_version ?? "");
        setUpdateAvail(res.update_available);
      })
      .catch((e: Error) => setVerError(e.message))
      .finally(() => setCheckingVer(false));

    // 加载后端全局日志开关（api_calls.log）
    setApiLogLoading(true);
    fetchAppSettings()
      .then((s) => setWriteApiCallLog(s.writeApiCallLog ?? false))
      .catch(() => {})
      .finally(() => setApiLogLoading(false));
  });

  // ── 处理函数 ──

  function applyTheme(mode: ThemeMode) {
    const next = setThemeModePreference(mode);
    setTheme(next);
    // 切换 data-theme（system 模式由 theme 模块监听系统偏好）
    applyThemePreference();
  }

  function applyHideConsole(enabled: boolean) {
    setHideConsole(setHideBackendConsolePreference(enabled));
  }

  function applyShowShortcutButtons(enabled: boolean) {
    setShowShortcutButtons(setShowShortcutButtonsPreference(enabled));
  }

  function applyFontSize(raw: string) {
    setFontSize(String(setCacheBrowserFontSizePreference(Number(raw) || NaN)));
  }

  function applyPageSizeLimit(raw: string) {
    // 注意：不能写 Number(raw) || NaN，否则合法的 0 会被当成 falsy 转成 NaN，
    // 进而被 normalizeCachePageSize 回退为默认值 2000，导致"设为 0 不生效"。
    setPageSizeLimit(String(setCachePageSizePreference(raw.trim() === "" ? NaN : Number(raw))));
  }

  function applyHistoryLimit(raw: string) {
    setHistoryLimit(String(setHomeHistoryRetentionLimit(Number(raw) || NaN)));
  }

  function applyJobLimit(raw: string) {
    setJobLimit(String(setHomeJobRetentionLimit(Number(raw) || NaN)));
  }

  function applyBgOpacity(raw: string) {
    const val = Number(raw) || NaN;
    const cur = getCustomBackgroundPreference();
    try {
      const next = setCustomBackgroundPreference({
        ...cur,
        opacity: val,
      });
      setBgOpacity(String(next.opacity));
    } catch {
      setBgError("保存背景设置失败");
    }
  }

  function applyBgSurfaceOpacity(raw: string) {
    const cur = getCustomBackgroundPreference();
    const val = Number(raw) || NaN;
    try {
      const next = setCustomBackgroundPreference({
        ...cur,
        surfaceOpacity: val,
      });
      setBgSurfaceOpacity(String(next.surfaceOpacity));
    } catch {
      setBgError("保存背景设置失败");
    }
  }

  async function handleBgPick() {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      if (!file.type.startsWith("image/")) {
        setBgError("请选择图片文件");
        return;
      }
      setBgBusy(true);
      setBgError("");
      try {
        const dataUrl = await compressImageToDataUrl(file);
        const cur = getCustomBackgroundPreference();
        const next = setCustomBackgroundPreference({
          imageDataUrl: dataUrl,
          imageName: file.name,
          opacity: cur.opacity,
          surfaceOpacity: cur.surfaceOpacity,
        });
        setBgDataUrl(next.imageDataUrl);
        setBgName(next.imageName);
        setBgOpacity(String(next.opacity));
        setBgSurfaceOpacity(String(next.surfaceOpacity));
      } catch (err) {
        const isQuota = err instanceof DOMException && err.name === "QuotaExceededError";
        setBgError(isQuota ? "图片过大，请选择更小的图片" : getErrorMessage(err) || "保存背景失败");
      } finally {
        setBgBusy(false);
      }
    };
    input.click();
  }

  function handleBgClear() {
    const next = clearCustomBackgroundPreference();
    setBgDataUrl(next.imageDataUrl);
    setBgName(next.imageName);
    setBgOpacity(String(next.opacity));
    setBgSurfaceOpacity(String(next.surfaceOpacity));
    setBgError("");
  }

  // 切换当前项目/配置名后，重新从项目 YAML 读取 GalTransl.log 开关
  createEffect(() => {
    const pid = appState.activeProjectId;
    if (!pid || appState.configNameDetecting) return;
    void loadGalTranslLogState(pid, getActiveConfigFileName());
  });

  // 处理 ActivityBar 快捷按钮发起的滚动定位（异步渲染完成后执行并清除标记）
  createEffect(() => {
    const target = appState.settingsScrollTarget;
    if (!target) return;
    requestAnimationFrame(() => {
      const el = document.getElementById(target);
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "start" });
        setAppState("settingsScrollTarget", null);
      }
    });
  });

  async function loadGalTranslLogState(projectId: string, configFileName: string) {
    setGalLogLoading(true);
    setGalLogError("");
    try {
      const res = await fetchProjectConfig(projectId, configFileName);
      const config = (res.config ?? {}) as Record<string, unknown>;
      const common = (config["common"] as Record<string, unknown> | undefined) ?? {};
      setSaveGalTranslLog(Boolean(common["saveLog"] ?? false));
      setLoggingLevel(String(common["loggingLevel"] ?? "info"));
    } catch (e) {
      setGalLogError(`读取 GalTransl.log 设置失败：${getErrorMessage(e)}`);
    } finally {
      setGalLogLoading(false);
    }
  }

  async function applyGalTranslLog(enabled: boolean) {
    const pid = appState.activeProjectId;
    if (!pid) return;
    const configFileName = getActiveConfigFileName();
    setGalLogError("");
    try {
      // 读最新配置再合并，避免覆盖其他字段
      const res = await fetchProjectConfig(pid, configFileName);
      const config = { ...(res.config ?? {}) } as Record<string, unknown>;
      const common = { ...((config["common"] as Record<string, unknown> | undefined) ?? {}) };
      common["saveLog"] = enabled;
      config["common"] = common;
      await updateProjectConfig(pid, { config, config_file_name: configFileName });
      setSaveGalTranslLog(enabled);
    } catch (e) {
      setGalLogError(`保存 GalTransl.log 设置失败：${getErrorMessage(e)}`);
    }
  }

  async function applyLoggingLevel(level: string) {
    const pid = appState.activeProjectId;
    if (!pid) return;
    const configFileName = getActiveConfigFileName();
    setLogLevelError("");
    setLogLevelLoading(true);
    try {
      // 读最新配置再合并，避免覆盖其他字段
      const res = await fetchProjectConfig(pid, configFileName);
      const config = { ...(res.config ?? {}) } as Record<string, unknown>;
      const common = { ...((config["common"] as Record<string, unknown> | undefined) ?? {}) };
      common["loggingLevel"] = level;
      config["common"] = common;
      await updateProjectConfig(pid, { config, config_file_name: configFileName });
      setLoggingLevel(level);
    } catch (e) {
      setLogLevelError(`保存日志级别失败：${getErrorMessage(e)}`);
    } finally {
      setLogLevelLoading(false);
    }
  }

  async function applyApiCallLog(enabled: boolean) {
    setApiLogSaving(true);
    setApiLogError("");
    try {
      const cur = await fetchAppSettings();
      const next = { ...cur, writeApiCallLog: enabled };
      await updateAppSettings(next);
      setWriteApiCallLog(enabled);
    } catch (e) {
      setApiLogError(`保存 api_calls.log 设置失败：${getErrorMessage(e)}`);
    } finally {
      setApiLogSaving(false);
    }
  }

  return (
    <div class="page page-settings">
      <h2 class="page-title">设置</h2>
      <p class="page-description">管理应用配置、后端连接与日志。</p>

      <div class="settings-content">
        {/* ── 1. AI API 调用接口相关 ── */}
        <section class="settings-section">
          <div class="settings-section-header">
            <h3>{APP_SETTINGS_TAXONOMY.find((s) => s.id === "ai-api")!.title}</h3>
            <p>{APP_SETTINGS_TAXONOMY.find((s) => s.id === "ai-api")!.desc}</p>
          </div>

          <div
            class="settings-field"
            style="cursor:pointer"
            onClick={() => navigateTo("backend-profiles")}
          >
            <span class="settings-label">后端配置</span>
            <span class="settings-about-value settings-about-link">管理 API 地址与模型 →</span>
          </div>
          <div class="settings-field" style="cursor:pointer" onClick={() => navigateTo("plugins")}>
            <span class="settings-label">插件管理</span>
            <span class="settings-about-value settings-about-link">查看已安装插件 →</span>
          </div>
          <div
            class="settings-field"
            style="cursor:pointer; border-bottom:none"
            onClick={() => navigateTo("prompt-templates")}
          >
            <span class="settings-label">提示词模板</span>
            <span class="settings-about-value settings-about-link">编辑默认提示词 →</span>
          </div>
        </section>

        {/* ── 2. 后端服务配置 ── */}
        <section class="settings-section" id="settings-section-backend">
          <div class="settings-section-header">
            <h3>{APP_SETTINGS_TAXONOMY.find((s) => s.id === "backend")!.title}</h3>
            <p>{APP_SETTINGS_TAXONOMY.find((s) => s.id === "backend")!.desc}</p>
          </div>
          <div
            class="settings-field"
            classList={{ "settings-field--disabled": !appState.activeProjectId }}
            style="cursor:pointer; border-bottom:none"
            onClick={() => {
              if (appState.activeProjectId) navigateTo("project-config");
            }}
          >
            <span class="settings-label">编辑后端与问题修复配置</span>
            <span class="settings-about-value settings-about-link">翻译/元数据/修复后端 →</span>
          </div>
        </section>

        {/* ── 3. 前端显示相关 ── */}
        <section class="settings-section">
          <div class="settings-section-header">
            <h3>{APP_SETTINGS_TAXONOMY.find((s) => s.id === "display")!.title}</h3>
            <p>{APP_SETTINGS_TAXONOMY.find((s) => s.id === "display")!.desc}</p>
          </div>

          <div class="settings-field">
            <span class="settings-label">主题模式</span>
            <select
              class="field__input settings-control"
              value={themeMode()}
              onChange={(e) => applyTheme(e.currentTarget.value as ThemeMode)}
            >
              <option value="light">浅色</option>
              <option value="dark">深色</option>
              <option value="system">跟随系统</option>
            </select>
          </div>

          <div class="settings-field">
            <span class="settings-label">隐藏服务端控制台</span>
            <label class="settings-toggle">
              <input
                type="checkbox"
                checked={hideConsole()}
                onChange={(e) => applyHideConsole(e.currentTarget.checked)}
              />
              <span class="settings-toggle-knob" />
            </label>
          </div>

          <div class="settings-field">
            <span class="settings-label">显示左侧快捷按钮</span>
            <label class="settings-toggle">
              <input
                type="checkbox"
                checked={showShortcutButtons()}
                onChange={(e) => applyShowShortcutButtons(e.currentTarget.checked)}
              />
              <span class="settings-toggle-knob" />
            </label>
            <p class="settings-hint">控制「后端设置（项目设置）」「问题检测项」两个快捷按钮的显示。</p>
          </div>

          <div class="settings-field">
            <span class="settings-label">自定义背景</span>
            <div class="settings-bg-row">
              <span class="settings-bg-name" title={bgName() || "尚未选择图片"}>
                {bgName() || "尚未选择图片"}
              </span>
              <button class="btn btn--sm" onClick={handleBgPick} disabled={bgBusy()}>
                {bgBusy() ? "处理中…" : bgDataUrl() ? "更换图片" : "选择图片"}
              </button>
              <button
                class="btn btn--sm"
                onClick={handleBgClear}
                disabled={!bgDataUrl() || bgBusy()}
              >
                清除
              </button>
            </div>
            <Show when={bgError()}>
              <div class="settings-error">{bgError()}</div>
            </Show>
          </div>

          <div class="settings-field">
            <span class="settings-label">背景透明度</span>
            <div class="settings-opacity">
              <input
                type="range"
                min={CUSTOM_BACKGROUND_OPACITY_MIN}
                max={CUSTOM_BACKGROUND_OPACITY_MAX}
                value={bgOpacity()}
                onInput={(e) => {
                  setBgOpacity(e.currentTarget.value);
                  applyBgOpacity(e.currentTarget.value);
                }}
              />
              <input
                type="number"
                class="field__input settings-number-sm"
                min={CUSTOM_BACKGROUND_OPACITY_MIN}
                max={CUSTOM_BACKGROUND_OPACITY_MAX}
                value={bgOpacity()}
                onInput={(e) => setBgOpacity(e.currentTarget.value)}
                onBlur={() => applyBgOpacity(bgOpacity())}
                onKeyDown={(e) => e.key === "Enter" && (e.currentTarget as HTMLElement).blur()}
              />
            </div>
          </div>

          <div class="settings-field">
            <span class="settings-label">容器不透明度</span>
            <div class="settings-opacity">
              <input
                type="range"
                min={CUSTOM_BACKGROUND_SURFACE_OPACITY_MIN}
                max={CUSTOM_BACKGROUND_SURFACE_OPACITY_MAX}
                value={bgSurfaceOpacity()}
                onInput={(e) => {
                  setBgSurfaceOpacity(e.currentTarget.value);
                  applyBgSurfaceOpacity(e.currentTarget.value);
                }}
              />
              <input
                type="number"
                class="field__input settings-number-sm"
                min={CUSTOM_BACKGROUND_SURFACE_OPACITY_MIN}
                max={CUSTOM_BACKGROUND_SURFACE_OPACITY_MAX}
                value={bgSurfaceOpacity()}
                onInput={(e) => setBgSurfaceOpacity(e.currentTarget.value)}
                onBlur={() => applyBgSurfaceOpacity(bgSurfaceOpacity())}
                onKeyDown={(e) => e.key === "Enter" && (e.currentTarget as HTMLElement).blur()}
              />
            </div>
          </div>

          <div class="settings-field">
            <span class="settings-label">缓存与问题字号</span>
            <div class="settings-opacity">
              <input
                type="range"
                min={CACHE_BROWSER_FONT_SIZE_MIN}
                max={CACHE_BROWSER_FONT_SIZE_MAX}
                value={fontSize()}
                onInput={(e) => {
                  setFontSize(e.currentTarget.value);
                  applyFontSize(e.currentTarget.value);
                }}
              />
              <input
                type="number"
                class="field__input settings-number-sm"
                min={CACHE_BROWSER_FONT_SIZE_MIN}
                max={CACHE_BROWSER_FONT_SIZE_MAX}
                value={fontSize()}
                onInput={(e) => setFontSize(e.currentTarget.value)}
                onBlur={() => applyFontSize(fontSize())}
                onKeyDown={(e) => e.key === "Enter" && (e.currentTarget as HTMLElement).blur()}
              />
            </div>
          </div>

          <div class="settings-field">
            <span class="settings-label">每页条目显示数量</span>
            <input
              type="number"
              class="field__input settings-control settings-number"
              min={CACHE_PAGE_SIZE_MIN}
              max={CACHE_PAGE_SIZE_MAX}
              value={pageSizeLimit()}
              onInput={(e) => setPageSizeLimit(e.currentTarget.value)}
              onBlur={() => applyPageSizeLimit(pageSizeLimit())}
              onKeyDown={(e) => e.key === "Enter" && (e.currentTarget as HTMLElement).blur()}
            />
            <p class="settings-hint">0 = 不分页，一次性显示全部条目；默认 2000。</p>
          </div>

          <div class="settings-hint">
            {bgDataUrl() ? "已启用自定义背景。" : "未设置自定义背景。"}
            主题、背景和容器透明度设置会即时生效。
          </div>

          {/* 首页记忆保留（并入前端显示相关） */}
          <div class="settings-subheader">
            <h4>首页记忆保留</h4>
            <p>控制首页历史项目与翻译任务列表保留条数。</p>
          </div>

          <div class="settings-field">
            <span class="settings-label">历史项目保留条数</span>
            <input
              type="number"
              class="field__input settings-control settings-number"
              min={HOME_LIST_LIMIT_MIN}
              max={HOME_LIST_LIMIT_MAX}
              value={historyLimit()}
              onInput={(e) => setHistoryLimit(e.currentTarget.value)}
              onBlur={() => applyHistoryLimit(historyLimit())}
              onKeyDown={(e) => e.key === "Enter" && (e.currentTarget as HTMLElement).blur()}
            />
          </div>

          <div class="settings-field">
            <span class="settings-label">翻译任务保留条数</span>
            <input
              type="number"
              class="field__input settings-control settings-number"
              min={HOME_LIST_LIMIT_MIN}
              max={HOME_LIST_LIMIT_MAX}
              value={jobLimit()}
              onInput={(e) => setJobLimit(e.currentTarget.value)}
              onBlur={() => applyJobLimit(jobLimit())}
              onKeyDown={(e) => e.key === "Enter" && (e.currentTarget as HTMLElement).blur()}
            />
          </div>

          <div class="settings-hint">
            取值范围 {HOME_LIST_LIMIT_MIN}-{HOME_LIST_LIMIT_MAX}。超出范围会自动修正。
          </div>
        </section>

        {/* ── 4. 日志相关 ── */}
        <section class="settings-section">
          <div class="settings-section-header">
            <h3>{APP_SETTINGS_TAXONOMY.find((s) => s.id === "log")!.title}</h3>
            <p>{APP_SETTINGS_TAXONOMY.find((s) => s.id === "log")!.desc}</p>
          </div>

          <div
            class="settings-field"
            classList={{ "settings-field--disabled": !appState.activeProjectId }}
          >
            <span class="settings-label">
              GalTransl.log 写入文件
              <span class="settings-hint-inline">（写入当前项目 config.common.saveLog）</span>
            </span>
            <label class="settings-toggle">
              <input
                type="checkbox"
                checked={saveGalTranslLog()}
                disabled={!appState.activeProjectId || galLogLoading()}
                onChange={(e) => void applyGalTranslLog(e.currentTarget.checked)}
              />
              <span class="settings-toggle-knob" />
            </label>
          </div>
          <Show when={galLogError()}>
            <div class="settings-error">{galLogError()}</div>
          </Show>
          <Show when={!appState.activeProjectId}>
            <div class="settings-hint">请先打开一个项目，才能设置 GalTransl.log 写入。</div>
          </Show>

          <div
            class="settings-field"
            classList={{ "settings-field--disabled": !appState.activeProjectId }}
          >
            <span class="settings-label">
              日志级别
              <span class="settings-hint-inline">（写入当前项目 config.common.loggingLevel）</span>
            </span>
            <select
              class="settings-select"
              value={loggingLevel()}
              disabled={!appState.activeProjectId || logLevelLoading()}
              onChange={(e) => void applyLoggingLevel(e.currentTarget.value)}
            >
              <option value="debug">debug（最详细）</option>
              <option value="info">info（常规）</option>
              <option value="warning">warning（仅警告）</option>
            </select>
          </div>
          <Show when={logLevelError()}>
            <div class="settings-error">{logLevelError()}</div>
          </Show>
          <Show when={!appState.activeProjectId}>
            <div class="settings-hint">请先打开一个项目，才能设置日志级别。</div>
          </Show>

          <div class="settings-field">
            <span class="settings-label">
              api_calls.log 写入文件
              <span class="settings-hint-inline">（后端全局设置，保存后立即生效于后续翻译请求；已运行的翻译任务需重启才完全生效）</span>
            </span>
            <label class="settings-toggle">
              <input
                type="checkbox"
                checked={writeApiCallLog()}
                disabled={apiLogLoading() || apiLogSaving()}
                onChange={(e) => void applyApiCallLog(e.currentTarget.checked)}
              />
              <span class="settings-toggle-knob" />
            </label>
          </div>
          <Show when={apiLogError()}>
            <div class="settings-error">{apiLogError()}</div>
          </Show>

          <div class="settings-field">
            <span class="settings-label">
              error.log 写入文件
              <span class="settings-hint-inline">（始终写入，不可关闭）</span>
            </span>
            <label class="settings-toggle">
              <input type="checkbox" checked={true} disabled={true} readOnly={true} />
              <span class="settings-toggle-knob" />
            </label>
          </div>
        </section>

        {/* ── 5. 缓存 / 校对 / 字典相关 ── */}
        <section class="settings-section">
          <div class="settings-section-header">
            <h3>{APP_SETTINGS_TAXONOMY.find((s) => s.id === "cache")!.title}</h3>
            <p>{APP_SETTINGS_TAXONOMY.find((s) => s.id === "cache")!.desc}</p>
          </div>
          <div
            class="settings-field"
            classList={{ "settings-field--disabled": !appState.activeProjectId }}
            style="cursor:pointer; border-bottom:none"
            onClick={() => {
              if (appState.activeProjectId) navigateTo("project-config");
            }}
          >
            <span class="settings-label">编辑缓存 / 校对 / 字典</span>
            <span class="settings-about-value settings-about-link">缓存、问题检测、字典 →</span>
          </div>
        </section>

        {/* ── 关于 ── */}
        <section class="settings-section">
          <div class="settings-section-header">
            <h3>{APP_SETTINGS_TAXONOMY.find((s) => s.id === "about")!.title}</h3>
            <p>{APP_SETTINGS_TAXONOMY.find((s) => s.id === "about")!.desc}</p>
          </div>

          <div class="settings-about-list">
            <div class="settings-about-row">
              <span class="settings-about-label">项目主页</span>
              <a
                class="settings-about-value settings-about-link"
                href={PROJECT_HOMEPAGE}
                target="_blank"
                rel="noreferrer noopener"
              >
                {PROJECT_HOMEPAGE}
              </a>
            </div>
            <div class="settings-about-row">
              <span class="settings-about-label">当前版本</span>
              <span class="settings-about-value">{coreVersion() || "—"}</span>
            </div>
            <div class="settings-about-row">
              <span class="settings-about-label">更新状态</span>
              <span class="settings-about-value">
                {checkingVer()
                  ? "检查中…"
                  : updateAvail() && latestVersion()
                    ? `发现新版本 v${latestVersion()}`
                    : "已是最新版本"}
              </span>
            </div>
            <Show when={updateAvail() && latestVersion()}>
              <div class="settings-about-row">
                <span class="settings-about-label">更新下载</span>
                <a
                  class="settings-about-value settings-about-link"
                  href={`${PROJECT_HOMEPAGE}/releases/latest`}
                  target="_blank"
                  rel="noreferrer noopener"
                >
                  前往最新发布页
                </a>
              </div>
            </Show>
            <div class="settings-about-row">
              <span class="settings-about-label">作者</span>
              <span class="settings-about-value">{coreAuthor() || "awei808"}</span>
            </div>
          </div>

          <Show when={verError()}>
            <div class="settings-hint">更新检查失败: {verError()}</div>
          </Show>
        </section>
      </div>
    </div>
  );
}
