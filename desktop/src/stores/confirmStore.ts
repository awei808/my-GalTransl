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

const [confirmState, setConfirmState] = createStore<ConfirmState>({
  visible: false,
  options: null,
  resolve: null,
  animating: false,
});

export const confirm = {
  show(options: ConfirmOptions): Promise<ConfirmResult> {
    // 如果有活动的旧弹窗，先关闭它
    if (confirmState.resolve) {
      confirmState.resolve({ confirmed: false });
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
    if (confirmState.resolve) {
      confirmState.resolve({
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
  },
};

export function getConfirmState() {
  return confirmState;
}
