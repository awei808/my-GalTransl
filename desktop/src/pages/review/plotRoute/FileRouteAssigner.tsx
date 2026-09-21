/**
 * 剧情路线图：文件多选 + 批量归属面板。
 *
 * 用途——用户自划路线：「勾选若干文件 → 选/填一个路线名 → 批量写入」。
 * 这正是 0.5.0「剧情路线图可由用户自己划分」的落地入口：
 * 用户在可视化画布上拖拽/连线决定走线，此面板决定「哪些文件属于同一条线」。
 *
 * 只做「归属」这一件事，不碰 mermaid 拓扑——拓扑由画布负责，
 * 两者经 parent 的 fileRoutes 汇合后共同生成 mermaid。
 */
import { createSignal, createMemo, For, Show } from "solid-js";

export function FileRouteAssigner(props: {
  /** 候选文件（通常是 pass1 元数据覆盖的剧本文件名） */
  files: string[];
  /** 已有归属：文件名 → 路线名 */
  fileRoutes: Record<string, string>;
  /** 已有路线名候选（含节点剧情里出现过的） */
  routes: string[];
  /** 批量写入：这些文件改归到该路线 */
  onAssign: (files: string[], route: string) => void;
}) {
  const [selected, setSelected] = createSignal<Set<string>>(new Set());
  const [targetRoute, setTargetRoute] = createSignal("");

  const toggle = (file: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(file)) next.delete(file);
      else next.add(file);
      return next;
    });
  };

  const allChecked = createMemo(
    () => props.files.length > 0 && selected().size === props.files.length,
  );

  const toggleAll = () => {
    setSelected(allChecked() ? new Set<string>() : new Set(props.files));
  };

  const apply = () => {
    const route = targetRoute().trim();
    if (selected().size === 0 || !route) return;
    props.onAssign([...selected()], route);
    setSelected(new Set<string>());
  };

  /* 路线 → 已归属文件数（供用户核对每条线的规模） */
  const routeCounts = createMemo(() => {
    const counts: Record<string, number> = {};
    for (const r of Object.values(props.fileRoutes)) {
      if (r) counts[r] = (counts[r] ?? 0) + 1;
    }
    return counts;
  });

  return (
    <div class="plotroute-assigner">
      <div class="plotroute-assigner-head">
        <span class="plotroute-assigner-title">文件归属</span>
        <button class="plotroute-tb-btn" onClick={toggleAll}>
          {allChecked() ? "取消全选" : "全选"}
        </button>
        <span class="plotroute-hint">已选 {selected().size} / {props.files.length}</span>
      </div>

      <div class="plotroute-assigner-list">
        <For each={props.files} fallback={<div class="plotroute-hint">无候选文件</div>}>
          {(file) => (
            <label class="plotroute-assigner-item">
              <input
                type="checkbox"
                checked={selected().has(file)}
                onChange={() => toggle(file)}
              />
              <span class="plotroute-assigner-name" title={file}>{file}</span>
              <Show when={props.fileRoutes[file]}>
                <span class="plotroute-assigner-tag">{props.fileRoutes[file]}</span>
              </Show>
            </label>
          )}
        </For>
      </div>

      <div class="plotroute-assigner-actions">
        <input
          class="plotroute-assigner-input"
          list="plotroute-route-options"
          placeholder="路线名（可新建）"
          value={targetRoute()}
          onInput={(e) => setTargetRoute(e.currentTarget.value)}
        />
        <datalist id="plotroute-route-options">
          <For each={props.routes}>{(r) => <option value={r} />}</For>
        </datalist>
        <button
          class="plotroute-btn plotroute-btn-primary"
          disabled={selected().size === 0 || !targetRoute().trim()}
          onClick={apply}
        >
          归入路线
        </button>
      </div>

      <Show when={Object.keys(routeCounts()).length > 0}>
        <div class="plotroute-assigner-stats">
          <For each={Object.entries(routeCounts())}>
            {([route, count]) => (
              <span class="plotroute-assigner-stat">
                {route}：{count}
              </span>
            )}
          </For>
        </div>
      </Show>
    </div>
  );
}
