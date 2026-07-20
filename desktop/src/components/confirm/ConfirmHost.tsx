import { Show, createSignal, onMount, onCleanup } from "solid-js";
import { Portal } from "solid-js/web";
import { confirm, getConfirmState } from "../../stores/confirmStore";

export function ConfirmHost() {
  const state = getConfirmState;
  const [inputValue, setInputValue] = createSignal("");

  let confirmBtnRef: HTMLButtonElement | undefined;

  function handleConfirm() {
    const val = state().options?.inputLabel ? inputValue() : undefined;
    confirm.resolve(true, val);
  }

  function handleCancel() {
    confirm.resolve(false);
  }

  function handleOverlayClick(e: MouseEvent) {
    if (e.target !== e.currentTarget) return;
    const dismissible = state().options?.dismissible ?? true;
    if (dismissible) handleCancel();
  }

  function handleKeyDown(e: KeyboardEvent) {
    if (e.key === "Enter") {
      e.preventDefault();
      e.stopPropagation();
      // 如果显示输入框且有值，才允许 Enter 确认
      if (state().options?.inputLabel && !inputValue()) return;
      handleConfirm();
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      const dismissible = state().options?.dismissible ?? true;
      if (dismissible) handleCancel();
    }
  }

  onMount(() => {
    document.addEventListener("keydown", handleKeyDown);
    setTimeout(() => confirmBtnRef?.focus(), 50);
  });

  onCleanup(() => {
    document.removeEventListener("keydown", handleKeyDown);
  });

  const toneClass = () => {
    const t = state().options?.tone ?? "info";
    return `btn--${t}`;
  };

  return (
    <Portal>
      <Show when={state().visible}>
        <div
          class="confirm-overlay"
          style={{ opacity: state().visible ? 1 : 0 }}
          onClick={handleOverlayClick}
        />
        <div class="confirm-dialog confirm-dialog--enter" role="dialog" aria-modal="true">
          <div class="confirm-header">
            <h2 class="confirm-title">{state().options?.title ?? ""}</h2>
          </div>
          <div class="confirm-body">
            <Show
              when={state().options?.html}
              fallback={<p class="confirm-message">{state().options?.message ?? ""}</p>}
            >
              <div innerHTML={state().options!.html!} />
            </Show>
            <Show when={state().options?.inputLabel}>
              <div class="confirm-input-group">
                <label class="confirm-input-label">{state().options!.inputLabel}</label>
                <input
                  class="confirm-input"
                  type="text"
                  placeholder={state().options?.inputPlaceholder}
                  value={inputValue()}
                  onInput={(e) => setInputValue(e.currentTarget.value)}
                />
              </div>
            </Show>
          </div>
          <div class="confirm-footer">
            <button class="btn btn--cancel" onClick={handleCancel}>
              {state().options?.cancelText ?? "取消"}
            </button>
            <button ref={confirmBtnRef} class={`btn ${toneClass()}`} onClick={handleConfirm}>
              {state().options?.confirmText ?? "确认"}
            </button>
          </div>
        </div>
      </Show>
    </Portal>
  );
}
