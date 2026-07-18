import { For, Show, Portal } from "solid-js/web";
import { getToastItems, toast, ToastEntry } from "../../stores/toastStore";

function ToastItem(props: { entry: ToastEntry }) {
  const toneClass = () => `toast-item toast--${props.entry.tone}`;

  return (
    <div class={toneClass()}>
      <div class="toast-message">
        {props.entry.allowHtml ? (
          <div innerHTML={props.entry.message} />
        ) : (
          <span>{props.entry.message}</span>
        )}
      </div>
      <button
        class="toast-close"
        onClick={() => toast.dismiss(props.entry.id)}
        aria-label="关闭"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10" />
          <line x1="8" y1="8" x2="16" y2="16" />
          <line x1="16" y1="8" x2="8" y2="16" />
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
