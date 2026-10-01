import { toast } from "../stores/toastStore";
import { getErrorMessage } from "./errors";

/**
 * 全局保存注册表：Ctrl+S（App）与标题栏菜单「保存」（TitleBar）的统一入口
 *
 * 页面挂载时注册自己的保存函数（save + 可选成功文案），invokeGlobalSave 查表调用；
 * 未注册视图给出「没有可保存的内容」提示，避免按键被吞后用户误以为已保存。
 *
 * 返回值口径：save 返回 true 表示已执行保存动作（成功与否由页面内部 toast 或
 * successMessage 反馈）；返回 false 表示静默（无修改 / 编辑器未打开 / 失败已由页面内部 toast）。
 */
export interface GlobalSaveEntry {
  save: () => Promise<boolean>;
  /** 保存成功时的统一提示；页面内部已自行 toast 的必须留空，避免双提示 */
  successMessage?: string | (() => string);
}

let current: GlobalSaveEntry | null = null;
let inFlight = false;

/** 注册当前视图的保存入口（后注册覆盖前者；组件体创建一次 entry 保证身份比对稳定） */
export function registerGlobalSave(entry: GlobalSaveEntry): void {
  current = entry;
}

/** 注销保存入口；身份比对避免视图切换乱序时误删后注册者的槽位 */
export function unregisterGlobalSave(entry: GlobalSaveEntry): void {
  if (current === entry) current = null;
}

/** 当前是否有视图注册了保存入口 */
export function hasGlobalSave(): boolean {
  return current !== null;
}

/** 触发一次全局保存（Ctrl+S / 菜单「保存」共用）；in-flight 期间忽略重复触发 */
export async function invokeGlobalSave(): Promise<void> {
  if (inFlight) return;
  const entry = current;
  if (!entry) {
    toast.info("当前页面没有可保存的内容");
    return;
  }
  inFlight = true;
  try {
    const ok = await entry.save();
    if (ok && entry.successMessage) {
      const msg =
        typeof entry.successMessage === "function" ? entry.successMessage() : entry.successMessage;
      if (msg) toast.success(msg);
    }
  } catch (e) {
    // 兜底：页面保存函数自身未捕获的异常在此报错，避免向上抛成 unhandled rejection
    toast.error(`保存失败：${getErrorMessage(e)}`);
  } finally {
    inFlight = false;
  }
}
