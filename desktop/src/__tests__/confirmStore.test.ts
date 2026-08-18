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

  it("排队：活动弹窗期间 show() 不覆盖旧 promise，关闭后展示下一个", async () => {
    const first = confirm.show({
      title: "A",
      confirmText: "确定",
      cancelText: "取消",
    });
    expect(getConfirmState().visible).toBe(true);
    expect(getConfirmState().options?.title).toBe("A");

    // 第二个 show 排队，不立即显示、不覆盖第一个
    const second = confirm.show({
      title: "B",
      confirmText: "确定",
      cancelText: "取消",
    });
    expect(getConfirmState().visible).toBe(true);
    expect(getConfirmState().options?.title).toBe("A");

    // 关闭第一个 → 第二个显示
    confirm.resolve(false);
    expect(getConfirmState().visible).toBe(true);
    expect(getConfirmState().options?.title).toBe("B");

    // 关闭第二个
    confirm.resolve(true);
    const r1 = await first;
    const r2 = await second;
    expect(r1.confirmed).toBe(false);
    expect(r2.confirmed).toBe(true);
  });

  it("连续 resolve 依次关闭队列中的弹窗", async () => {
    const first = confirm.show({ title: "A", confirmText: "确定", cancelText: "取消" });
    const second = confirm.show({ title: "B", confirmText: "确定", cancelText: "取消" });

    confirm.resolve(false); // 关闭 A → 显示 B
    expect(getConfirmState().options?.title).toBe("B");

    confirm.resolve(false); // 关闭 B（连续操作语义：依次关闭，非覆盖）
    expect(getConfirmState().visible).toBe(false);

    expect((await first).confirmed).toBe(false);
    expect((await second).confirmed).toBe(false);
  });

  it("弹窗已全部关闭后 resolve 为 no-op（守卫）", async () => {
    const first = confirm.show({ title: "A", confirmText: "确定", cancelText: "取消" });
    confirm.resolve(false);
    // 弹窗已关闭且无队列：再次 resolve 不应抛错、不应显示任何弹窗
    expect(() => confirm.resolve(false)).not.toThrow();
    expect(getConfirmState().visible).toBe(false);
    expect((await first).confirmed).toBe(false);
  });
});
