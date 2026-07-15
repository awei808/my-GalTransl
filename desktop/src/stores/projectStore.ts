import { create } from 'zustand';

/**
 * Project Store — manages the single active translation project.
 *
 * In the refactored architecture, only one project is open at a time.
 * Switching projects is done by calling `openProject()` with a new directory,
 * which clears all previous project state.
 *
 * This store will replace the multi-project `openProjects: string[]` state
 * currently in App.tsx (migration happens in Stage 2).
 */

export interface ProjectState {
  /** Absolute path to the project directory, or null if no project is open */
  projectDir: string | null;
  /** Config file name (default: "config.yaml") */
  configFileName: string;
  /** Whether the project config has been loaded */
  configLoaded: boolean;
  /** Whether a translation job is currently running for this project */
  isTranslating: boolean;
  /** Current job ID if a job is running */
  currentJobId: string | null;
  /** Recently used translator for this project (persisted separately) */
  lastTranslator: string | null;

  // Actions
  openProject: (dir: string, configFileName?: string) => void;
  closeProject: () => void;
  setConfigLoaded: (loaded: boolean) => void;
  setTranslating: (isTranslating: boolean, jobId?: string | null) => void;
  setLastTranslator: (translator: string) => void;
}

export const useProjectStore = create<ProjectState>((set) => ({
  projectDir: null,
  configFileName: 'config.yaml',
  configLoaded: false,
  isTranslating: false,
  currentJobId: null,
  lastTranslator: null,

  openProject: (dir, configFileName = 'config.yaml') =>
    set({
      projectDir: dir,
      configFileName,
      configLoaded: false,
      isTranslating: false,
      currentJobId: null,
      lastTranslator: null,
    }),

  closeProject: () =>
    set({
      projectDir: null,
      configFileName: 'config.yaml',
      configLoaded: false,
      isTranslating: false,
      currentJobId: null,
      lastTranslator: null,
    }),

  setConfigLoaded: (loaded) => set({ configLoaded: loaded }),

  setTranslating: (isTranslating, jobId = null) =>
    set({ isTranslating, currentJobId: jobId }),

  setLastTranslator: (translator) => set({ lastTranslator: translator }),
}));
