import { useCallback, useEffect, useState } from 'react';
import { NavLink, useNavigate, useLocation } from 'react-router-dom';
import { invoke } from '@tauri-apps/api/core';
import { submitJob, fetchJob, fetchProjectRuntime, encodeProjectDir } from '../lib/api';
import { Icon } from './icons';
import { InlineFeedback } from './page-state/InlineFeedback';
import { useProjectStore, useUIStore } from '../stores';
import logoUrl from '../assets/logo.png';

const OUTPUT_FOLDER_NAME = 'gt_output';

const PROJECT_TABS = [
  { path: 'translate', label: '翻译工作台', icon: 'globe' },
  { path: 'cache', label: '缓存与问题', icon: 'database' },
  { path: 'dictionary', label: '项目字典', icon: 'book' },
  { path: 'names', label: '人名翻译', icon: 'user' },
  { path: 'config', label: '配置编辑', icon: 'settings' },
];

type SidebarProps = {
  onCloseProject: () => void;
};

export function Sidebar({ onCloseProject }: SidebarProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const projectDir = useProjectStore((s) => s.projectDir);
  const [expanded, setExpanded] = useState(true);
  const [rebuilding, setRebuilding] = useState(false);
  const [translating, setTranslating] = useState(false);
  const [rebuildToast, setRebuildToast] = useState<string | null>(null);

  // Poll translation status for the current project
  useEffect(() => {
    if (!projectDir) {
      setTranslating(false);
      return;
    }

    let cancelled = false;
    const pollStatus = async () => {
      try {
        const runtime = await fetchProjectRuntime(encodeProjectDir(projectDir));
        const isTranslating = runtime.job !== null &&
          (runtime.job.status === 'pending' || runtime.job.status === 'running');
        if (!cancelled) setTranslating(isTranslating);
      } catch {
        if (!cancelled) setTranslating(false);
      }
    };

    void pollStatus();
    const poller = window.setInterval(pollStatus, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(poller);
    };
  }, [projectDir]);

  const toggleExpanded = useCallback(() => {
    setExpanded((prev) => !prev);
  }, []);

  const handleRebuildOutput = useCallback(async () => {
    if (!projectDir || rebuilding || translating) return;
    const configFileName = useProjectStore.getState().configFileName;
    setRebuilding(true);
    try {
      const job = await submitJob({
        project_dir: projectDir,
        config_file_name: configFileName,
        translator: 'rebuilda',
      });
      for (let i = 0; i < 120; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        const status = await fetchJob(job.job_id);
        if (status.status === 'completed' || status.status === 'failed' || status.status === 'cancelled') {
          if (status.success) {
            const normalizedDir = projectDir.replace(/[\\/]+$/, '');
            const outputDir = `${normalizedDir}\\${OUTPUT_FOLDER_NAME}`;
            await invoke('open_folder', { path: outputDir });
          } else {
            setRebuildToast(`输出文件重建失败: ${status.error || '未知错误'}`);
          }
          return;
        }
      }
      setRebuildToast('输出文件重建超时');
    } catch (err) {
      setRebuildToast(`输出文件重建出错: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setRebuilding(false);
    }
  }, [projectDir, rebuilding, translating]);

  const handleCloseProject = useCallback(async () => {
    if (!projectDir) return;
    const confirmed = await useUIStore.getState().confirm({
      title: '关闭项目',
      message: '确定要关闭当前项目吗？未保存的更改将丢失。',
      confirmText: '关闭',
      cancelText: '取消',
      tone: 'danger',
    });
    if (confirmed) {
      onCloseProject();
    }
  }, [projectDir, onCloseProject]);

  const projectName = projectDir
    ? projectDir.replace(/[/\\]/g, '/').split('/').filter(Boolean).pop() || projectDir
    : '';

  const isProjectActive = location.pathname.startsWith('/project');

  return (
    <aside className={`sidebar ${expanded ? 'sidebar--expanded' : 'sidebar--collapsed'}`}>
      <div className="sidebar__header">
        <img src={logoUrl} alt="" className="sidebar__logo-img" />
        {expanded && <span className="sidebar__logo">GalTransl</span>}
      </div>

      <div className="sidebar__top-nav">
        <NavLink
          to="/"
          end
          className={({ isActive }) =>
            `sidebar__nav-item ${isActive ? 'sidebar__nav-item--active' : ''}`
          }
          title="首页"
        >
          <span className="sidebar__nav-icon">
            <Icon name="home" size={20} />
          </span>
          {expanded && <span className="sidebar__nav-label">首页</span>}
        </NavLink>
      </div>

      {projectDir && (
        <nav className="sidebar__nav">
          <div className="sidebar__project-group">
            {expanded ? (
              <>
                <div className="sidebar__project-header" title={projectDir}>
                  <span
                    className="sidebar__nav-icon sidebar__project-icon sidebar__project-icon--link"
                    role="button"
                    tabIndex={0}
                    title="打开项目文件夹"
                    onClick={(e) => { e.stopPropagation(); void invoke('open_folder', { path: projectDir }); }}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.stopPropagation(); e.preventDefault(); void invoke('open_folder', { path: projectDir }); } }}
                  >
                    <Icon name="folder-open" size={20} />
                  </span>
                  <span className="sidebar__project-name">{projectName}</span>
                  <button
                    className="sidebar__project-close"
                    type="button"
                    onClick={handleCloseProject}
                    title="关闭项目"
                  >
                    <Icon name="x" size={16} />
                  </button>
                </div>
                <div className="sidebar__project-children sidebar__project-children--expanded">
                  {PROJECT_TABS.map((tab) => (
                    <NavLink
                      key={tab.path}
                      to={`/project/${tab.path}`}
                      className={({ isActive }) =>
                        `sidebar__project-child ${isActive ? 'sidebar__project-child--active' : ''}`
                      }
                    >
                      <span className="sidebar__project-child-icon">
                        <Icon name={tab.icon} size={18} />
                      </span>
                      <span className="sidebar__project-child-label">{tab.label}</span>
                    </NavLink>
                  ))}
                  <div className="sidebar__project-child-separator" />
                  <button
                    type="button"
                    className={`sidebar__project-child sidebar__project-child--action${translating ? ' sidebar__project-child--disabled' : ''}`}
                    onClick={(e) => { e.preventDefault(); if (!rebuilding && !translating) void handleRebuildOutput(); }}
                    title={translating ? '项目正在翻译中，无法构建输出' : '重建输出文件并打开文件夹'}
                    disabled={rebuilding || translating}
                    style={(rebuilding || translating) ? { opacity: 0.6, pointerEvents: 'none' } : undefined}
                  >
                    <span className="sidebar__project-child-icon">
                      <Icon name={rebuilding ? 'loader' : translating ? 'ban' : 'upload'} size={18} />
                    </span>
                    <span className="sidebar__project-child-label">构建输出</span>
                  </button>
                </div>
              </>
            ) : isProjectActive ? (
              <>
                <NavLink
                  to="/project/translate"
                  className={({ isActive }) =>
                    `sidebar__nav-item ${isActive ? 'sidebar__nav-item--active' : ''}`
                  }
                  title={projectName}
                >
                  <span className="sidebar__nav-icon">
                    <Icon name="folder" size={20} />
                  </span>
                </NavLink>
                {PROJECT_TABS.map((tab) => (
                  <NavLink
                    key={tab.path}
                    to={`/project/${tab.path}`}
                    className={({ isActive }) =>
                      `sidebar__nav-item sidebar__nav-item--sub ${isActive ? 'sidebar__nav-item--active' : ''}`
                    }
                    title={tab.label}
                  >
                    <span className="sidebar__nav-icon">
                      <Icon name={tab.icon} size={20} />
                    </span>
                  </NavLink>
                ))}
                <button
                  type="button"
                  className={`sidebar__nav-item sidebar__nav-item--sub${translating ? ' sidebar__nav-item--disabled' : ''}`}
                  onClick={(e) => { e.preventDefault(); if (!rebuilding && !translating) void handleRebuildOutput(); }}
                  title={translating ? '项目正在翻译中' : '构建输出'}
                  disabled={rebuilding || translating}
                  style={(rebuilding || translating) ? { opacity: 0.6, pointerEvents: 'none' } : undefined}
                >
                  <span className="sidebar__nav-icon">
                    <Icon name={rebuilding ? 'loader' : translating ? 'ban' : 'upload'} size={20} />
                  </span>
                </button>
              </>
            ) : (
              <NavLink
                to="/project/translate"
                className={({ isActive }) =>
                  `sidebar__nav-item ${isActive ? 'sidebar__nav-item--active' : ''}`
                }
                title={projectName}
              >
                <span className="sidebar__nav-icon">
                  <Icon name="folder" size={20} />
                </span>
              </NavLink>
            )}
          </div>
        </nav>
      )}

      <nav className="sidebar__bottom-nav">
        <NavLink
          to="/common-dictionaries"
          className={({ isActive }) =>
            `sidebar__nav-item ${isActive ? 'sidebar__nav-item--active' : ''}`
          }
          title="通用字典管理"
        >
          <span className="sidebar__nav-icon">
            <Icon name="library" size={20} />
          </span>
          {expanded && <span className="sidebar__nav-label">通用字典管理</span>}
        </NavLink>

        <NavLink
          to="/settings"
          className={({ isActive }) =>
            `sidebar__nav-item ${isActive ? 'sidebar__nav-item--active' : ''}`
          }
          title="设置"
        >
          <span className="sidebar__nav-icon">
            <Icon name="settings" size={20} />
          </span>
          {expanded && <span className="sidebar__nav-label">设置</span>}
        </NavLink>
      </nav>

      <div className="sidebar__footer">
        <button
          className="sidebar__toggle-btn"
          type="button"
          onClick={toggleExpanded}
          title={expanded ? '收起侧边栏' : '展开侧边栏'}
        >
          <span className={`sidebar__toggle-icon ${expanded ? 'sidebar__toggle-icon--flip' : ''}`}>
            <Icon name={expanded ? 'chevron-left' : 'chevron-right'} size={18} />
          </span>
          {expanded && <span className="sidebar__toggle-label">收起</span>}
        </button>
      </div>

      {rebuildToast ? (
        <div className="sidebar__toast-host" aria-live="assertive">
          <InlineFeedback
            tone="error"
            title="构建输出失败"
            description={rebuildToast}
            autoDismiss={2800}
            onDismiss={() => setRebuildToast(null)}
          />
        </div>
      ) : null}
    </aside>
  );
}
