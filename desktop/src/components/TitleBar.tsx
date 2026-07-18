import { createSignal, onMount, onCleanup, For, Show } from "solid-js";
import { appState, navigateTo, setAppState } from "../stores/appStore";

interface MenuItem {
  label: string;
  shortcut?: string;
  disabled?: boolean | (() => boolean);
  separator?: boolean;
  action?: () => void;
}

interface MenuDef {
  label: string;
  items: MenuItem[];
}

const menus: MenuDef[] = [
  {
    label: "文件",
    items: [
      { label: "新建项目", action: () => navigateTo("new-project") },
      { label: "打开项目", action: () => {} },
      { label: "关闭项目", disabled: () => !appState.activeProjectId, action: () => {} },
      { label: "", separator: true },
      { label: "退出", action: () => {} },
    ],
  },
  {
    label: "编辑",
    items: [
      { label: "撤销", shortcut: "Ctrl+Z", action: () => {} },
      { label: "重做", shortcut: "Ctrl+Y", action: () => {} },
      { label: "", separator: true },
      { label: "剪切", shortcut: "Ctrl+X", action: () => {} },
      { label: "复制", shortcut: "Ctrl+C", action: () => {} },
      { label: "粘贴", shortcut: "Ctrl+V", action: () => {} },
      { label: "", separator: true },
      { label: "查找", shortcut: "Ctrl+F", action: () => {} },
      { label: "替换", shortcut: "Ctrl+H", action: () => {} },
    ],
  },
  {
    label: "翻译",
    items: [
      { label: "启动流程", action: () => {
        if (appState.activeProjectId) navigateTo("translate");
      } },
      { label: "停止翻译", disabled: true, action: () => {} },
      { label: "", separator: true },
      { label: "打开日志", action: () => {} },
    ],
  },
  {
    label: "视图",
    items: [
      { label: "切换侧栏", shortcut: "Ctrl+B", action: () => { setAppState('sidebarOpen', s => !s); } },
      { label: "", separator: true },
      { label: "翻译控制台", action: () => navigateTo("translate") },
      { label: "校对审核", action: () => navigateTo("review") },
      { label: "设置", action: () => navigateTo("settings") },
    ],
  },
  {
    label: "帮助",
    items: [
      { label: "关于 GalTransl", action: () => {} },
      { label: "项目地址", action: () => {} },
      { label: "翻译指南", action: () => {} },
    ],
  },
];

export function TitleBar() {
  const [openMenu, setOpenMenu] = createSignal<number | null>(null);

  function handleClickOutside(e: MouseEvent) {
    const target = e.target as HTMLElement;
    if (!target.closest(".titlebar-menu")) {
      setOpenMenu(null);
    }
  }

  onMount(() => {
    document.addEventListener("click", handleClickOutside);
  });

  onCleanup(() => {
    document.removeEventListener("click", handleClickOutside);
  });

  function toggleMenu(index: number) {
    setOpenMenu(openMenu() === index ? null : index);
  }

  function handleItemClick(item: MenuItem) {
    if (itemDisabled(item) || item.separator) return;
    setOpenMenu(null);
    item.action?.();
  }

  function itemDisabled(item: MenuItem): boolean {
    return typeof item.disabled === "function" ? item.disabled() : !!item.disabled;
  }

  return (
    <header class="titlebar">
      <nav class="titlebar-menu">
        <For each={menus}>
          {(menu, i) => (
            <div class="titlebar-menuitem-wrapper">
              <div
                class={`titlebar-menuitem ${openMenu() === i() ? "open" : ""}`}
                onClick={() => toggleMenu(i())}
                role="menubar"
              >
                {menu.label}
              </div>
              <Show when={openMenu() === i()}>
                <div class="titlebar-dropdown" role="menu">
                  <For each={menu.items}>
                    {(item) =>
                      item.separator ? (
                        <div class="titlebar-dropdown-separator" />
                      ) : (
                        <div
                          class={`titlebar-dropdown-item ${itemDisabled(item) ? "disabled" : ""}`}
                          onClick={() => handleItemClick(item)}
                          role="menuitem"
                        >
                          <span>{item.label}</span>
                          <Show when={item.shortcut}>
                            <span class="shortcut">{item.shortcut}</span>
                          </Show>
                        </div>
                      )
                    }
                  </For>
                </div>
              </Show>
            </div>
          )}
        </For>
      </nav>
      <div class="titlebar-title">GalTransl Desktop</div>
    </header>
  );
}
