/**
 * 全局保存注册表（globalSave）单元测试
 * 覆盖：注册/调用、successMessage（静态/函数/空串/false 静默）、未注册提示、
 * in-flight 重复触发忽略、异常兜底 toast、身份比对注销不误删后注册者。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../stores/toastStore", () => ({
  toast: { show: vi.fn(), info: vi.fn(), success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

import { toast } from "../stores/toastStore";
import {
  registerGlobalSave,
  unregisterGlobalSave,
  invokeGlobalSave,
  hasGlobalSave,
} from "../lib/globalSave";
import type { GlobalSaveEntry } from "../lib/globalSave";

const mockedToast = vi.mocked(toast);

function entry(partial: Partial<GlobalSaveEntry> = {}): GlobalSaveEntry {
  return { save: vi.fn(async () => true), ...partial };
}

beforeEach(() => {
  vi.clearAllMocks();
  // 注册哨兵再注销：覆盖并清空其他用例可能残留的模块级注册状态
  const sentinel = entry();
  registerGlobalSave(sentinel);
  unregisterGlobalSave(sentinel);
});

describe("invokeGlobalSave", () => {
  it("未注册视图：提示没有可保存的内容，不调用任何 save", async () => {
    await invokeGlobalSave();
    expect(mockedToast.info).toHaveBeenCalledWith("当前页面没有可保存的内容");
    expect(mockedToast.success).not.toHaveBeenCalled();
  });

  it("已注册且无 successMessage：只执行 save，不额外弹 success（反馈由页面内部负责）", async () => {
    const e = entry();
    registerGlobalSave(e);
    await invokeGlobalSave();
    expect(e.save).toHaveBeenCalledTimes(1);
    expect(mockedToast.success).not.toHaveBeenCalled();
  });

  it("successMessage 静态文案：保存成功后弹出", async () => {
    registerGlobalSave(entry({ successMessage: "已保存配置" }));
    await invokeGlobalSave();
    expect(mockedToast.success).toHaveBeenCalledWith("已保存配置");
  });

  it("successMessage 函数形式：保存成功后取值弹出", async () => {
    registerGlobalSave(entry({ successMessage: () => "已保存向导设置" }));
    await invokeGlobalSave();
    expect(mockedToast.success).toHaveBeenCalledWith("已保存向导设置");
  });

  it("save 返回 false：静默（无修改/守卫拦截场景），即使配置了 successMessage", async () => {
    registerGlobalSave(entry({ save: async () => false, successMessage: "不应出现" }));
    await invokeGlobalSave();
    expect(mockedToast.success).not.toHaveBeenCalled();
  });

  it("successMessage 解析为空串：静默", async () => {
    registerGlobalSave(entry({ successMessage: () => "" }));
    await invokeGlobalSave();
    expect(mockedToast.success).not.toHaveBeenCalled();
  });

  it("in-flight 期间重复触发被忽略", async () => {
    let resolveSave: (v: boolean) => void = () => {};
    const save = vi.fn(
      () => new Promise<boolean>((resolve) => (resolveSave = resolve)),
    );
    registerGlobalSave(entry({ save }));
    const first = invokeGlobalSave();
    const second = invokeGlobalSave();
    resolveSave(true);
    await Promise.all([first, second]);
    expect(save).toHaveBeenCalledTimes(1);
  });

  it("save 抛出未捕获异常：兜底 toast.error 且释放 in-flight（后续可再次触发）", async () => {
    const save = vi.fn(async () => {
      throw new Error("网络炸了");
    });
    registerGlobalSave(entry({ save }));
    await expect(invokeGlobalSave()).resolves.toBeUndefined();
    expect(mockedToast.error).toHaveBeenCalledWith("保存失败：网络炸了");
    // in-flight 已释放：第二次触发会再次调用 save
    await invokeGlobalSave();
    expect(save).toHaveBeenCalledTimes(2);
  });
});

describe("unregisterGlobalSave 身份比对", () => {
  it("注销旧 entry 不误删后注册者的槽位", async () => {
    const firstSave = vi.fn(async () => true);
    const secondSave = vi.fn(async () => true);
    const first = entry({ save: firstSave });
    const second = entry({ save: secondSave });
    registerGlobalSave(first);
    registerGlobalSave(second);
    unregisterGlobalSave(first);
    expect(hasGlobalSave()).toBe(true);
    await invokeGlobalSave();
    expect(secondSave).toHaveBeenCalledTimes(1);
    expect(firstSave).not.toHaveBeenCalled();
    unregisterGlobalSave(second);
    expect(hasGlobalSave()).toBe(false);
  });
});
