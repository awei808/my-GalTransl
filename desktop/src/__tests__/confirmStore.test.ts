/**
 * confirmStore 单元测试
 * 覆盖三按钮确认弹窗（extraText/action），验证 backward compat
 */
import { describe, it, expect, beforeEach } from "vitest";
import { confirm, getConfirmState } from "../stores/confirmStore";
import type { ConfirmResult } from "../stores/confirmStore";

describe("confirmStore — 三按钮确认流程", () => {
  beforeEach(() => {
    // 确保前一个测试的 resolve 不会残留（如果之前未 resolve 手动清理）
    // 由于 confirm 是单例模态，需要确保每次测试从干净状态开始
    if (getConfirmState().visible) {
      confirm.resolve(false);
    }
  });

  it("两按钮确认：resolve(true) → confirmed=true, action='confirm'", async () => {
    const promise = confirm.show({
      title: "测试标题",
      message: "测试消息",
      confirmText: "保存",
      cancelText: "取消",
      dismissible: false,
    });

    // 模拟 ConfirmHost 点击确认按钮
    confirm.resolve(true);

    const result: ConfirmResult = await promise;
    expect(result.confirmed).toBe(true);
    expect(result.action).toBe("confirm");
  });

  it("两按钮确认：resolve(false) → confirmed=false, action='cancel'", async () => {
    const promise = confirm.show({
      title: "测试标题",
      message: "测试消息",
      confirmText: "保存",
      cancelText: "取消",
      dismissible: false,
    });

    confirm.resolve(false);

    const result: ConfirmResult = await promise;
    expect(result.confirmed).toBe(false);
    expect(result.action).toBe("cancel");
  });

  it("三按钮确认（extraText='取消'）：只调 resolve(false, undefined, 'extra') → action='extra'", async () => {
    const promise = confirm.show({
      title: "未保存的修改",
      message: "是否保存？",
      confirmText: "保存",
      cancelText: "不保存",
      extraText: "取消",
      dismissible: false,
    });

    // 模拟 ConfirmHost 点击第三个按钮
    confirm.resolve(false, undefined, "extra");

    const result: ConfirmResult = await promise;
    expect(result.confirmed).toBe(false);
    expect(result.action).toBe("extra");
  });

  it("三按钮确认：点保存 resolve(true) → action='confirm'", async () => {
    const promise = confirm.show({
      title: "未保存的修改",
      message: "是否保存？",
      confirmText: "保存",
      cancelText: "不保存",
      extraText: "取消",
      dismissible: false,
    });

    confirm.resolve(true);

    const result: ConfirmResult = await promise;
    expect(result.confirmed).toBe(true);
    expect(result.action).toBe("confirm");
  });

  it("三按钮确认：点不保存 resolve(false) → action='cancel'", async () => {
    const promise = confirm.show({
      title: "未保存的修改",
      message: "是否保存？",
      confirmText: "保存",
      cancelText: "不保存",
      extraText: "取消",
      dismissible: false,
    });

    confirm.resolve(false);

    const result: ConfirmResult = await promise;
    expect(result.confirmed).toBe(false);
    expect(result.action).toBe("cancel");
  });

  it("向后兼容：旧的 resolve(true) 未传 action → 自动推断为 'confirm'", async () => {
    const promise = confirm.show({
      title: "旧版调用",
      confirmText: "删除",
      cancelText: "取消",
      tone: "danger",
    });

    // 旧版调用方式：resolve(true) — 不传 action
    confirm.resolve(true);

    const result: ConfirmResult = await promise;
    expect(result.action).toBe("confirm");
  });

  it("向后兼容：旧的 resolve(false) 未传 action → 自动推断为 'cancel'", async () => {
    const promise = confirm.show({
      title: "旧版调用",
      confirmText: "删除",
      cancelText: "取消",
      tone: "danger",
    });

    confirm.resolve(false);

    const result: ConfirmResult = await promise;
    expect(result.action).toBe("cancel");
  });
});
