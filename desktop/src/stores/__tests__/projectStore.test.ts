import { describe, it, expect, beforeEach } from 'vitest';
import { useProjectStore } from '../projectStore';

describe('useProjectStore', () => {
  beforeEach(() => {
    useProjectStore.setState({
      projectDir: null,
      configFileName: 'config.yaml',
      configLoaded: false,
      isTranslating: false,
      currentJobId: null,
      lastTranslator: null,
    });
  });

  describe('initial state', () => {
    it('starts with no project directory', () => {
      expect(useProjectStore.getState().projectDir).toBeNull();
    });

    it('starts with default config file name', () => {
      expect(useProjectStore.getState().configFileName).toBe('config.yaml');
    });

    it('starts with config not loaded', () => {
      expect(useProjectStore.getState().configLoaded).toBe(false);
    });

    it('starts with no translation running', () => {
      expect(useProjectStore.getState().isTranslating).toBe(false);
    });

    it('starts with no current job ID', () => {
      expect(useProjectStore.getState().currentJobId).toBeNull();
    });

    it('starts with no last translator', () => {
      expect(useProjectStore.getState().lastTranslator).toBeNull();
    });
  });

  describe('openProject', () => {
    it('sets projectDir and default configFileName', () => {
      useProjectStore.getState().openProject('/test/project');
      expect(useProjectStore.getState().projectDir).toBe('/test/project');
      expect(useProjectStore.getState().configFileName).toBe('config.yaml');
    });

    it('sets custom configFileName', () => {
      useProjectStore.getState().openProject('/test/project', 'custom.yaml');
      expect(useProjectStore.getState().configFileName).toBe('custom.yaml');
    });

    it('resets all state when opening a new project', () => {
      // Set some state first
      useProjectStore.setState({
        isTranslating: true,
        currentJobId: 'job-123',
        lastTranslator: 'gpt4',
        configLoaded: true,
      });

      useProjectStore.getState().openProject('/new/project');
      expect(useProjectStore.getState().isTranslating).toBe(false);
      expect(useProjectStore.getState().currentJobId).toBeNull();
      expect(useProjectStore.getState().lastTranslator).toBeNull();
      expect(useProjectStore.getState().configLoaded).toBe(false);
    });

    it('replaces existing project (single-project semantics)', () => {
      useProjectStore.getState().openProject('/first/project');
      expect(useProjectStore.getState().projectDir).toBe('/first/project');

      useProjectStore.getState().openProject('/second/project');
      expect(useProjectStore.getState().projectDir).toBe('/second/project');
    });

    it('handles Windows paths with backslashes', () => {
      useProjectStore.getState().openProject('E:\\GalTransl\\MyProject');
      expect(useProjectStore.getState().projectDir).toBe('E:\\GalTransl\\MyProject');
    });

    it('handles UNC paths', () => {
      useProjectStore.getState().openProject('\\\\server\\share\\project');
      expect(useProjectStore.getState().projectDir).toBe('\\\\server\\share\\project');
    });
  });

  describe('closeProject', () => {
    it('clears projectDir to null', () => {
      useProjectStore.getState().openProject('/test/project');
      useProjectStore.getState().closeProject();
      expect(useProjectStore.getState().projectDir).toBeNull();
    });

    it('resets configFileName to default', () => {
      useProjectStore.getState().openProject('/test/project', 'custom.yaml');
      useProjectStore.getState().closeProject();
      expect(useProjectStore.getState().configFileName).toBe('config.yaml');
    });

    it('clears all project-related state', () => {
      useProjectStore.setState({
        isTranslating: true,
        currentJobId: 'job-456',
        lastTranslator: 'claude',
        configLoaded: true,
      });

      useProjectStore.getState().closeProject();
      expect(useProjectStore.getState().isTranslating).toBe(false);
      expect(useProjectStore.getState().currentJobId).toBeNull();
      expect(useProjectStore.getState().lastTranslator).toBeNull();
      expect(useProjectStore.getState().configLoaded).toBe(false);
    });
  });

  describe('setConfigLoaded', () => {
    it('sets configLoaded to true', () => {
      useProjectStore.getState().setConfigLoaded(true);
      expect(useProjectStore.getState().configLoaded).toBe(true);
    });

    it('sets configLoaded to false', () => {
      useProjectStore.getState().setConfigLoaded(true);
      useProjectStore.getState().setConfigLoaded(false);
      expect(useProjectStore.getState().configLoaded).toBe(false);
    });
  });

  describe('setTranslating', () => {
    it('sets isTranslating and currentJobId', () => {
      useProjectStore.getState().setTranslating(true, 'job-789');
      expect(useProjectStore.getState().isTranslating).toBe(true);
      expect(useProjectStore.getState().currentJobId).toBe('job-789');
    });

    it('defaults jobId to null', () => {
      useProjectStore.getState().setTranslating(true);
      expect(useProjectStore.getState().isTranslating).toBe(true);
      expect(useProjectStore.getState().currentJobId).toBeNull();
    });

    it('clears translation state', () => {
      useProjectStore.getState().setTranslating(true, 'job-1');
      useProjectStore.getState().setTranslating(false);
      expect(useProjectStore.getState().isTranslating).toBe(false);
      expect(useProjectStore.getState().currentJobId).toBeNull();
    });
  });

  describe('setLastTranslator', () => {
    it('stores the last used translator', () => {
      useProjectStore.getState().setLastTranslator('ForGal-json-multi-chat');
      expect(useProjectStore.getState().lastTranslator).toBe('ForGal-json-multi-chat');
    });

    it('updates when called again', () => {
      useProjectStore.getState().setLastTranslator('ForGal-json-multi-chat');
      useProjectStore.getState().setLastTranslator('ForGal-full-pipeline');
      expect(useProjectStore.getState().lastTranslator).toBe('ForGal-full-pipeline');
    });
  });
});
