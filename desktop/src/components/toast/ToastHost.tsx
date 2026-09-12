import { For, Show, Portal } from "solid-js/web";
import { getToastItems, toast, ToastEntry } from "../../stores/toastStore";
import { Icon } from "../icons";
import { themeVivid } from "../../lib/theme";

function ToastItem(props: { entry: ToastEntry }) {
  const toneClass = () => `toast-item toast--${props.entry.tone}`;

  // 自动消失定时器
  const timer = setTimeout(() => {
    toast.dismiss(props.entry.id);
  }, props.entry.duration);

  return (
    <div class={toneClass()} role="alert">
      <div class="toast-icon">
        <Icon name={`tone-${props.entry.tone}`} size={18} strokeWidth={2} />
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
        onClick={() => {
          clearTimeout(timer);
          toast.dismiss(props.entry.id);
        }}
        aria-label="关闭"
      >
        {themeVivid() ? (
          <span class="icon-unicode" aria-hidden="true">✕</span>
        ) : (
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
          >
            <line x1="6" y1="6" x2="18" y2="18" />
            <line x1="18" y1="6" x2="6" y2="18" />
          </svg>
        )}
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
