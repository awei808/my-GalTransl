import { createStore } from "solid-js/store";

export interface ConfirmOptions {
  title: string;
  message?: string;
  html?: string;
  inputLabel?: string;
  inputPlaceholder?: string;
  inputDefault?: string;
  confirmText?: string;
  cancelText?: string;
  /** 可选的第三个按钮（位于取消与确认之间），用于"取消操作并留在原处"等场景 */
  extraText?: string;
  tone?: "danger" | "warning" | "info" | "default";
  dismissible?: boolean;
}

export type ConfirmAction = "confirm" | "cancel" | "extra";

export interface ConfirmResult {
  confirmed: boolean;
  action?: ConfirmAction;
  inputValue?: string;
}

interface ConfirmState {
  visible: boolean;
  options: ConfirmOptions | null;
  resolve: ((result: ConfirmResult) => void) | null;
  animating: boolean;
}

/** 排队等待的确认请求（活动弹窗关闭后依次展示） */
interface PendingConfirm {
  options: ConfirmOptions;
  resolve: (result: ConfirmResult) => void;
}

const [confirmState, setConfirmState] = createStore<ConfirmState>({
  visible: false,
  options: null,
  resolve: null,
  animating: false,
});

const pendingQueue: PendingConfirm[] = [];

export const confirm = {
  show(options: ConfirmOptions): Promise<ConfirmResult> {
    // 已有活动弹窗：排队等待，不再覆盖旧 promise——
    // 旧行为直接 resolve({confirmed:false}) 会被调用方当成"取消"，导致未保存修改被丢弃
    if (confirmState.resolve) {
      return new Promise<ConfirmResult>((resolve) => {
        pendingQueue.push({ options, resolve });
      });
    }

    return new Promise<ConfirmResult>((resolve) => {
      setConfirmState({
        visible: true,
        options,
        resolve,
        animating: false,
      });
    });
  },

  resolve(confirmed: boolean, inputValue?: string, action?: ConfirmAction) {
    // 重复 resolve（组件卸载兜底等）直接忽略，避免误跳队
    if (!confirmState.visible && !confirmState.resolve) return;
    const currentResolve = confirmState.resolve;
    if (currentResolve) {
      currentResolve({
        confirmed,
        inputValue,
        action: action ?? (confirmed ? "confirm" : "cancel"),
      });
    }
    setConfirmState({
      visible: false,
      resolve: null,
      animating: false,
    });
    // 展示下一个排队弹窗
    const next = pendingQueue.shift();
    if (next) {
      setConfirmState({
        visible: true,
        options: next.options,
        resolve: next.resolve,
        animating: false,
      });
    }
  },
};

export function getConfirmState() {
  return confirmState;
}
