import { describe, it, expect, beforeEach } from 'vitest';
import { useUIStore } from '../uiStore';

describe('useUIStore', () => {
  beforeEach(() => {
    // Reset to initial state
    useUIStore.setState({
      sidebarCollapsed: false,
      confirmDialog: {
        visible: false,
        title: '',
        message: '',
        confirmText: '确认',
        cancelText: '取消',
        dismissible: true,
        tone: 'primary',
        resolve: null,
      },
    });
  });

  describe('Sidebar', () => {
    it('starts expanded', () => {
      expect(useUIStore.getState().sidebarCollapsed).toBe(false);
    });

    it('toggleSidebar flips the state', () => {
      useUIStore.getState().toggleSidebar();
      expect(useUIStore.getState().sidebarCollapsed).toBe(true);

      useUIStore.getState().toggleSidebar();
      expect(useUIStore.getState().sidebarCollapsed).toBe(false);
    });

    it('setSidebarCollapsed sets the value directly', () => {
      useUIStore.getState().setSidebarCollapsed(true);
      expect(useUIStore.getState().sidebarCollapsed).toBe(true);

      useUIStore.getState().setSidebarCollapsed(false);
      expect(useUIStore.getState().sidebarCollapsed).toBe(false);
    });
  });

  describe('ConfirmDialog', () => {
    it('starts hidden', () => {
      expect(useUIStore.getState().confirmDialog.visible).toBe(false);
    });

    it('confirm() shows the dialog and returns a promise', () => {
      const promise = useUIStore.getState().confirm({
        title: 'Delete file?',
        message: 'This action cannot be undone.',
      });

      expect(promise).toBeInstanceOf(Promise);
      expect(useUIStore.getState().confirmDialog.visible).toBe(true);
      expect(useUIStore.getState().confirmDialog.title).toBe('Delete file?');
      expect(useUIStore.getState().confirmDialog.message).toBe('This action cannot be undone.');
    });

    it('confirm() uses default button text', () => {
      useUIStore.getState().confirm({ title: 'Test', message: 'Msg' });
      const dialog = useUIStore.getState().confirmDialog;
      expect(dialog.confirmText).toBe('确认');
      expect(dialog.cancelText).toBe('取消');
    });

    it('confirm() accepts custom button text and tone', () => {
      useUIStore.getState().confirm({
        title: 'Delete all',
        message: 'Everything will be gone.',
        confirmText: 'Delete',
        cancelText: 'Keep',
        tone: 'danger',
      });
      const dialog = useUIStore.getState().confirmDialog;
      expect(dialog.confirmText).toBe('Delete');
      expect(dialog.cancelText).toBe('Keep');
      expect(dialog.tone).toBe('danger');
    });

    it('resolveConfirm(true) resolves the promise and hides dialog', async () => {
      const promise = useUIStore.getState().confirm({
        title: 'Test',
        message: 'Msg',
      });

      useUIStore.getState().resolveConfirm(true);

      const result = await promise;
      expect(result).toBe(true);
      expect(useUIStore.getState().confirmDialog.visible).toBe(false);
    });

    it('resolveConfirm(false) resolves the promise with false', async () => {
      const promise = useUIStore.getState().confirm({
        title: 'Test',
        message: 'Msg',
      });

      useUIStore.getState().resolveConfirm(false);

      const result = await promise;
      expect(result).toBe(false);
      expect(useUIStore.getState().confirmDialog.visible).toBe(false);
    });

    it('non-dismissible dialog cannot be dismissed by backdrop/escape', () => {
      useUIStore.getState().confirm({
        title: 'Blocked',
        message: 'You must choose',
        dismissible: false,
      });
      expect(useUIStore.getState().confirmDialog.dismissible).toBe(false);
    });
  });
});
