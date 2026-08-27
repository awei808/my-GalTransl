import { onCleanup } from "solid-js";
import { toast } from "../stores/toastStore";
import { getErrorMessage } from "./errors";

/**
 * 页面卸载自动保存统一骨架
 *
 * 统一「切换页面 / 卸载组件时自动保存未落盘修改」的流程：
 * 1. 可选 waitForReady：等待在途保存/加载完成（避免并发双写；完成后重新判断 dirty）
 * 2. skip / isBusy：额外跳过条件
 * 3. isDirty：无未保存修改 → 完全静默（不落盘、不提示）
 * 4. 执行落盘：成功 → toast.info（短时长）；失败 → toast.error
 *
 * 注意：调用方必须用「挂载时快照」的身份（项目 id / 配置名 / 文件路径）落盘，
 * 因为卸载瞬间全局状态（activeProjectId/activeFilePath/dirtyFiles 等）可能已被
 * openProject/closeProject 重置，直接读全局会把旧项目数据写入错误目标。
 *
 * 若页面需要在卸载时先提交聚焦草稿（如 ReviewPage 的 textarea onBlur 语义），
 * 请在调用 runPageAutosave 之前先同步 (document.activeElement as HTMLElement | null)?.blur()。
 */
export interface PageAutosaveOptions {
  /** 卸载后先执行：等待在途保存/加载完成（内部应自带上限与超时兜底），完成后重新判断 isDirty。 */
  waitForReady?: () => Promise<void>;
  /** 是否有未保存修改。无修改时完全静默。 */
  isDirty: () => boolean;
  /** 是否处于加载/在途保存中（返回 true 则跳过本次，交由在途流程处理）。 */
  isBusy?: () => boolean;
  /** 额外跳过条件（如页面尚未加载完成）。 */
  skip?: () => boolean;
  /** 实际落盘动作，返回 true 表示确认落盘成功，false 表示失败。 */
  save: () => Promise<boolean>;
  /** 成功提示文案（缺省静默）。统一为 info 短时长。支持函数（保存目标在闭包内才可知）。 */
  successMessage?: string | (() => string);
  /** 失败提示前缀，如 "自动保存 xxx 失败"（缺省 "自动保存失败"）。支持函数。 */
  failMessage?: string | (() => string);
}

/** 自动保存 toast 统一短时长（info） */
export const AUTOSAVE_TOAST_DURATION = 3000;

/** 解析成功/失败文案（支持静态字符串或函数动态取值）。 */
function resolveMessage(msg: string | (() => string) | undefined, fallback: string): string {
  if (typeof msg === "function") return msg();
  return msg ?? fallback;
}

/** 自动保存成功提示（统一 info 短时长；msg 为空时静默）。 */
export function autosaveInfo(msg: string): void {
  if (!msg) return;
  toast.info(msg, AUTOSAVE_TOAST_DURATION);
}

/** 自动保存失败提示（统一 error；err 存在时拼接错误详情）。 */
export function autosaveError(failMessage: string, err?: unknown): void {
  if (err === undefined) {
    toast.error(failMessage);
  } else {
    toast.error(`${failMessage}：${getErrorMessage(err)}`);
  }
}

/**
 * 执行一次页面卸载自动保存（供已在 onCleanup 内自行安排调用顺序的页面使用）。
 *
 * 注意：本函数只对自身触发的 success/error toast 做 dispose 延后（setTimeout 宏任务）；
 * 调用方 save/isDirty 内部自行调用的 toast（如 toast.warning、页面 feedback）不享受该
 * 保护，组件卸载的 batch 上下文可能丢弃其 createStore 更新，请自行 setTimeout(fn, 0) 延后。
 */
export async function runPageAutosave(opts: PageAutosaveOptions): Promise<void> {
  await opts.waitForReady?.();
  if (opts.skip?.()) return;
  if (opts.isBusy?.()) return;
  if (!opts.isDirty()) return; // 无修改：不落盘、不提示
  try {
    const ok = await opts.save();
    if (ok && opts.successMessage) {
      const msg = resolveMessage(opts.successMessage, "已自动保存");
      if (!msg) return; // 成功文案为空：静默（如保存失败后卸载重试成功，避免与失败提示矛盾）
      // 延迟到宏任务：组件卸载（dispose）上下文中 SolidJS 的 batch 可能丢弃嵌套的
      // createStore 更新（setToasts），setTimeout 脱离该上下文后 toast 才能正常入列渲染
      setTimeout(() => {
        autosaveInfo(msg);
      }, 0);
    } else if (!ok) {
      autosaveError(resolveMessage(opts.failMessage, "自动保存失败"));
    }
  } catch (e) {
    autosaveError(resolveMessage(opts.failMessage, "自动保存失败"), e);
  }
}

/** 注册页面卸载自动保存（onCleanup 触发；适合无额外卸载清理逻辑的简单页面）。 */
export function usePageAutosave(opts: PageAutosaveOptions): void {
  onCleanup(() => {
    void runPageAutosave(opts);
  });
}
