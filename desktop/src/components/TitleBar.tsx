import { createSignal, onMount, onCleanup, For, Show } from "solid-js";
import { appState, navigateTo, setAppState, openProject, closeProject } from "../stores/appStore";
import { toast } from "../stores/toastStore";
import { open } from "@tauri-apps/plugin-dialog";
import { getCurrentWebviewWindow } from "@tauri-apps/api/webviewWindow";
import { ensureDesktopBackendReady, encodeProjectDir, isBackendReachable } from "../lib/api/client";
import { fetchProjectFiles } from "../lib/api/project";

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

// ── 异步操作实现 ──

async function handleOpenProject() {
  const dir = await open({ directory: true });
  if (!dir) return;

  const selectedPath = typeof dir === "string" ? dir.replace(/\//g, "\\") : dir;

  // 仅当后端确实不可达时才提示“正在启动”，避免对已连接后端误报
  let needsStart = true;
  try {
    needsStart = !(await isBackendReachable(800));
  } catch {
    needsStart = true;
  }
  if (needsStart) toast.info("正在启动后端服务...");
  try {
    await ensureDesktopBackendReady({ timeoutMs: 30000 });
    setAppState({ connectionPhase: "online", backendOnline: true });
  } catch {
    toast.error("无法启动后端服务，请手动运行 run_backend.py");
    setAppState({ connectionPhase: "offline", backendOnline: false });
    // 继续尝试
  }

  const projectId = encodeProjectDir(selectedPath);
  try {
    await fetchProjectFiles(projectId);
  } catch {
    toast.error("所选目录不是有效的 GalTransl 项目");
    return;
  }

  openProject(projectId);
  window.__addRecentProject?.(selectedPath);
  toast.success("项目已打开");
}

async function handleCloseProject() {
  closeProject();
  toast.info("项目已关闭");
  // 注意：关闭项目不会停止共享后端，不要因此把连接状态置为离线
}

async function handleExit() {
  try {
    await getCurrentWebviewWindow().close();
  } catch {
    // fallback
    window.close();
  }
}

const menus: MenuDef[] = [
  {
    label: "文件",
    items: [
      { label: "新建项目", action: () => navigateTo("new-project") },
      {
        label: "打开项目",
        action: () => {
          void handleOpenProject();
        },
      },
      {
        label: "关闭项目",
        disabled: () => !appState.activeProjectId,
        action: () => {
          void handleCloseProject();
        },
      },
      { label: "", separator: true },
      {
        label: "退出",
        action: () => {
          void handleExit();
        },
      },
    ],
  },
  {
    label: "编辑",
    items: [
      {
        label: "撤销",
        shortcut: "Ctrl+Z",
        action: () => document.dispatchEvent(new CustomEvent("galtransl:undo")),
      },
      {
        label: "重做",
        shortcut: "Ctrl+Y",
        action: () => document.dispatchEvent(new CustomEvent("galtransl:redo")),
      },
      { label: "", separator: true },
      { label: "剪切", shortcut: "Ctrl+X", action: () => {} },
      { label: "复制", shortcut: "Ctrl+C", action: () => {} },
      { label: "粘贴", shortcut: "Ctrl+V", action: () => {} },
      { label: "", separator: true },
      {
        label: "文件内查找",
        shortcut: "Ctrl+F",
        action: () => document.dispatchEvent(new CustomEvent("galtransl:find-in-file")),
      },
      {
        label: "文件夹替换",
        shortcut: "Ctrl+H",
        action: () => setAppState({ sidebarOpen: true, sidebarTab: "find" }),
      },
      { label: "", separator: true },
      {
        label: "保存",
        shortcut: "Ctrl+S",
        action: () => document.dispatchEvent(new CustomEvent("galtransl:save")),
      },
    ],
  },
  {
    label: "翻译",
    items: [
      {
        label: "启动流程",
        action: () => {
          if (appState.activeProjectId) navigateTo("translate");
        },
      },
      { label: "停止翻译", disabled: true, action: () => {} },
      { label: "", separator: true },
      { label: "打开日志", action: () => navigateTo("logs") },
      { label: "", separator: true },
      { label: "后端配置", action: () => navigateTo("backend-profiles") },
      { label: "提示词模板", action: () => navigateTo("prompt-templates") },
      { label: "插件管理", action: () => navigateTo("plugins") },
      { label: "", separator: true },
      {
        label: "项目配置",
        disabled: () => !appState.activeProjectId,
        action: () => navigateTo("project-config"),
      },
    ],
  },
  {
    label: "视图",
    items: [
      {
        label: "切换侧栏",
        shortcut: "Ctrl+B",
        action: () => {
          setAppState("sidebarOpen", (s: boolean) => !s);
        },
      },
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
