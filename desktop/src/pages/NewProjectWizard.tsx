import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { open } from '@tauri-apps/plugin-dialog';
import { invoke } from '@tauri-apps/api/core';
import { getCurrentWebviewWindow } from '@tauri-apps/api/webviewWindow';
import { Button } from '../components/Button';
import { Icon } from '../components/icons';
import { PageHeader } from '../components/PageHeader';
import { InlineFeedback } from '../components/page-state';
import {
  BACKEND_PROFILES_CHANGE_EVENT, DEFAULT_BACKEND_PROFILE_CHANGE_EVENT,
  type PluginInfo, getDefaultBackendProfile, getBackendProfileNames,
  fetchPlugins, fetchDefaultProjectConfigTemplate, fetchProjectConfig,
  fetchTranslationGuidelines, updateProjectConfig, submitJob, fetchJob, encodeProjectDir,
} from '../lib/api';
import { StepProjectInfo } from './wizard/StepProjectInfo';
import { StepImportFiles } from './wizard/StepImportFiles';
import { StepBackendSelect } from './wizard/StepBackendSelect';
import { StepSettings } from './wizard/StepSettings';
import { StepExtractNames } from './wizard/StepExtractNames';

const STEPS = ['项目位置', '导入文件', '翻译后端', '常用设置', '提取人名'];
const LAST_PARENT_DIR_KEY = 'galtransl-new-project-last-parent-dir';

type NewProjectWizardProps = { onOpenProject: (projectDir: string, config: string) => void };

