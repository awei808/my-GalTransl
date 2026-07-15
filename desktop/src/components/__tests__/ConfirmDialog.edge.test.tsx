import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { useUIStore } from '../../stores';
import { ConfirmDialog } from '../ConfirmDialog';

describe('ConfirmDialog — edge cases', () => {
  beforeEach(() => {
    useUIStore.setState({
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

  describe('rendering with edge case content', () => {
    it('renders with empty title and message', () => {
      useUIStore.setState({
        confirmDialog: {
          visible: true,
          title: '',
          message: '',
          confirmText: 'OK',
          cancelText: 'Cancel',
          dismissible: true,
          tone: 'primary',
          resolve: null,
        },
      });

      render(<ConfirmDialog />);
      // Dialog should still render
      expect(screen.getByRole('alertdialog')).toBeTruthy();
      // Buttons should be present
      expect(screen.getByText('OK')).toBeTruthy();
      expect(screen.getByText('Cancel')).toBeTruthy();
    });

    it('renders title with HTML-like content (no XSS)', () => {
      useUIStore.setState({
        confirmDialog: {
          visible: true,
          title: '<b>bold</b>',
          message: '<img src=x onerror=alert(1)>',
          confirmText: 'OK',
          cancelText: 'Cancel',
          dismissible: true,
          tone: 'primary',
          resolve: null,
        },
      });

      render(<ConfirmDialog />);
      // React escapes HTML in text content — no actual <b> or <img> elements
      expect(screen.queryByRole('img')).toBeNull();
      // The title text is rendered as literal text
      expect(screen.getByText('<b>bold</b>')).toBeTruthy();
    });

    it('renders with very long message', () => {
      const longMsg = 'A'.repeat(5000);
      useUIStore.setState({
        confirmDialog: {
          visible: true,
          title: 'Test',
          message: longMsg,
          confirmText: 'OK',
          cancelText: 'Cancel',
          dismissible: true,
          tone: 'primary',
          resolve: null,
        },
      });

      render(<ConfirmDialog />);
      expect(screen.getByText(longMsg)).toBeTruthy();
    });

    it('renders message with newlines', () => {
      useUIStore.setState({
        confirmDialog: {
          visible: true,
          title: 'Test',
          message: 'Line 1\nLine 2\nLine 3',
          confirmText: 'OK',
          cancelText: 'Cancel',
          dismissible: true,
          tone: 'primary',
          resolve: null,
        },
      });

      render(<ConfirmDialog />);
      // Text with newlines is rendered — CSS handles display
      expect(screen.getByText(/Line 1/)).toBeTruthy();
    });
  });

  describe('tone variations', () => {
    it('applies primary tone class', () => {
      useUIStore.setState({
        confirmDialog: {
          visible: true,
          title: 'T', message: 'M',
          confirmText: 'OK', cancelText: 'Cancel',
          dismissible: true, tone: 'primary',
          resolve: null,
        },
      });

      render(<ConfirmDialog />);
      const confirmBtn = screen.getByText('OK');
      expect(confirmBtn.className).toContain('confirm-dialog__btn--primary');
    });

    it('applies danger tone class', () => {
      useUIStore.setState({
        confirmDialog: {
          visible: true,
          title: 'T', message: 'M',
          confirmText: 'Delete', cancelText: 'Keep',
          dismissible: true, tone: 'danger',
          resolve: null,
        },
      });

      render(<ConfirmDialog />);
      const confirmBtn = screen.getByText('Delete');
      expect(confirmBtn.className).toContain('confirm-dialog__btn--danger');
    });
  });

  describe('keyboard edge cases', () => {
    it('Tab key does not trigger confirm or cancel', async () => {
      const promise = useUIStore.getState().confirm({ title: 'T', message: 'M' });
      const onResolve = vi.fn();
      promise.then(onResolve);

      render(<ConfirmDialog />);

      fireEvent.keyDown(document, { key: 'Tab' });
      await new Promise((r) => setTimeout(r, 0));

      expect(onResolve).not.toHaveBeenCalled();
      expect(useUIStore.getState().confirmDialog.visible).toBe(true);
    });

    it('Space key does not trigger confirm or cancel', async () => {
      const promise = useUIStore.getState().confirm({ title: 'T', message: 'M' });
      const onResolve = vi.fn();
      promise.then(onResolve);

      render(<ConfirmDialog />);

      fireEvent.keyDown(document, { key: ' ' });
      await new Promise((r) => setTimeout(r, 0));

      expect(onResolve).not.toHaveBeenCalled();
    });

    it('Arrow keys do not trigger confirm or cancel', async () => {
      const promise = useUIStore.getState().confirm({ title: 'T', message: 'M' });
      const onResolve = vi.fn();
      promise.then(onResolve);

      render(<ConfirmDialog />);

      fireEvent.keyDown(document, { key: 'ArrowUp' });
      fireEvent.keyDown(document, { key: 'ArrowDown' });
      fireEvent.keyDown(document, { key: 'ArrowLeft' });
      fireEvent.keyDown(document, { key: 'ArrowRight' });
      await new Promise((r) => setTimeout(r, 0));

      expect(onResolve).not.toHaveBeenCalled();
    });

    it('rapid Enter key presses — only first resolves', async () => {
      const promise = useUIStore.getState().confirm({ title: 'T', message: 'M' });
      const onResolve = vi.fn();
      promise.then(onResolve);

      render(<ConfirmDialog />);

      fireEvent.keyDown(document, { key: 'Enter' });
      fireEvent.keyDown(document, { key: 'Enter' });
      fireEvent.keyDown(document, { key: 'Enter' });
      await new Promise((r) => setTimeout(r, 0));

      // Only the first should resolve — dialog is hidden after first
      expect(onResolve).toHaveBeenCalledTimes(1);
      expect(onResolve).toHaveBeenCalledWith(true);
    });

    it('rapid Escape key presses — only first resolves', async () => {
      const promise = useUIStore.getState().confirm({ title: 'T', message: 'M', dismissible: true });
      const onResolve = vi.fn();
      promise.then(onResolve);

      render(<ConfirmDialog />);

      fireEvent.keyDown(document, { key: 'Escape' });
      fireEvent.keyDown(document, { key: 'Escape' });
      await new Promise((r) => setTimeout(r, 0));

      expect(onResolve).toHaveBeenCalledTimes(1);
      expect(onResolve).toHaveBeenCalledWith(false);
    });

    it('Enter works even when dismissible is false', async () => {
      const promise = useUIStore.getState().confirm({
        title: 'T', message: 'M', dismissible: false,
      });
      const onResolve = vi.fn();
      promise.then(onResolve);

      render(<ConfirmDialog />);

      // Escape should NOT work
      fireEvent.keyDown(document, { key: 'Escape' });
      await new Promise((r) => setTimeout(r, 0));
      expect(onResolve).not.toHaveBeenCalled();
      expect(useUIStore.getState().confirmDialog.visible).toBe(true);

      // Enter SHOULD work even when not dismissible
      fireEvent.keyDown(document, { key: 'Enter' });
      await new Promise((r) => setTimeout(r, 0));
      expect(onResolve).toHaveBeenCalledWith(true);
    });
  });

  describe('click edge cases', () => {
    it('clicking inside dialog content does not dismiss', () => {
      useUIStore.getState().confirm({
        title: 'T', message: 'M', dismissible: true,
      });

      render(<ConfirmDialog />);

      // Click on the dialog content (not backdrop)
      const dialog = screen.getByRole('alertdialog');
      fireEvent.click(dialog);

      // Should still be visible
      expect(useUIStore.getState().confirmDialog.visible).toBe(true);
    });

    it('clicking confirm button when already resolving is safe', async () => {
      useUIStore.getState().confirm({ title: 'T', message: 'M' });

      render(<ConfirmDialog />);

      const confirmBtn = screen.getByText('确认');
      fireEvent.click(confirmBtn);
      // Dialog is now hidden
      expect(useUIStore.getState().confirmDialog.visible).toBe(false);

      // Click again — should not crash
      // The button is no longer in the DOM, so this is a no-op
      expect(() => fireEvent.click(confirmBtn)).not.toThrow();
    });

    it('clicking close button (x) resolves with false', async () => {
      const promise = useUIStore.getState().confirm({
        title: 'T', message: 'M', dismissible: true,
      });
      const onResolve = vi.fn();
      promise.then(onResolve);

      render(<ConfirmDialog />);

      const closeBtn = screen.getByLabelText('关闭');
      fireEvent.click(closeBtn);

      await waitFor(() => expect(onResolve).toHaveBeenCalledWith(false));
    });
  });

  describe('dialog visibility transitions', () => {
    it('dialog appears when visible goes from false to true', () => {
      const { rerender } = render(<ConfirmDialog />);
      expect(screen.queryByRole('alertdialog')).toBeNull();

      useUIStore.setState({
        confirmDialog: {
          visible: true, title: 'T', message: 'M',
          confirmText: 'OK', cancelText: 'Cancel',
          dismissible: true, tone: 'primary', resolve: null,
        },
      });

      rerender(<ConfirmDialog />);
      expect(screen.getByRole('alertdialog')).toBeTruthy();
    });

    it('dialog disappears when visible goes from true to false', () => {
      useUIStore.setState({
        confirmDialog: {
          visible: true, title: 'T', message: 'M',
          confirmText: 'OK', cancelText: 'Cancel',
          dismissible: true, tone: 'primary', resolve: null,
        },
      });

      const { rerender } = render(<ConfirmDialog />);
      expect(screen.getByRole('alertdialog')).toBeTruthy();

      useUIStore.setState({
        confirmDialog: {
          visible: false, title: '', message: '',
          confirmText: 'OK', cancelText: 'Cancel',
          dismissible: true, tone: 'primary', resolve: null,
        },
      });

      rerender(<ConfirmDialog />);
      expect(screen.queryByRole('alertdialog')).toBeNull();
    });
  });
});
