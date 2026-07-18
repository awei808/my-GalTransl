import { For, Show, Portal } from "solid-js/web";
import {
  getToastItems,
  toast,
  ToastEntry,
} from "../../stores/toastStore";

function ToastItem(props: { entry: ToastEntry }) {
  const toneClass = () => `toast-item toast--${props.entry.tone}`;

  // 自动消失定时器
  const timer = setTimeout(() => {
    toast.dismiss(props.entry.id);
  }, props.entry.duration);

  return (
    <div class={toneClass()} role="alert">
      <div class="toast-icon">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <Show when={props.entry.tone === "success"}>
            <path d="M22 11.1V12a10 10 0 1 1-6-9.2" /><path d="M22 4 12 14.01l-3-3" />
          </Show>
          <Show when={props.entry.tone === "error"}>
            <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
          </Show>
          <Show when={props.entry.tone === "warning"}>
            <path d="M12 2 2 21h20L12 2Z" /><line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" />
          </Show>
          <Show when={props.entry.tone === "info"}>
            <circle cx="12" cy="12" r="10" /><line x1="12" y1="16" x2="12" y2="12" /><line x1="12" y1="8" x2="12.01" y2="8" />
          </Show>
        </svg>
      </div>
      <div class="toast-message">
        {props.entry.allowHtml ? (
          <div innerHTML={props.entry.message} />
        ) : (
          <span>{props.entry.message}</span>
        )}
      </div>
      <button
        class="toast-close"
        onClick={() => { clearTimeout(timer); toast.dismiss(props.entry.id); }}
        aria-label="关闭"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <line x1="6" y1="6" x2="18" y2="18" /><line x1="18" y1="6" x2="6" y2="18" />
        </svg>
      </button>
    </div>
  );
}

export function ToastHost() {
  const items = getToastItems;

  return (
    <Portal>
      <Show when={items().length > 0}>
        <div class="toast-host">
          <For each={items()}>{(entry) => <ToastItem entry={entry} />}</For>
        </div>
      </Show>
    </Portal>
  );
}
