/**
 * BackendPicker 交互测试：展开/过滤/选中/键盘/收起。
 *
 * 背景：翻译控制台的后端下拉原有 18 项长描述平铺（面板 200px 高、每项折行后
 * 一次仅见约 2 项），改为「搜索 + 分组 + 单行说明」后，交互正确性（过滤口径、
 * 选中回传引擎名、键盘可达、Esc 收起）需要由测试锁定，避免以后改动破坏。
 */
import { beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@solidjs/testing-library";
import { createSignal } from "solid-js";
import { BackendPicker } from "../components/BackendPicker";
import type { TranslatorOption } from "../lib/api/types";

// jsdom 未实现 scrollIntoView；组件用它把键盘高亮项滚进视野（浏览器端行为）
beforeAll(() => {
  Element.prototype.scrollIntoView = () => {};
});

const LIST: TranslatorOption[] = [
  { name: "ForGal-full-pipeline", description: "完整翻译流水线：自动执行压缩→全局分析→…" },
  { name: "ForGlobalPrompt", description: "由压缩后全文+游戏信息生成全局剧情概要" },
  { name: "ForGal-json-translate", description: "翻译后端：翻译Gal时使用，json格式输入" },
  { name: "ForSemCheck", description: "逐句对照原文与译文，仅将语义极大差异的句子标记" },
  { name: "ForSemCheckAgain", description: "对已标记为「疑似错误」的句子逐句复核。" },
  { name: "ForFutureEngine", description: "尚未登记短名的未来后端" },
];

function open(container: HTMLElement) {
  fireEvent.click(container.querySelector(".backend-select-trigger")!);
}

describe("BackendPicker", () => {
  it("默认收起：触发框显示中文短名与引擎名，不渲染搜索框", () => {
    const { container } = render(() => (
      <BackendPicker translators={LIST} value="ForSemCheck" onSelect={() => {}} />
    ));
    const trigger = container.querySelector(".backend-select-trigger")!;
    expect(trigger.textContent).toContain("语义检测");
    expect(trigger.textContent).toContain("ForSemCheck");
    expect(container.querySelector(".backend-search")).toBeNull();
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  });

  it("展开后渲染分组头与全部选项，单行说明用短说明（未登记项回退后端描述）", () => {
    const { container } = render(() => (
      <BackendPicker translators={LIST} value="" onSelect={() => {}} />
    ));
    open(container);

    const groups = [...container.querySelectorAll(".backend-group")].map((e) => e.textContent);
    expect(groups).toEqual(["翻译执行", "分析与元数据", "后处理与校对", "其它"]);
    expect(container.querySelectorAll(".backend-option")).toHaveLength(LIST.length);
    expect(container.querySelector(".backend-empty")).toBeNull();

    const hints = [...container.querySelectorAll(".backend-option__hint")].map(
      (e) => e.textContent
    );
    expect(hints[0]).toBe("一键跑完全流程（不建议日常启用）");
    expect(hints).toContain("尚未登记短名的未来后端");
    expect(container.querySelector(".backend-search")).not.toBeNull();
  });

  it("搜索按中文短名/说明过滤，无命中给提示", () => {
    const { container } = render(() => (
      <BackendPicker translators={LIST} value="" onSelect={() => {}} />
    ));
    open(container);

    fireEvent.input(container.querySelector(".backend-search")!, {
      target: { value: "语义 复核" },
    });
    const names = [...container.querySelectorAll(".backend-option__engine")].map(
      (e) => e.textContent
    );
    expect(names).toEqual(["ForSemCheckAgain"]);

    fireEvent.input(container.querySelector(".backend-search")!, { target: { value: "zzz" } });
    expect(container.querySelectorAll(".backend-option")).toHaveLength(0);
    expect(container.querySelector(".backend-empty")!.textContent).toContain("没有匹配的后端");
  });

  it("点击选项回传引擎名并收起面板", () => {
    const onSelect = vi.fn();
    const { container } = render(() => (
      <BackendPicker translators={LIST} value="ForSemCheck" onSelect={onSelect} />
    ));
    open(container);

    // 按引擎名定位，避免依赖分组后的渲染下标
    const target = [...container.querySelectorAll(".backend-option")].find(
      (el) => el.querySelector(".backend-option__engine")!.textContent === "ForGlobalPrompt"
    )!;
    fireEvent.click(target);
    expect(onSelect).toHaveBeenCalledWith("ForGlobalPrompt");
    expect(container.querySelector(".backend-search")).toBeNull();
  });

  it("键盘：↓ 移动高亮、回车选中高亮项、Esc 收起", () => {
    const onSelect = vi.fn();
    const { container } = render(() => (
      <BackendPicker translators={LIST} value="ForGal-full-pipeline" onSelect={onSelect} />
    ));
    open(container);

    const search = container.querySelector(".backend-search")!;
    // 打开时高亮当前选中项（分组后仍是首项）；↓ 落到摊平顺序的第二项
    expect(container.querySelector(".backend-option--hl .backend-option__engine")!.textContent).toBe(
      "ForGal-full-pipeline"
    );
    fireEvent.keyDown(search, { key: "ArrowDown" });
    const highlighted = container.querySelector(".backend-option--hl")!;
    expect(highlighted.querySelector(".backend-option__engine")!.textContent).toBe(
      "ForGal-json-translate"
    );

    fireEvent.keyDown(search, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("ForGal-json-translate");

    open(container);
    fireEvent.keyDown(container.querySelector(".backend-search")!, { key: "Escape" });
    expect(container.querySelector(".backend-search")).toBeNull();
  });

  it("点击面板外部收起", () => {
    const { container } = render(() => (
      <BackendPicker translators={LIST} value="ForSemCheck" onSelect={() => {}} />
    ));
    open(container);
    fireEvent.click(document.body);
    expect(container.querySelector(".backend-search")).toBeNull();
  });

  it("当前选中项有 active 标记与勾选，未选中项没有", () => {
    const { container } = render(() => (
      <BackendPicker translators={LIST} value="ForSemCheck" onSelect={() => {}} />
    ));
    open(container);
    const active = container.querySelectorAll(".backend-option.active");
    expect(active).toHaveLength(1);
    expect(active[0].querySelector(".backend-option__check")).not.toBeNull();
    expect(active[0].getAttribute("aria-selected")).toBe("true");
  });

  it("触发框键盘可达：回车展开、闭态 Esc 无异常", () => {
    const { container } = render(() => (
      <BackendPicker translators={LIST} value="ForSemCheck" onSelect={() => {}} />
    ));
    const trigger = container.querySelector(".backend-select-trigger")!;
    expect(trigger.getAttribute("tabindex")).toBe("0");

    fireEvent.keyDown(trigger, { key: "Enter" });
    expect(container.querySelector(".backend-search")).not.toBeNull();
    expect(trigger.getAttribute("aria-expanded")).toBe("true");

    fireEvent.keyDown(trigger, { key: "Escape" });
    fireEvent.keyDown(trigger, { key: "Escape" });
    expect(container.querySelector(".backend-search")).toBeNull();
  });

  it("清单拉取失败：触发框给出可见状态，点击不展开空面板", () => {
    const { container } = render(() => (
      <BackendPicker translators={[]} loadFailed value="ForSemCheck" onSelect={() => {}} />
    ));
    const trigger = container.querySelector(".backend-select-trigger")!;
    expect(trigger.textContent).toContain("后端清单未加载");
    // 失败态下不显示旧选中值的引擎名，避免「未加载 + 旧值」并存造成误读
    expect(trigger.textContent).not.toContain("ForSemCheck");
    expect(trigger.getAttribute("aria-disabled")).toBe("true");
    expect(trigger.getAttribute("title")).toContain("后端清单未加载");

    fireEvent.click(trigger);
    expect(container.querySelector(".backend-dropdown")).toBeNull();
  });

  it("清单尚未加载完：显示加载中，且不套用失败态样式", () => {
    const { container } = render(() => (
      <BackendPicker translators={[]} loadFailed={false} value="" onSelect={() => {}} />
    ));
    const trigger = container.querySelector(".backend-select-trigger")!;
    expect(trigger.textContent).toContain("加载中");
    expect(trigger.classList.contains("backend-select-trigger--disabled")).toBe(false);
    expect(trigger.getAttribute("aria-disabled")).toBeNull();
    expect(container.querySelector(".backend-select-trigger__engine")).toBeNull();
  });

  it("value 变化时触发框文案随之更新（选中口径为响应式）", async () => {
    const [value, setValue] = createSignal("ForSemCheck");
    const { container } = render(() => (
      <BackendPicker translators={LIST} value={value()} onSelect={() => {}} />
    ));
    expect(container.querySelector(".backend-select-trigger")!.textContent).toContain("语义检测");

    setValue("ForFixRound");
    await Promise.resolve();
    const text = container.querySelector(".backend-select-trigger")!.textContent!;
    expect(text).toContain("统一修复");
    expect(text).toContain("ForFixRound");
  });

  it("过滤到无结果后再清空查询，高亮不越界且可回车选中", () => {
    const onSelect = vi.fn();
    const { container } = render(() => (
      <BackendPicker translators={LIST} value="" onSelect={onSelect} />
    ));
    open(container);
    const search = container.querySelector(".backend-search")!;

    fireEvent.input(search, { target: { value: "zzz" } });
    expect(container.querySelectorAll(".backend-option")).toHaveLength(0);
    fireEvent.keyDown(search, { key: "Enter" });
    expect(onSelect).not.toHaveBeenCalled();

    fireEvent.input(search, { target: { value: "" } });
    fireEvent.keyDown(search, { key: "ArrowUp" }); // 从 0 回绕到末项，仍在界内
    expect(container.querySelectorAll(".backend-option--hl")).toHaveLength(1);
  });
});