export function NewProjectWizard({ onOpenProject }: NewProjectWizardProps) {
  const navigate = useNavigate();
  const [currentStep, setCurrentStep] = useState(0);
  const [stepDirection, setStepDirection] = useState<'forward' | 'backward'>('forward');
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error' | 'info'; message: string } | null>(null);

  // Step 1 state
  const [parentDir, setParentDir] = useState(() => { try { return localStorage.getItem(LAST_PARENT_DIR_KEY) || ''; } catch { return ''; } });
  const [projectName, setProjectName] = useState('');
  const [projectCreated, setProjectCreated] = useState(false);

  // Step 2 state
  const [importedFiles, setImportedFiles] = useState<string[]>([]);

  // Step 3 state
  const [backendProfileNames, setBackendProfileNames] = useState<string[]>([]);
  const [selectedBackend, setSelectedBackend] = useState('__default__');
  const [defaultBackendName, setDefaultBackendName] = useState(() => getDefaultBackendProfile());

  // Step 4 state
  const [filePlugins, setFilePlugins] = useState<PluginInfo[]>([]);
  const [selectedFilePlugin, setSelectedFilePlugin] = useState('file_galtransl_json');
  const [workersPerProject, setWorkersPerProject] = useState(16);
  const [numPerRequest, setNumPerRequest] = useState(16);
  const [dynamicNumPerRequest, setDynamicNumPerRequest] = useState(false);
  const [dynamicNumPerRequestMin, setDynamicNumPerRequestMin] = useState(8);
  const [dynamicNumPerRequestMax, setDynamicNumPerRequestMax] = useState(64);
  const [language, setLanguage] = useState('zh-cn');
  const [guidelines, setGuidelines] = useState<string[]>([]);
  const [translationGuideline, setTranslationGuideline] = useState('');
  const [settingsSaved, setSettingsSaved] = useState(false);

  // Step 5 state
  const [nameJobStatus, setNameJobStatus] = useState<'idle' | 'running' | 'completed' | 'failed'>('idle');
  const [nameJobMessage, setNameJobMessage] = useState('');

  const projectDir = useMemo(() => {
    if (!parentDir || !projectName) return '';
    return `${parentDir}${parentDir.includes('/') ? '/' : '\\'}${projectName}`;
  }, [parentDir, projectName]);
  const gtInputDir = useMemo(() => projectDir ? `${projectDir}${projectDir.includes('/') ? '/' : '\\'}gt_input` : '', [projectDir]);

  const importPathsToInput = useCallback(async (paths: string[]) => {
    if (!gtInputDir || paths.length === 0) return;
    const existingNames = new Set(importedFiles.map((n) => n.toLowerCase()));
    const namesInBatch = new Set<string>(); const pathsToImport: string[] = []; const acceptedNames: string[] = [];
    for (const p of paths) {
      const name = p.split(/[/\\]/).pop() || p; const key = name.toLowerCase();
      if (existingNames.has(key) || namesInBatch.has(key)) continue;
      namesInBatch.add(key); pathsToImport.push(p); acceptedNames.push(name);
    }
    if (pathsToImport.length === 0) { setFeedback({ type: 'info', message: '已过滤重复文件，本次无新增导入。' }); return; }
    try {
      await invoke('copy_files', { sources: pathsToImport, destinationDir: gtInputDir });
      setImportedFiles((prev) => [...prev, ...acceptedNames]);
      setFeedback({ type: 'success', message: pathsToImport.length < paths.length ? `已导入 ${pathsToImport.length} 个文件，已过滤 ${paths.length - pathsToImport.length} 个重复文件` : `已导入 ${pathsToImport.length} 个文件` });
    } catch (err) { setFeedback({ type: 'error', message: `导入失败: ${err instanceof Error ? err.message : String(err)}` }); }
  }, [gtInputDir, importedFiles]);

  // Drag-drop listener
  useEffect(() => {
    const currentWindow = getCurrentWebviewWindow(); let disposed = false;
    const unlistenPromise = currentWindow.onDragDropEvent((event: unknown) => {
      if (currentStep !== 1) return;
      const payload = (event as { payload?: { type?: string; paths?: string[] } })?.payload;
      if (payload?.type !== 'drop') return;
      const paths = Array.isArray(payload.paths) ? payload.paths : [];
      if (paths.length === 0) { setFeedback({ type: 'error', message: '未能读取拖拽文件路径' }); return; }
      void importPathsToInput(paths);
    });
    return () => { disposed = true; void unlistenPromise.then((u) => { if (!disposed) return; u(); }); };
  }, [currentStep, importPathsToInput]);

  useEffect(() => { try { if (parentDir.trim()) localStorage.setItem(LAST_PARENT_DIR_KEY, parentDir); } catch {} }, [parentDir]);

  const handleSelectParentDir = useCallback(async () => { const s = await open({ directory: true }); if (s) setParentDir(typeof s === 'string' ? s.replace(/\//g, '\\') : s); }, []);
  const handleCreateProject = useCallback(async () => {
    if (!projectDir) { setFeedback({ type: 'error', message: '请选择目录并输入项目名称' }); return; }
    try {
      const sep = projectDir.includes('/') ? '/' : '\\';
      await fetchDefaultProjectConfigTemplate().then((c) => invoke('write_text_file', { path: `${projectDir}${sep}config.yaml`, content: c }));
      await invoke('create_dir', { path: projectDir });
      await invoke('create_dir', { path: `${projectDir}${sep}gt_input` });
      await invoke('create_dir', { path: `${projectDir}${sep}gt_output` });
      await invoke('create_dir', { path: `${projectDir}${sep}transl_cache` });
      setProjectCreated(true); setFeedback({ type: 'success', message: '项目创建成功！' });
    } catch (err) { setFeedback({ type: 'error', message: `创建失败: ${err instanceof Error ? err.message : String(err)}` }); }
  }, [projectDir]);

  const handleFileDrop = useCallback(async (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault(); e.stopPropagation(); e.currentTarget.classList.remove('drop-zone--over');
    if (!gtInputDir) return;
    const files = Array.from(e.dataTransfer.files);
    const directPaths = files.map((f) => (f as File & { path?: string }).path).filter((p): p is string => Boolean(p?.trim()));
    const parseUriList = () => {
      const data = e.dataTransfer.getData('text/uri-list') || e.dataTransfer.getData('text/plain');
      if (!data) return [] as string[];
      return data.split(/\r?\n/).map((l) => l.trim()).filter((l) => l && !l.startsWith('#'))
        .map((l) => { try { if (l.startsWith('file://')) { const u = new URL(l); const d = decodeURIComponent(u.pathname || ''); return (/^\/[A-Za-z]:/.test(d) ? d.slice(1) : d).replace(/\//g, '\\'); } return decodeURIComponent(l).replace(/\//g, '\\'); } catch { return l.replace(/\//g, '\\'); } })
        .filter((p) => /^[A-Za-z]:\\/.test(p) || p.startsWith('\\\\'));
    };
    const paths = directPaths.length > 0 ? directPaths : parseUriList();
    if (paths.length === 0) { setFeedback({ type: 'error', message: '未能读取拖拽文件路径' }); return; }
    await importPathsToInput(paths);
  }, [gtInputDir, importPathsToInput]);

  const handleFilePick = useCallback(async () => { if (!gtInputDir) return; const s = await open({ multiple: true }); if (!s) return; await importPathsToInput((Array.isArray(s) ? s : [s]) as string[]); }, [gtInputDir, importPathsToInput]);
  const handleOpenInputFolder = useCallback(async () => { if (!gtInputDir) return; try { await invoke('open_folder', { path: gtInputDir }); } catch (err) { setFeedback({ type: 'error', message: `打开失败: ${err instanceof Error ? err.message : String(err)}` }); } }, [gtInputDir]);

  useEffect(() => { if (currentStep !== 2) return; setBackendProfileNames(getBackendProfileNames()); }, [currentStep]);
  useEffect(() => { const h = () => setDefaultBackendName(getDefaultBackendProfile()); window.addEventListener(DEFAULT_BACKEND_PROFILE_CHANGE_EVENT, h); return () => window.removeEventListener(DEFAULT_BACKEND_PROFILE_CHANGE_EVENT, h); }, []);
  useEffect(() => { const h = () => { if (currentStep === 2) setBackendProfileNames(getBackendProfileNames()); }; window.addEventListener(BACKEND_PROFILES_CHANGE_EVENT, h); return () => window.removeEventListener(BACKEND_PROFILES_CHANGE_EVENT, h); }, [currentStep]);

  useEffect(() => { if (currentStep !== 3) return; fetchPlugins().then((p) => setFilePlugins(p.filter((x) => x.type === 'file'))).catch(() => {}); fetchTranslationGuidelines().then((l) => { setGuidelines(l); setTranslationGuideline((prev) => prev || (l.includes('日译中_增强') ? '日译中_增强' : l[0] || '')); }).catch(() => {}); }, [currentStep]);

  const handleSaveSettings = useCallback(async () => {
    if (!projectDir) return;
    try {
      const projectId = encodeProjectDir(projectDir);
      const res = await fetchProjectConfig(projectId, 'config.yaml');
      const config = { ...res.config };
      const common: Record<string, unknown> = { ...((config.common as Record<string, unknown>) || {}), workersPerProject, language, 'gpt.numPerRequestTranslate': numPerRequest, 'gpt.dynamicNumPerRequestTranslate': dynamicNumPerRequest, 'gpt.dynamicNumPerRequestTranslate.min': dynamicNumPerRequestMin, 'gpt.dynamicNumPerRequestTranslate.max': dynamicNumPerRequestMax, 'gpt.contextNum': 8 };
      if (translationGuideline) common['gpt.translation_guideline'] = translationGuideline;
      config.common = common;
      config.plugin = { ...((config.plugin as Record<string, unknown>) || {}), filePlugin: selectedFilePlugin, textPlugins: Array.isArray(((config.plugin as Record<string, unknown>) || {}).textPlugins) ? ((config.plugin as Record<string, unknown>)).textPlugins : [] };
      await updateProjectConfig(projectId, { config, config_file_name: 'config.yaml' });
      const { setSelectedBackendProfile } = await import('../lib/api');
      setSelectedBackendProfile(projectDir, selectedBackend);
      setSettingsSaved(true); setFeedback({ type: 'success', message: '设置已保存' });
    } catch (err) { setFeedback({ type: 'error', message: `保存失败: ${err instanceof Error ? err.message : String(err)}` }); }
  }, [projectDir, workersPerProject, language, numPerRequest, dynamicNumPerRequest, dynamicNumPerRequestMin, dynamicNumPerRequestMax, selectedFilePlugin, selectedBackend, translationGuideline]);

  useEffect(() => {
    if (currentStep !== 4 || nameJobStatus !== 'idle' || !projectDir) return;
    if (importedFiles.length === 0) { setNameJobStatus('completed'); setNameJobMessage('gt_input 中没有文件，已跳过人名提取。'); return; }
    const run = async () => {
      try { setNameJobStatus('running'); const job = await submitJob({ project_dir: projectDir, config_file_name: 'config.yaml', translator: 'dump-name' });
        const poll = async () => { try { const s = await fetchJob(job.job_id); if (s.status === 'completed') { setNameJobStatus('completed'); setNameJobMessage(s.success ? '人名提取完成！' : `提取完成但有警告: ${s.error || ''}`); } else if (s.status === 'failed') { setNameJobStatus('failed'); setNameJobMessage(s.error || '提取失败'); } else setTimeout(poll, 2000); } catch { setTimeout(poll, 3000); } }; poll();
      } catch (err) { setNameJobStatus('failed'); setNameJobMessage(err instanceof Error ? err.message : String(err)); } };
    run();
  }, [currentStep]);

  const handleFinish = useCallback(() => { if (!projectDir) return; onOpenProject(projectDir, 'config.yaml'); }, [projectDir, onOpenProject]);
  const canNext = useMemo(() => currentStep === 0 ? projectCreated : currentStep === 3 ? settingsSaved : true, [currentStep, projectCreated, settingsSaved]);
  const stepProgress = useMemo(() => Math.round(((currentStep + 1) / STEPS.length) * 100), [currentStep]);
  useEffect(() => { if (!settingsSaved) return; setSettingsSaved(false); }, [selectedBackend, selectedFilePlugin, workersPerProject, numPerRequest, language]);

  const stepRenderers = [
    <StepProjectInfo key="s1" parentDir={parentDir} projectName={projectName} projectDir={projectDir} projectCreated={projectCreated}
      onSelectParentDir={handleSelectParentDir} onParentDirChange={setParentDir} onProjectNameChange={setProjectName}
      onProjectCreatedChange={setProjectCreated} onCreateProject={handleCreateProject} />,
    <StepImportFiles key="s2" gtInputDir={gtInputDir} importedFiles={importedFiles} onFileDrop={handleFileDrop} onFilePick={handleFilePick} onOpenInputFolder={handleOpenInputFolder} />,
    <StepBackendSelect key="s3" selectedBackend={selectedBackend} onBackendChange={setSelectedBackend} backendProfileNames={backendProfileNames} defaultBackendName={defaultBackendName} />,
    <StepSettings key="s4" filePlugins={filePlugins} selectedFilePlugin={selectedFilePlugin} workersPerProject={workersPerProject}
      numPerRequest={numPerRequest} dynamicNumPerRequest={dynamicNumPerRequest} dynamicNumPerRequestMin={dynamicNumPerRequestMin}
      dynamicNumPerRequestMax={dynamicNumPerRequestMax} language={language} translationGuideline={translationGuideline}
      guidelines={guidelines} settingsSaved={settingsSaved} onFilePluginChange={setSelectedFilePlugin}
      onWorkersChange={setWorkersPerProject} onNumPerRequestChange={setNumPerRequest} onDynamicNumChange={setDynamicNumPerRequest}
      onDynamicMinChange={setDynamicNumPerRequestMin} onDynamicMaxChange={setDynamicNumPerRequestMax}
      onLanguageChange={setLanguage} onGuidelineChange={setTranslationGuideline} onSaveSettings={handleSaveSettings} />,
    <StepExtractNames key="s5" nameJobStatus={nameJobStatus} nameJobMessage={nameJobMessage} />,
  ];

  return (
    <div className="wizard-page">
      <PageHeader title="新建项目" description="按照向导创建一个新的翻译项目。" />
      <ul className="wizard-steps">
        {STEPS.map((label, i) => (
          <li key={i} className={`wizard-step${i === currentStep ? ' wizard-step--active' : ''}${i < currentStep ? ' wizard-step--completed' : ''}`}>
            <span className="wizard-step__number">{i < currentStep ? <Icon name="check" size={12} /> : i + 1}</span>
            <span className="wizard-step__label">{label}</span>
          </li>
        ))}
      </ul>
      <div className="wizard-content">
        <div className="wizard-step-summary">
          <div className="wizard-step-summary__top"><span>第 {currentStep + 1} / {STEPS.length} 步</span><strong>{STEPS[currentStep]}</strong></div>
          <div className="wizard-step-summary__bar"><span style={{ width: `${stepProgress}%` }} /></div>
        </div>
        <div key={currentStep} className={`wizard-step-stage wizard-step-stage--${stepDirection}`}>
          {stepRenderers[currentStep]}
        </div>
        {feedback && <InlineFeedback className={feedback.type === 'success' ? 'inline-alert--floating' : undefined} tone={feedback.type === 'error' ? 'error' : feedback.type === 'success' ? 'success' : 'info'} title={feedback.message} />}
      </div>
      <div className="wizard-nav">
        <Button variant="secondary" onClick={() => { setStepDirection('backward'); setCurrentStep((s) => Math.max(0, s - 1)); }} disabled={currentStep === 0}>上一步</Button>
        {currentStep < 4
          ? <Button onClick={() => { setStepDirection('forward'); setCurrentStep((s) => Math.min(STEPS.length - 1, s + 1)); }} disabled={!canNext}>下一步</Button>
          : <Button onClick={handleFinish}>完成并打开项目</Button>}
      </div>
    </div>
  );
}
