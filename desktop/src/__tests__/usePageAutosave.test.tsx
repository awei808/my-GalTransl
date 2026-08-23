/**
 * usePageAutosave 卸载自动保存骨架验证
 *
 * 覆盖统一自动保存的核心语义：
 *   1. 无未保存修改 → 不落盘、不提示（完全静默）
 *   2. 成功 → toast.info（统一短时长）
 *   3. 失败（返回 false / 抛错）→ toast.error
 *   4. isBusy / skip → 跳过本次
 *   5. waitForReady 在 dirty 判断之前执行
 *   6. usePageAutosave 在组件卸载（dispose）时触发
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { Mock, MockInstance } from "vitest";
import { createRoot } from "solid-js";
import { toast } from "../stores/toastStore";
import { runPageAutosave, usePageAutosave } from "../lib/usePageAutosave";
import type { PageAutosaveOptions } from "../lib/usePageAutosave";

/** 等待所有已排队的宏任务（setTimeout 0）执行，用于断言延迟 toast 后的结果。 */
async function flushTimers(): Promise<void> {
  await new Promise((r) => setTimeout(r, 10));
}

describe("runPageAutosave", () => {
  let infoSpy: MockInstance<(msg: string, duration?: number) => string>;
  let errorSpy: MockInstance<(msg: string, duration?: number) => string>;

  beforeEach(() => {
    infoSpy = vi.spyOn(toast, "info").mockImplementation(() => "t");
    errorSpy = vi.spyOn(toast, "error").mockImplementation(() => "t");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function makeOpts(overrides: Partial<PageAutosaveOptions> = {}): PageAutosaveOptions & {
    save: Mock;
  } {
    const save = vi.fn().mockResolvedValue(true);
    return {
      isDirty: () => true,
      save,
      ...overrides,
    } as PageAutosaveOptions & { save: Mock };
  }

  it("无未保存修改时完全不落盘、不提示", async () => {
    const opts = makeOpts({ isDirty: () => false, successMessage: "已自动保存 x" });
    await runPageAutosave(opts);
    await flushTimers();
    expect(opts.save).not.toHaveBeenCalled();
    expect(infoSpy).not.toHaveBeenCalled();
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it("保存成功且有 successMessage → toast.info（短时长 3000）", async () => {
    const opts = makeOpts({ successMessage: "已自动保存 x" });
    await runPageAutosave(opts);
    await flushTimers(); // 等待 toast 延迟到宏任务执行
    expect(opts.save).toHaveBeenCalledTimes(1);
    expect(infoSpy).toHaveBeenCalledWith("已自动保存 x", 3000);
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it("保存成功但未配置 successMessage → 静默（不提示）", async () => {
    const opts = makeOpts({});
    await runPageAutosave(opts);
    await flushTimers();
    expect(opts.save).toHaveBeenCalledTimes(1);
    expect(infoSpy).not.toHaveBeenCalled();
    expect(errorSpy).not.toHaveBeenCalled();
  });

  it("save 返回 false → toast.error（failMessage）", async () => {
    const opts = makeOpts({
      save: vi.fn().mockResolvedValue(false),
      failMessage: "自动保存 x 失败",
    });
    await runPageAutosave(opts);
    await flushTimers();
    expect(errorSpy).toHaveBeenCalledWith("自动保存 x 失败");
  });

  it("save 返回 false 且未配置 failMessage → 默认文案", async () => {
    const opts = makeOpts({ save: vi.fn().mockResolvedValue(false) });
    await runPageAutosave(opts);
    await flushTimers();
    expect(errorSpy).toHaveBeenCalledWith("自动保存失败");
  });

  it("save 抛错 → toast.error 附带错误信息", async () => {
    const opts = makeOpts({ save: vi.fn().mockRejectedValue(new Error("磁盘写入失败")) });
    await runPageAutosave(opts);
    await flushTimers();
    expect(errorSpy).toHaveBeenCalledWith("自动保存失败：磁盘写入失败");
  });

  it("successMessage/failMessage 支持函数动态取值", async () => {
    const opts = makeOpts({
      successMessage: () => "已自动保存 a.txt",
      failMessage: () => "自动保存 a.txt 失败",
    });
    await runPageAutosave(opts);
    await flushTimers();
    expect(infoSpy).toHaveBeenCalledWith("已自动保存 a.txt", 3000);

    const opts2 = makeOpts({
      save: vi.fn().mockResolvedValue(false),
      failMessage: () => "自动保存 a.txt 失败",
    });
    await runPageAutosave(opts2);
    expect(errorSpy).toHaveBeenCalledWith("自动保存 a.txt 失败");
  });

  it("isBusy 为 true → 跳过本次保存", async () => {
    const opts = makeOpts({ isBusy: () => true, successMessage: "已自动保存 x" });
    await runPageAutosave(opts);
    expect(opts.save).not.toHaveBeenCalled();
    expect(infoSpy).not.toHaveBeenCalled();
  });

  it("skip 为 true → 跳过本次保存", async () => {
    const opts = makeOpts({ skip: () => true, successMessage: "已自动保存 x" });
    await runPageAutosave(opts);
    expect(opts.save).not.toHaveBeenCalled();
  });

  it("waitForReady 先于 dirty 判断与 save 执行", async () => {
    const order: string[] = [];
    const opts = makeOpts({
      waitForReady: async () => {
        order.push("wait");
        await Promise.resolve();
      },
      isDirty: () => {
        order.push("dirty");
        return true;
      },
      save: vi.fn(async () => {
        order.push("save");
        return true;
      }),
      successMessage: "已自动保存 x",
    });
    await runPageAutosave(opts);
    expect(order).toEqual(["wait", "dirty", "save"]);
  });
});

describe("usePageAutosave（onCleanup 触发）", () => {
  let infoSpy: MockInstance<(msg: string, duration?: number) => string>;

  beforeEach(() => {
    infoSpy = vi.spyOn(toast, "info").mockImplementation(() => "t");
  });

  afterEach(async () => {
    // 清空可能残留的延迟 toast 回调（setTimeout 泄漏），避免污染下一个用例
    await flushTimers();
    vi.restoreAllMocks();
  });

  it("组件卸载（dispose）时执行保存并提示", async () => {
    const save = vi.fn().mockResolvedValue(true);
    let dispose!: () => void;
    createRoot((d) => {
      dispose = d;
      usePageAutosave({
        isDirty: () => true,
        save,
        successMessage: "已自动保存 x",
      });
    });
    expect(save).not.toHaveBeenCalled();
    dispose();
    // 等待 onCleanup 内的异步保存完成（save await 链 + toast 延迟宏任务）
    await flushTimers();
    expect(save).toHaveBeenCalledTimes(1);
    expect(infoSpy).toHaveBeenCalledWith("已自动保存 x", 3000);
  });

  it("无未保存修改时卸载不提示", async () => {
    const save = vi.fn().mockResolvedValue(true);
    let dispose!: () => void;
    createRoot((d) => {
      dispose = d;
      usePageAutosave({ isDirty: () => false, save, successMessage: "已自动保存 x" });
    });
    dispose();
    await flushTimers();
    expect(save).not.toHaveBeenCalled();
    expect(infoSpy).not.toHaveBeenCalled();
  });
});
