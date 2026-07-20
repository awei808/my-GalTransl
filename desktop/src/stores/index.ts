// SolidJS stores — new frontend
export {
  appState,
  setAppState,
  navigateTo,
  openProject,
  closeProject,
  markDirty,
  markClean,
} from "./appStore";
export type { ActiveView, ConnectionPhase, SidebarTab, AppState } from "./appStore";

export { toast } from "./toastStore";
export type { ToastTone, ToastEntry } from "./toastStore";

export { confirm } from "./confirmStore";
export type { ConfirmOptions, ConfirmResult } from "./confirmStore";

export { pushUndo, undo, redo, clearUndo, getUndoState } from "./undoStore";
export type { UndoEntry } from "./undoStore";
