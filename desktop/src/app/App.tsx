import { Suspense, lazy, useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react';
import { HashRouter, Routes, Route, useNavigate, useLocation } from 'react-router-dom';
import {
  CUSTOM_BACKGROUND_CHANGE_EVENT,
  THEME_MODE_CHANGE_EVENT,
  type CustomBackgroundPreference,
  getCustomBackgroundPreference,
  getThemeModePreference,
} from '../lib/api';
import { Sidebar } from '../components/Sidebar';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { ConnectionProvider } from '../features/connection/ConnectionContext';
import { HomePage } from '../pages/HomePage';
import { addProjectToHistory } from '../pages/home/homeUtils';
import { useProjectStore } from '../stores';

const ProjectLayout = lazy(async () => {
  const mod = await import('../components/ProjectLayout');
  return { default: mod.ProjectLayout };
});

const SettingsPage = lazy(async () => {
  const mod = await import('../pages/SettingsPage');
  return { default: mod.SettingsPage };
});

const CommonDictionaryPage = lazy(async () => {
  const mod = await import('../pages/CommonDictionaryPage');
  return { default: mod.CommonDictionaryPage };
});

const NewProjectWizard = lazy(async () => {
  const mod = await import('../pages/NewProjectWizard');
  return { default: mod.NewProjectWizard };
});

function RouteLoadingFallback() {
  return <div className="inline-feedback">页面加载中…</div>;
}

export function App() {
  useEffect(() => {
    const root = document.documentElement;
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');

    const applyTheme = () => {
      const mode = getThemeModePreference();
      const resolved = mode === 'system' ? (mediaQuery.matches ? 'dark' : 'light') : mode;
      root.dataset.themeMode = mode;
      root.dataset.theme = resolved;
    };

    const handleThemeModeChange = () => {
      applyTheme();
    };

    const handleSystemSchemeChange = () => {
      if (getThemeModePreference() === 'system') {
        applyTheme();
      }
    };

    applyTheme();
    window.addEventListener(THEME_MODE_CHANGE_EVENT, handleThemeModeChange as EventListener);
    mediaQuery.addEventListener('change', handleSystemSchemeChange);

    return () => {
      window.removeEventListener(THEME_MODE_CHANGE_EVENT, handleThemeModeChange as EventListener);
      mediaQuery.removeEventListener('change', handleSystemSchemeChange);
    };
  }, []);

  return (
    <HashRouter>
      <ConnectionProvider>
        <AppInner />
        <ConfirmDialog />
      </ConnectionProvider>
    </HashRouter>
  );
}

function AppInner() {
  const navigate = useNavigate();
  const location = useLocation();
  const contentRef = useRef<HTMLElement | null>(null);
  const [displayLocation, setDisplayLocation] = useState(location);
  const [transitionStage, setTransitionStage] = useState<'fadeIn' | 'fadeOut'>('fadeIn');
  const [customBackground, setCustomBackground] = useState<CustomBackgroundPreference>(() => getCustomBackgroundPreference());

  const handleOpenProject = (projectDir: string, config: string) => {
    const cfg = config || 'config.yaml';
    useProjectStore.getState().openProject(projectDir, cfg);
    addProjectToHistory(projectDir, cfg);
    navigate('/project/translate');
  };

  const handleCloseProject = () => {
    useProjectStore.getState().closeProject();
    navigate('/');
  };

  useEffect(() => {
    const handleCustomBackgroundChange = () => {
      setCustomBackground(getCustomBackgroundPreference());
    };

    window.addEventListener(CUSTOM_BACKGROUND_CHANGE_EVENT, handleCustomBackgroundChange as EventListener);
    return () => {
      window.removeEventListener(CUSTOM_BACKGROUND_CHANGE_EVENT, handleCustomBackgroundChange as EventListener);
    };
  }, []);

  // Timeout fallback: if animationend never fires (e.g. prefers-reduced-motion
  // disables animations), force the transition to complete so the page doesn't
  // get stuck on the old route.
  const transitionTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (location.pathname !== displayLocation.pathname) {
      setTransitionStage('fadeOut');
      if (transitionTimerRef.current) clearTimeout(transitionTimerRef.current);
      transitionTimerRef.current = setTimeout(() => {
        setDisplayLocation(location);
        setTransitionStage('fadeIn');
      }, 200);
    }
    return () => {
      if (transitionTimerRef.current) {
        clearTimeout(transitionTimerRef.current);
        transitionTimerRef.current = null;
      }
    };
  }, [location, displayLocation]);

  useLayoutEffect(() => {
    contentRef.current?.scrollTo({ top: 0, left: 0, behavior: 'instant' as ScrollBehavior });
  }, [displayLocation.pathname]);

  const handleTransitionEnd = () => {
    if (transitionStage === 'fadeOut') {
      if (transitionTimerRef.current) {
        clearTimeout(transitionTimerRef.current);
        transitionTimerRef.current = null;
      }
      setDisplayLocation(location);
      setTransitionStage('fadeIn');
    }
  };

  const surfaceOpacity = customBackground.surfaceOpacity / 100;
  const softSurfaceOpacity = Math.max(0.1, surfaceOpacity - 0.14);
  const appLayoutStyle = {
    '--custom-bg-surface-opacity': String(surfaceOpacity),
    '--custom-bg-surface-soft-opacity': String(softSurfaceOpacity),
  } as CSSProperties;

  return (
    <div
      className={`app-layout${customBackground.imageDataUrl ? ' app-layout--custom-background' : ''}`}
      style={appLayoutStyle}
    >
      <div
        className={`app-layout__custom-background ${customBackground.imageDataUrl ? 'app-layout__custom-background--visible' : ''}`}
        style={{
          backgroundImage: customBackground.imageDataUrl ? `url(${customBackground.imageDataUrl})` : 'none',
          opacity: customBackground.opacity / 100,
        }}
      />
      <Sidebar onCloseProject={handleCloseProject} />
      <main
        ref={contentRef}
        className={`app-layout__content page-transition-${transitionStage}`}
        onAnimationEnd={handleTransitionEnd}
      >
        <Routes location={displayLocation}>
          <Route
            path="/"
            element={<HomePage onOpenProject={handleOpenProject} />}
          />
          <Route
            path="/common-dictionaries"
            element={(
              <Suspense fallback={<RouteLoadingFallback />}>
                <CommonDictionaryPage />
              </Suspense>
            )}
          />
          <Route
            path="/settings"
            element={(
              <Suspense fallback={<RouteLoadingFallback />}>
                <SettingsPage />
              </Suspense>
            )}
          />
          <Route
            path="/new-project"
            element={(
              <Suspense fallback={<RouteLoadingFallback />}>
                <NewProjectWizard onOpenProject={handleOpenProject} />
              </Suspense>
            )}
          />
          <Route
            path="/project/*"
            element={(
              <Suspense fallback={<RouteLoadingFallback />}>
                <ProjectLayout />
              </Suspense>
            )}
          />
        </Routes>
      </main>
    </div>
  );
}
