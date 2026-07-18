import { createStore } from "solid-js/store";

export type ToastTone = "error" | "warning" | "success" | "info";

export interface ToastEntry {
  id: string;
  message: string;
  tone: ToastTone;
  duration: number;
  allowHtml?: boolean;
  createdAt: number;
}

const [toasts, setToasts] = createStore<{ items: ToastEntry[] }>({
  items: [],
});

const MAX_TOASTS = 5;
let counter = 0;
function uid() {
  return `t${Date.now()}-${++counter}`;
}

export const toast = {
  show(
    message: string,
    opts?: { tone?: ToastTone; duration?: number; allowHtml?: boolean }
  ): string {
    const id = uid();
    const entry: ToastEntry = {
      id,
      message,
      tone: opts?.tone ?? "info",
      duration: opts?.duration ?? 6000,
      allowHtml: opts?.allowHtml ?? false,
      createdAt: Date.now(),
    };
    setToasts("items", (items) => {
      const next = [...items, entry];
      if (next.length > MAX_TOASTS) next.shift();
      return next;
    });
    return id;
  },

  dismiss(id: string) {
    setToasts("items", (items) => items.filter((t) => t.id !== id));
  },

  success(msg: string, duration?: number) {
    return toast.show(msg, { tone: "success", duration });
  },
  error(msg: string, duration?: number) {
    return toast.show(msg, { tone: "error", duration });
  },
  warning(msg: string, duration?: number) {
    return toast.show(msg, { tone: "warning", duration });
  },
  info(msg: string, duration?: number) {
    return toast.show(msg, { tone: "info", duration });
  },
};

export function getToastItems() {
  return toasts.items;
}
