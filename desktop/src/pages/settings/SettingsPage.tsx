import {
  createSignal,
  onMount,
  Show,
} from "solid-js";
import { toast } from "../../stores/toastStore";
import { navigateTo } from "../../stores/appStore";
import {
  getThemeModePreference,
  setThemeModePreference,
  getHideBackendConsolePreference,
  setHideBackendConsolePreference,
  getCustomBackgroundPreference,
  setCustomBackgroundPreference,
  clearCustomBackgroundPreference,
  getCacheBrowserFontSizePreference,
  setCacheBrowserFontSizePreference,
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
} from "../../lib/api/preferences";
import { fetchVersion, fetchVersionCheck } from "../../lib/api/general";
import type { ThemeMode, CustomBackgroundPreference } from "../../lib/api/types";
import { compressImageToDataUrl } from "./imageCompress";

const PROJECT_HOMEPAGE = "https://github.com/GalTransl/GalTransl";
const PROJECT_AUTHOR = "xd2333";

export function SettingsPage() {
  // ── 外观 ──
  const [themeMode, setTheme] = createSignal<ThemeMode>(getThemeModePreference());
  const [hideConsole, setHideConsole] = createSignal(getHideBackendConsolePreference());
  const [bgDataUrl, setBgDataUrl] = createSignal(getCustomBackgroundPreference().imageDataUrl);
  const [bgName, setBgName] = createSignal(getCustomBackgroundPreference().imageName);
  const [bgOpacity, setBgOpacity] = createSignal(String(getCustomBackgroundPreference().opacity));
  const [bgSurfaceOpacity, setBgSurfaceOpacity] = createSignal(
    String(getCustomBackgroundPreference().surfaceOpacity)
  );
  const [fontSize, setFontSize] = createSignal(String(getCacheBrowserFontSizePreference()));
  const [bgBusy, setBgBusy] = createSignal(false);
  const [bgError, setBgError] = createSignal("");

  // ── 首页记忆 ──
  const [historyLimit, setHistoryLimit] = createSignal(String(getHomeHistoryRetentionLimit()));
  const [jobLimit, setJobLimit] = createSignal(String(getHomeJobRetentionLimit()));

  // ── 关于 ──
  const [coreVersion, setCoreVersion] = createSignal("");
  const [latestVersion, setLatestVersion] = createSignal("");
  const [updateAvail, setUpdateAvail] = createSignal(false);
  const [checkingVer, setCheckingVer] = createSignal(true);
  const [verError, setVerError] = createSignal("");

  onMount(() => {
    fetchVersion()
      .then((v) => setCoreVersion(v))
      .catch(() => {});

    fetchVersionCheck()
      .then((res: any) => {
        setCoreVersion(res.version);
        setLatestVersion(res.latest_version);
        setUpdateAvail(res.update_available);
      })
      .catch((e: Error) => setVerError(e.message))
      .finally(() => setCheckingVer(false));
  });

  // ── 处理函数 ──

  function applyTheme(mode: ThemeMode) {
    const next = setThemeModePreference(mode);
    setTheme(next);
    // 切换 data-theme
    document.documentElement.setAttribute("data-theme", next === "system" ? "" : next);
  }

  function applyHideConsole(enabled: boolean) {
    setHideConsole(setHideBackendConsolePreference(enabled));
  }

  function applyFontSize(raw: string) {
    setFontSize(String(setCacheBrowserFontSizePreference(Number(raw) || NaN)));
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
      } catch (err: any) {
        const isQuota = err instanceof DOMException && (err.name === "QuotaExceededError" || (err as any).code === 22);
        setBgError(isQuota ? "图片过大，请选择更小的图片" : err.message || "保存背景失败");
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

  return (
    <div class="page page-settings">
      <h2 class="page-title">设置</h2>
      <p class="page-description">管理应用配置和后端连接。</p>

      <div class="settings-content">
        {/* ── 项目设置 ── */}
        <section class="settings-section">
          <div class="settings-section-header">
            <h3>项目设置</h3>
            <p>编辑当前打开项目的翻译配置参数（config.yaml）。</p>
          </div>
          <div class="settings-field" style="cursor:pointer; border-bottom:none" onClick={() => navigateTo("project-config")}>
            <span class="settings-label">编辑项目配置</span>
            <span class="settings-about-value settings-about-link">后端、插件、字典等参数 →</span>
          </div>
        </section>

        {/* ── 外观 ── */}
        <section class="settings-section">
          <div class="settings-section-header">
            <h3>外观</h3>
            <p>设置界面主题风格，以及半透明的全局自定义背景。</p>
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
            <span class="settings-label">自定义背景</span>
            <div class="settings-bg-row">
              <span class="settings-bg-name" title={bgName() || "尚未选择图片"}>
                {bgName() || "尚未选择图片"}
              </span>
              <button class="btn btn--sm" onClick={handleBgPick} disabled={bgBusy()}>
                {bgBusy() ? "处理中…" : bgDataUrl() ? "更换图片" : "选择图片"}
              </button>
              <button class="btn btn--sm" onClick={handleBgClear} disabled={!bgDataUrl() || bgBusy()}>
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

          <div class="settings-hint">
            {bgDataUrl() ? "已启用自定义背景。" : "未设置自定义背景。"}主题、背景和容器透明度设置会即时生效。
          </div>
        </section>

        {/* ── 首页记忆 ── */}
        <section class="settings-section">
          <div class="settings-section-header">
            <h3>首页记忆保留</h3>
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

        {/* ── 配置管理 ── */}
        <section class="settings-section">
          <div class="settings-section-header">
            <h3>配置管理</h3>
            <p>管理后端连接配置、翻译插件、以及各翻译引擎的提示词模板。</p>
          </div>

          <div class="settings-field" style="cursor:pointer" onClick={() => navigateTo("backend-profiles")}>
            <span class="settings-label">后端配置</span>
            <span class="settings-about-value settings-about-link">管理 API 地址与模型 →</span>
          </div>
          <div class="settings-field" style="cursor:pointer" onClick={() => navigateTo("plugins")}>
            <span class="settings-label">插件管理</span>
            <span class="settings-about-value settings-about-link">查看已安装插件 →</span>
          </div>
          <div class="settings-field" style="cursor:pointer; border-bottom:none" onClick={() => navigateTo("prompt-templates")}>
            <span class="settings-label">提示词模板</span>
            <span class="settings-about-value settings-about-link">编辑默认提示词 →</span>
          </div>
        </section>

        {/* ── 关于 ── */}
        <section class="settings-section">
          <div class="settings-section-header">
            <h3>关于</h3>
            <p>查看项目基础信息与版本更新状态。</p>
          </div>

          <div class="settings-about-list">
            <div class="settings-about-row">
              <span class="settings-about-label">项目主页</span>
              <a class="settings-about-value settings-about-link" href={PROJECT_HOMEPAGE} target="_blank" rel="noreferrer noopener">
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
              <span class="settings-about-value">{PROJECT_AUTHOR}</span>
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
