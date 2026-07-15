import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { useUIStore } from '../../stores';
import { ConfirmDialog } from '../ConfirmDialog';

describe('ConfirmDialog component', () => {
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

  it('renders nothing when not visible', () => {
    render(<ConfirmDialog />);
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('renders title and message when visible', () => {
    useUIStore.setState({
      confirmDialog: {
        visible: true,
        title: '确认删除',
        message: '此操作不可撤销',
        confirmText: '删除',
        cancelText: '保留',
        dismissible: true,
        tone: 'danger',
        resolve: null,
      },
    });

    render(<ConfirmDialog />);
    expect(screen.getByText('确认删除')).toBeTruthy();
    expect(screen.getByText('此操作不可撤销')).toBeTruthy();
    expect(screen.getByText('删除')).toBeTruthy();
    expect(screen.getByText('保留')).toBeTruthy();
  });

  it('clicking confirm button resolves with true', async () => {
    const promise = useUIStore.getState().confirm({
      title: 'Test',
      message: 'Msg',
    });
    const onResolve = vi.fn();
    promise.then(onResolve);

    render(<ConfirmDialog />);
    expect(useUIStore.getState().confirmDialog.visible).toBe(true);

    fireEvent.click(screen.getByText('确认'));

    await waitFor(() => expect(onResolve).toHaveBeenCalledWith(true));
    expect(useUIStore.getState().confirmDialog.visible).toBe(false);
  });

  it('clicking cancel button resolves with false', async () => {
    const promise = useUIStore.getState().confirm({
      title: 'Test',
      message: 'Msg',
    });
    const onResolve = vi.fn();
    promise.then(onResolve);

    render(<ConfirmDialog />);

    fireEvent.click(screen.getByText('取消'));

    await waitFor(() => expect(onResolve).toHaveBeenCalledWith(false));
    expect(useUIStore.getState().confirmDialog.visible).toBe(false);
  });

  it('clicking backdrop dismisses when dismissible', async () => {
    const promise = useUIStore.getState().confirm({
      title: 'Test',
      message: 'Msg',
      dismissible: true,
    });
    const onResolve = vi.fn();
    promise.then(onResolve);

    render(<ConfirmDialog />);

    const backdrop = document.querySelector('.confirm-dialog__backdrop');
    expect(backdrop).toBeTruthy();
    fireEvent.click(backdrop!);

    await waitFor(() => expect(onResolve).toHaveBeenCalledWith(false));
  });

  it('backdrop click does nothing when not dismissible', () => {
    useUIStore.getState().confirm({
      title: 'Blocked',
      message: 'Must choose',
      dismissible: false,
    });

    render(<ConfirmDialog />);

    const backdrop = document.querySelector('.confirm-dialog__backdrop');
    fireEvent.click(backdrop!);

    expect(useUIStore.getState().confirmDialog.visible).toBe(true);
  });

  it('Escape key cancels when dismissible', async () => {
    const promise = useUIStore.getState().confirm({
      title: 'Test',
      message: 'Msg',
      dismissible: true,
    });
    const onResolve = vi.fn();
    promise.then(onResolve);

    render(<ConfirmDialog />);

    fireEvent.keyDown(document, { key: 'Escape' });

    await waitFor(() => expect(onResolve).toHaveBeenCalledWith(false));
    expect(useUIStore.getState().confirmDialog.visible).toBe(false);
  });

  it('Escape key does nothing when not dismissible', () => {
    useUIStore.getState().confirm({
      title: 'Blocked',
      message: 'Must choose',
      dismissible: false,
    });

    render(<ConfirmDialog />);

    fireEvent.keyDown(document, { key: 'Escape' });

    expect(useUIStore.getState().confirmDialog.visible).toBe(true);
  });

  it('Enter key confirms', async () => {
    const promise = useUIStore.getState().confirm({
      title: 'Test',
      message: 'Msg',
    });
    const onResolve = vi.fn();
    promise.then(onResolve);

    render(<ConfirmDialog />);

    fireEvent.keyDown(document, { key: 'Enter' });

    await waitFor(() => expect(onResolve).toHaveBeenCalledWith(true));
    expect(useUIStore.getState().confirmDialog.visible).toBe(false);
  });

  it('renders close button when dismissible', () => {
    useUIStore.getState().confirm({
      title: 'Test',
      message: 'Msg',
      dismissible: true,
    });

    render(<ConfirmDialog />);

    const closeBtn = screen.getByLabelText('关闭');
    expect(closeBtn).toBeTruthy();
  });

  it('does not render close button when not dismissible', () => {
    useUIStore.getState().confirm({
      title: 'Test',
      message: 'Msg',
      dismissible: false,
    });

    render(<ConfirmDialog />);

    expect(screen.queryByLabelText('关闭')).toBeNull();
  });
});
