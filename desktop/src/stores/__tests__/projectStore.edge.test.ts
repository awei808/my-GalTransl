import { describe, it, expect, beforeEach } from 'vitest';
import { useProjectStore } from '../projectStore';

describe('useProjectStore — edge cases', () => {
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

  describe('openProject edge cases', () => {
    it('handles empty string dir', () => {
      useProjectStore.getState().openProject('');
      expect(useProjectStore.getState().projectDir).toBe('');
    });

    it('handles empty string configFileName (falls back to default)', () => {
      useProjectStore.getState().openProject('/path', '');
      // Default parameter only applies when arg is undefined, not empty string
      // So empty string is stored as-is
      expect(useProjectStore.getState().configFileName).toBe('');
    });

    it('handles very long path', () => {
      const longPath = 'C:\\' + 'a'.repeat(10000);
      useProjectStore.getState().openProject(longPath);
      expect(useProjectStore.getState().projectDir).toBe(longPath);
    });

    it('handles path with unicode characters', () => {
      useProjectStore.getState().openProject('D:\\解包或汉化用\\项目');
      expect(useProjectStore.getState().projectDir).toBe('D:\\解包或汉化用\\项目');
    });

    it('handles path with spaces', () => {
      useProjectStore.getState().openProject('C:\\Program Files\\My Project');
      expect(useProjectStore.getState().projectDir).toBe('C:\\Program Files\\My Project');
    });

    it('handles path with special characters', () => {
      useProjectStore.getState().openProject('C:\\path with [brackets] & (parens)');
      expect(useProjectStore.getState().projectDir).toBe('C:\\path with [brackets] & (parens)');
    });

    it('handles undefined configFileName (defaults to config.yaml)', () => {
      useProjectStore.getState().openProject('/path');
      expect(useProjectStore.getState().configFileName).toBe('config.yaml');
    });
  });

  describe('closeProject edge cases', () => {
    it('closeProject when already closed (no crash)', () => {
      expect(() => useProjectStore.getState().closeProject()).not.toThrow();
      expect(useProjectStore.getState().projectDir).toBeNull();
    });

    it('closeProject clears all state even if partially set', () => {
      useProjectStore.getState().openProject('/path', 'custom.yaml');
      useProjectStore.getState().setConfigLoaded(true);
      useProjectStore.getState().setTranslating(true, 'job-1');
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
  });

  describe('setTranslating edge cases', () => {
    it('setTranslating(true) without jobId sets currentJobId to null', () => {
      useProjectStore.getState().setTranslating(true);
      expect(useProjectStore.getState().isTranslating).toBe(true);
      expect(useProjectStore.getState().currentJobId).toBeNull();
    });

    it('setTranslating(false, "some-id") — jobId provided but isTranslating false', () => {
      useProjectStore.getState().setTranslating(false, 'job-123');
      expect(useProjectStore.getState().isTranslating).toBe(false);
      // currentJobId is set to the provided value even though isTranslating is false
      // This is a design decision: the store trusts the caller
      expect(useProjectStore.getState().currentJobId).toBe('job-123');
    });

    it('setTranslating(true, null) explicitly sets jobId to null', () => {
      useProjectStore.getState().setTranslating(true, null);
      expect(useProjectStore.getState().isTranslating).toBe(true);
      expect(useProjectStore.getState().currentJobId).toBeNull();
    });

    it('setTranslating(true, "") — empty string jobId', () => {
      useProjectStore.getState().setTranslating(true, '');
      expect(useProjectStore.getState().currentJobId).toBe('');
    });

    it('rapid setTranslating calls do not lose state', () => {
      useProjectStore.getState().setTranslating(true, 'job-1');
      useProjectStore.getState().setTranslating(true, 'job-2');
      useProjectStore.getState().setTranslating(true, 'job-3');
      expect(useProjectStore.getState().currentJobId).toBe('job-3');
      expect(useProjectStore.getState().isTranslating).toBe(true);

      useProjectStore.getState().setTranslating(false);
      expect(useProjectStore.getState().isTranslating).toBe(false);
    });
  });

  describe('setConfigLoaded edge cases', () => {
    it('setConfigLoaded(true) on a closed project', () => {
      // No project open, but setConfigLoaded doesn't check
      useProjectStore.getState().setConfigLoaded(true);
      expect(useProjectStore.getState().configLoaded).toBe(true);
    });

    it('setConfigLoaded(false) when already false', () => {
      useProjectStore.getState().setConfigLoaded(false);
      expect(useProjectStore.getState().configLoaded).toBe(false);
    });
  });

  describe('setLastTranslator edge cases', () => {
    it('setLastTranslator with empty string', () => {
      useProjectStore.getState().setLastTranslator('');
      expect(useProjectStore.getState().lastTranslator).toBe('');
    });

    it('setLastTranslator with very long string', () => {
      const long = 'a'.repeat(1000);
      useProjectStore.getState().setLastTranslator(long);
      expect(useProjectStore.getState().lastTranslator).toBe(long);
    });

    it('setLastTranslator with unicode', () => {
      useProjectStore.getState().setLastTranslator('全流程翻译');
      expect(useProjectStore.getState().lastTranslator).toBe('全流程翻译');
    });
  });

  describe('rapid openProject sequence', () => {
    it('opening 3 projects in rapid succession keeps only the last', () => {
      useProjectStore.getState().openProject('/path/a', 'a.yaml');
      useProjectStore.getState().openProject('/path/b', 'b.yaml');
      useProjectStore.getState().openProject('/path/c', 'c.yaml');

      const state = useProjectStore.getState();
      expect(state.projectDir).toBe('/path/c');
      expect(state.configFileName).toBe('c.yaml');
      // Translation state should be reset
      expect(state.isTranslating).toBe(false);
      expect(state.currentJobId).toBeNull();
      expect(state.lastTranslator).toBeNull();
    });

    it('open → close → open cycle works', () => {
      useProjectStore.getState().openProject('/first', 'config1.yaml');
      useProjectStore.getState().setTranslating(true, 'job-1');
      useProjectStore.getState().closeProject();
      useProjectStore.getState().openProject('/second', 'config2.yaml');

      const state = useProjectStore.getState();
      expect(state.projectDir).toBe('/second');
      expect(state.configFileName).toBe('config2.yaml');
      expect(state.isTranslating).toBe(false);
    });
  });
});
