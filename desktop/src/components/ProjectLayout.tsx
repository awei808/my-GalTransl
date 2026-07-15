import { Suspense, lazy, useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useProjectStore } from '../stores';
import { encodeProjectDir } from '../lib/api';

const ProjectTranslatePage = lazy(async () => {
  const mod = await import('../pages/ProjectTranslatePage');
  return { default: mod.ProjectTranslatePage };
});

const ProjectConfigPage = lazy(async () => {
  const mod = await import('../pages/ProjectConfigPage');
  return { default: mod.ProjectConfigPage };
});

const ProjectDictionaryPage = lazy(async () => {
  const mod = await import('../pages/ProjectDictionaryPage');
  return { default: mod.ProjectDictionaryPage };
});

const ProjectNamePage = lazy(async () => {
  const mod = await import('../pages/ProjectNamePage');
  return { default: mod.ProjectNamePage };
});

const ProjectCachePage = lazy(async () => {
  const mod = await import('../pages/ProjectCachePage');
  return { default: mod.ProjectCachePage };
});

/** Tab path → component mapping */
const TAB_MAP: { path: string; label: string }[] = [
  { path: 'translate', label: '翻译工作台' },
  { path: 'cache', label: '缓存与问题' },
  { path: 'config', label: '配置编辑' },
  { path: 'dictionary', label: '项目字典' },
  { path: 'names', label: '人名翻译' },
];

/** Shared context passed to every child page */
export interface ProjectPageContext {
  projectDir: string;
  /** Base64url-encoded project directory — used in backend API URL paths */
  projectId: string;
  configFileName: string;
}

const TAB_STORAGE_KEY = 'galtransl-last-project-tab';
const VALID_TABS = ['translate', 'cache', 'config', 'dictionary', 'names'] as const;

function loadLastTab(): string {
  try {
    const tab = localStorage.getItem(TAB_STORAGE_KEY);
    if (tab && (VALID_TABS as readonly string[]).includes(tab)) return tab;
  } catch {}
  return 'translate';
}

function saveLastTab(tab: string): void {
  try {
    localStorage.setItem(TAB_STORAGE_KEY, tab);
  } catch {}
}

export function ProjectLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const projectDir = useProjectStore((s) => s.projectDir);
  const configFileName = useProjectStore((s) => s.configFileName);

  // Redirect to home if no project is open
  useEffect(() => {
    if (!projectDir) {
      navigate('/', { replace: true });
    }
  }, [projectDir, navigate]);

  if (!projectDir) return null;

  // Extract current tab from URL: /project/cache → "cache"
  const segments = location.pathname.split('/');
  const currentTab = segments[2] || 'translate';

  // If accessing /project without a tab, redirect to the last visited tab
  useEffect(() => {
    if (!segments[2]) {
      const lastTab = loadLastTab();
      navigate('/project/' + lastTab, { replace: true });
    }
  }, [segments[2], navigate]);

  const ctx: ProjectPageContext = useMemo(
    () => ({
      projectDir: projectDir || '',
      projectId: projectDir ? encodeProjectDir(projectDir) : '',
      configFileName,
    }),
    [projectDir, configFileName],
  );

  // ── Scroll to top on tab switch ──
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [currentTab]);

  const activeTab = TAB_MAP.some((tab) => tab.path === currentTab) ? currentTab : 'translate';

  // Save the active tab whenever it changes
  useEffect(() => {
    if (activeTab) {
      saveLastTab(activeTab);
    }
  }, [activeTab]);

  // 对"缓存与问题"页、人名翻译页、项目字典页：一旦访问过就保持挂载，
  // 避免重复加载，并让页内长任务在切换标签后继续运行。
  const [cacheVisited, setCacheVisited] = useState(() => activeTab === 'cache');
  const [dictionaryVisited, setDictionaryVisited] = useState(() => activeTab === 'dictionary');
  const [nameVisited, setNameVisited] = useState(() => activeTab === 'names');
  useEffect(() => {
    if (activeTab === 'cache') {
      setCacheVisited(true);
    }
  }, [activeTab]);

  useEffect(() => {
    if (activeTab === 'dictionary') {
      setDictionaryVisited(true);
    }
  }, [activeTab]);

  useEffect(() => {
    if (activeTab === 'names') {
      setNameVisited(true);
    }
  }, [activeTab]);

  const shouldRenderCache = cacheVisited || activeTab === 'cache';
  const shouldRenderDictionary = dictionaryVisited || activeTab === 'dictionary';
  const shouldRenderNames = nameVisited || activeTab === 'names';

  return (
    <div className="project-layout">
      <Suspense fallback={<div className="inline-feedback">页面加载中…</div>}>
        {activeTab === 'translate' ? <ProjectTranslatePage ctx={ctx} /> : null}
        {activeTab === 'config' ? <ProjectConfigPage ctx={ctx} /> : null}
        {shouldRenderDictionary ? (
          <div
            className="project-layout__keep-alive"
            hidden={activeTab !== 'dictionary'}
            style={activeTab !== 'dictionary' ? { display: 'none' } : undefined}
          >
            <ProjectDictionaryPage ctx={ctx} active={activeTab === 'dictionary'} />
          </div>
        ) : null}
        {shouldRenderNames ? (
          <div
            className="project-layout__keep-alive"
            hidden={activeTab !== 'names'}
            style={activeTab !== 'names' ? { display: 'none' } : undefined}
          >
            <ProjectNamePage ctx={ctx} active={activeTab === 'names'} />
          </div>
        ) : null}
        {shouldRenderCache ? (
          <div
            className="project-layout__keep-alive"
            hidden={activeTab !== 'cache'}
            style={activeTab !== 'cache' ? { display: 'none' } : undefined}
          >
            <ProjectCachePage ctx={ctx} active={activeTab === 'cache'} />
          </div>
        ) : null}
      </Suspense>
    </div>
  );
}
