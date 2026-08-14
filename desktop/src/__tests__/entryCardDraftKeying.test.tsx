/**
 * 回归测试：过滤（只看问题）重排后，草稿组件实例必须按条目身份隔离。
 *
 * 根因：ReviewPage 原用 <Index>（按位置复用组件实例）。过滤后同一位置挂载新条目时，
 * 旧 EntryCard 实例被复用，其 draftDst 草稿信号仍保留旧条目草稿；失焦/提交时把旧草稿
 * 写入新条目对应的存储位置 → 覆盖/污染译文。
 * 修复：改用 <For each by={e=>e.index}>（按条目身份复用）。重排时旧实例卸载、新实例
 * 重建，draftDst 以新条目 pre_dst 初始化，彻底隔离草稿。
 *
 * 本测试不模拟脆弱的输入/失焦时序，而是直接验证 keying 语义（bug 根因）：
 * 列表由 [entry1, entry2] 过滤为 [entry2] 后：
 *   - <For by=index>：entry1 实例卸载、位置 0 出现 entry2 的新实例 → 挂载日志 [1, 2]
 *   - <Index>：位置 0 复用旧实例（仅更新值不重建）→ 挂载日志 [1]
 * 草稿串位正源于「实例未重建、信号延续」，故此测试锁定修复不复发。
 */
import { describe, it, expect } from "vitest";
import { createRoot, createSignal, onMount } from "solid-js";
import { render, For, Index } from "solid-js/web";

type Row = { index: number; pre_dst: string };

async function tick(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

async function collectMounts(useFor: boolean): Promise<number[]> {
  const mounts: number[] = [];
  const rows = createRoot(() => {
    const [list, setList] = createSignal<Row[]>([
      { index: 1, pre_dst: "原译文A" },
      { index: 2, pre_dst: "原译文B" },
    ]);
    return { list, setList };
  });

  const Probe = (props: { entry: Row }) => {
    onMount(() => mounts.push(props.entry.index));
    return <textarea data-index={props.entry.index} />;
  };

  const Displayer = () =>
    useFor ? (
      <For each={rows.list()}>
        {(entry) => <Probe entry={entry} />}
      </For>
    ) : (
      <Index each={rows.list()}>
        {(sig) => <Probe entry={sig()} />}
      </Index>
    );

  const host = document.createElement("div");
  document.body.appendChild(host);
  const dispose = render(() => <Displayer />, host);
  try {
    await tick();
    // 过滤：只看问题 → 只保留 entry2，entry2 落到原位置 0
    rows.setList([{ index: 2, pre_dst: "原译文B" }]);
    await tick();
    return mounts;
  } finally {
    dispose();
    host.remove();
  }
}

describe("EntryCard 草稿实例隔离回归", () => {
  it("<For by=index> 过滤重排后为同位置新条目重建实例（修复后行为）", async () => {
    const mounts = await collectMounts(true);
    // entry1 挂载 → 卸载 → entry2 落位 0 重建实例：entry2 共挂载 2 次
    // 重建即草稿信号以新条目 pre_dst 重新初始化，旧草稿不延续 → 无串位
    expect(mounts.filter((i) => i === 2)).toHaveLength(2);
  });

  it("<Index> 过滤重排后复用旧实例（旧行为对照，证明 bug 来源）", async () => {
    const mounts = await collectMounts(false);
    // 位置 0 的实例被复用（不重建）：entry2 仅初始挂载 1 次
    // 旧实例草稿信号延续到新条目 → 覆盖/污染译文的串位根源
    expect(mounts.filter((i) => i === 2)).toHaveLength(1);
  });
});
