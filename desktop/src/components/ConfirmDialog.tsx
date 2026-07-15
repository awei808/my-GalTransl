import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useUIStore, type ConfirmDialogOptions } from '../stores';
import { Icon } from './icons';

export { type ConfirmDialogOptions };

/**
 * ConfirmDialog — a centered modal dialog for confirming destructive or
 * irreversible operations.
 *
 * API: Call `useUIStore().confirm({ title, message })` from anywhere
 * in the app. The dialog renders via portal to document.body and
 * resolves the promise with `true` (confirm) or `false` (cancel).
 *
 * Keyboard:
 * - Enter → confirm
 * - Escape → cancel (if dismissible)
 *
 * Only one dialog can be shown at a time. A new `confirm()` call while
 * a dialog is visible will replace the existing one (the previous promise
 * resolves with `false`).
 */
export function ConfirmDialog() {
  const { confirmDialog, resolveConfirm } = useUIStore();
  const confirmBtnRef = useRef<HTMLButtonElement>(null);

  // Auto-focus the confirm button when the dialog appears
  useEffect(() => {
    if (confirmDialog.visible) {
      // Small delay to ensure the element is in the DOM
      requestAnimationFrame(() => {
        confirmBtnRef.current?.focus();
      });
    }
  }, [confirmDialog.visible]);

  // Keyboard handling
  useEffect(() => {
    if (!confirmDialog.visible) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        if (confirmDialog.dismissible !== false) {
          resolveConfirm(false);
        }
      } else if (e.key === 'Enter') {
        e.preventDefault();
        e.stopPropagation();
        resolveConfirm(true);
      }
    };

    // Capture phase to intercept before any other handlers
    document.addEventListener('keydown', handleKeyDown, true);
    return () => document.removeEventListener('keydown', handleKeyDown, true);
  }, [confirmDialog.visible, confirmDialog.dismissible, resolveConfirm]);

  if (!confirmDialog.visible) return null;

  const {
    title,
    message,
    confirmText = '确认',
    cancelText = '取消',
    dismissible = true,
    tone = 'primary',
  } = confirmDialog;

  const handleBackdropClick = () => {
    if (dismissible) {
      resolveConfirm(false);
    }
  };

  const handleContentClick = (e: React.MouseEvent) => {
    e.stopPropagation();
  };

  return createPortal(
    <div
      className="confirm-dialog__backdrop"
      onClick={handleBackdropClick}
      role="presentation"
    >
      <div
        className="confirm-dialog"
        onClick={handleContentClick}
        role="alertdialog"
        aria-modal="true"
        aria-label={title}
        aria-describedby="confirm-dialog-message"
      >
        <div className="confirm-dialog__header">
          <h2 className="confirm-dialog__title">{title}</h2>
          {dismissible && (
            <button
              type="button"
              className="confirm-dialog__close"
              onClick={() => resolveConfirm(false)}
              aria-label="关闭"
            >
              <Icon name="x" size={18} />
            </button>
          )}
        </div>
        <p id="confirm-dialog-message" className="confirm-dialog__message">
          {message}
        </p>
        <div className="confirm-dialog__actions">
          <button
            type="button"
            className="confirm-dialog__btn confirm-dialog__btn--cancel"
            onClick={() => resolveConfirm(false)}
          >
            {cancelText}
          </button>
          <button
            type="button"
            ref={confirmBtnRef}
            className={`confirm-dialog__btn confirm-dialog__btn--confirm confirm-dialog__btn--${tone}`}
            onClick={() => resolveConfirm(true)}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
