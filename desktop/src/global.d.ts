export {};

declare global {
  interface Window {
    /** 由主界面注入，供 Tauri 命令回调把最近项目写回前端（见 HomePage / TitleBar） */
    __addRecentProject?: (path: string) => void;
  }
}
