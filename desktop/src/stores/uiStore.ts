import { create } from 'zustand';

/**
 * UI Store — ephemeral UI state (sidebar, tabs, dialogs).
 *
 * This store is NOT persisted to localStorage — it resets on every app launch.
 * The only persisted UI state is the "last active tab per project" which is
 * handled separately by projectTabMemory.ts (or will be simplified in Stage 2).
 */

// ── ConfirmDialog types ──

export interface ConfirmDialogOptions {
  title: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  /** If true, clicking the backdrop or pressing Escape cancels. Default: true */
  dismissible?: boolean;
  /** If provided, sets the confirm button tone. Default: 'primary' */
  tone?: 'primary' | 'danger';
}

interface ConfirmDialogState extends ConfirmDialogOptions {
  visible: boolean;
  resolve: ((value: boolean) => void) | null;
}

// ── Store interface ──

export interface UIState {
  // Sidebar
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;

  // ConfirmDialog
  confirmDialog: ConfirmDialogState;

  /**
   * Show a confirmation dialog and return a promise that resolves to:
   * - `true` if the user clicks confirm
   * - `false` if the user clicks cancel, presses Escape, or clicks backdrop (if dismissible)
   */
  confirm: (options: ConfirmDialogOptions) => Promise<boolean>;

  /** Resolve the dialog with the given value (internal) */
  resolveConfirm: (value: boolean) => void;
}

const INITIAL_DIALOG: ConfirmDialogState = {
  visible: false,
  title: '',
  message: '',
  confirmText: '确认',
  cancelText: '取消',
  dismissible: true,
  tone: 'primary',
  resolve: null,
};

export const useUIStore = create<UIState>((set, get) => ({
  sidebarCollapsed: false,
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),

  confirmDialog: INITIAL_DIALOG,

  confirm: (options) => {
    return new Promise<boolean>((resolve) => {
      const current = get().confirmDialog;
      // If a dialog is already visible, resolve the previous promise with false
      // before replacing it — prevents orphaned promises.
      if (current.visible && current.resolve) {
        current.resolve(false);
      }
      set({
        confirmDialog: {
          ...INITIAL_DIALOG,
          ...options,
          visible: true,
          resolve,
        },
      });
    });
  },

  resolveConfirm: (value) => {
    const { confirmDialog } = get();
    confirmDialog.resolve?.(value);
    set({ confirmDialog: { ...INITIAL_DIALOG } });
  },
}));
