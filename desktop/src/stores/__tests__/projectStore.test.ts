import { describe, it, expect, beforeEach } from 'vitest';
import { useProjectStore } from '../projectStore';

describe('useProjectStore', () => {
  beforeEach(() => {
    // Reset to initial state before each test
    useProjectStore.setState({
      projectDir: null,
      configFileName: 'config.yaml',
      configLoaded: false,
      isTranslating: false,
      currentJobId: null,
      lastTranslator: null,
    });
  });

  it('starts with no project open', () => {
    const state = useProjectStore.getState();
    expect(state.projectDir).toBeNull();
    expect(state.configLoaded).toBe(false);
    expect(state.isTranslating).toBe(false);
  });

  it('openProject sets project dir and config file name', () => {
    useProjectStore.getState().openProject('/path/to/project', 'custom.yaml');
    const state = useProjectStore.getState();
    expect(state.projectDir).toBe('/path/to/project');
    expect(state.configFileName).toBe('custom.yaml');
    expect(state.configLoaded).toBe(false);
  });

  it('openProject defaults configFileName to config.yaml', () => {
    useProjectStore.getState().openProject('/path/to/project');
    expect(useProjectStore.getState().configFileName).toBe('config.yaml');
  });

  it('openProject resets translation state', () => {
    useProjectStore.getState().setTranslating(true, 'job-123');
    useProjectStore.getState().openProject('/new/project');
    const state = useProjectStore.getState();
    expect(state.isTranslating).toBe(false);
    expect(state.currentJobId).toBeNull();
  });

  it('closeProject resets all state', () => {
    useProjectStore.getState().openProject('/path/to/project');
    useProjectStore.getState().setConfigLoaded(true);
    useProjectStore.getState().setTranslating(true, 'job-456');
    useProjectStore.getState().setLastTranslator('ForGal-full-pipeline');

    useProjectStore.getState().closeProject();

    const state = useProjectStore.getState();
    expect(state.projectDir).toBeNull();
    expect(state.configFileName).toBe('config.yaml');
    expect(state.configLoaded).toBe(false);
    expect(state.isTranslating).toBe(false);
    expect(state.currentJobId).toBeNull();
    expect(state.lastTranslator).toBeNull();
  });

  it('setTranslating updates isTranslating and currentJobId', () => {
    useProjectStore.getState().setTranslating(true, 'job-789');
    const state = useProjectStore.getState();
    expect(state.isTranslating).toBe(true);
    expect(state.currentJobId).toBe('job-789');

    useProjectStore.getState().setTranslating(false);
    expect(useProjectStore.getState().isTranslating).toBe(false);
    expect(useProjectStore.getState().currentJobId).toBeNull();
  });

  it('setLastTranslator updates lastTranslator', () => {
    useProjectStore.getState().setLastTranslator('GenDic');
    expect(useProjectStore.getState().lastTranslator).toBe('GenDic');
  });
});
