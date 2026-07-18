import { Show, createSignal, onMount, onCleanup } from "solid-js";
import { Portal } from "solid-js/web";
import {
  confirm,
  getConfirmState,
} from "../../stores/confirmStore";

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

  function handleKeyDown(e: KeyboardEvent) {
    if (e.key === "Enter") {
      e.preventDefault();
      e.stopPropagation();
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
    // 聚焦确认按钮
    setTimeout(() => confirmBtnRef?.focus(), 50);
  });

  onCleanup(() => {
    document.removeEventListener("keydown", handleKeyDown);
  });

  return (
    <Portal>
      <Show when={state().visible}>
        <div class="confirm-overlay" onClick={handleCancel} />
        <div class="confirm-dialog" role="dialog" aria-modal="true">
          <div class="confirm-header">
            <h2 class="confirm-title">{state().options?.title ?? ""}</h2>
          </div>
          <div class="confirm-body">
            {state().options?.html ? (
              <div innerHTML={state().options!.html!} />
            ) : (
              <p>{state().options?.message ?? ""}</p>
            )}
            {state().options?.inputLabel && (
              <div class="confirm-input-group">
                <label class="confirm-input-label">
                  {state().options!.inputLabel}
                </label>
                <input
                  class="confirm-input"
                  type="text"
                  placeholder={state().options?.inputPlaceholder}
                  value={inputValue()}
                  onInput={(e) => setInputValue(e.currentTarget.value)}
                />
              </div>
            )}
          </div>
          <div class="confirm-footer">
            <button class="btn btn--cancel" onClick={handleCancel}>
              {state().options?.cancelText ?? "取消"}
            </button>
            <button
              ref={confirmBtnRef}
              class={`btn btn--${state().options?.tone ?? "info"}`}
              onClick={handleConfirm}
            >
              {state().options?.confirmText ?? "确认"}
            </button>
          </div>
        </div>
      </Show>
    </Portal>
  );
}
