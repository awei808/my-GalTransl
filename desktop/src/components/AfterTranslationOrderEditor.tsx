import { createEffect, createSignal, For, onMount } from "solid-js";
import { toast } from "../stores/toastStore";
import {
  AFTER_TRANSLATION_BACKENDS,
  AfterTranslationEntry,
  FixConfig,
  createFixEntry,
  isFixEntry,
} from "../lib/afterTranslation";
import { fetchProblemTypes } from "../lib/api/general";

interface AfterTranslationOrderEditorProps {
  /** 有序后端条目数组（数组顺序即执行顺序；空数组 = 不执行） */
  value: AfterTranslationEntry[];
  onChange: (order: AfterTranslationEntry[]) => void;
}

/**
 * 「修复和改进译文」后处理顺序编辑器：样式参考流水线阶段列表，
 * 但勾选框换成数字框——数字几就代表该后端在第几步执行，留空则不执行。
 *
 * 交互约定（与需求确认一致）：
 * - 点击（聚焦）数字框：若该后端未入选，追加到末尾（数组始终紧凑无空洞，
 *   故追加即分配当前最小的可用序号）。
 * - 手动输入数字：把该后端移到对应执行位（越界自动夹取到首/尾）。
 * - 清空 / 输入非法值：移除该后端，其余自动紧凑重排（序号始终连续 1..N）。
 * - 统一问题修复（fix）为对象条目，入选后可展开编辑问题类型组合
 *   （输入模式由所选问题类型自动推导）；问题类型为空时 toast 告警（运行时会跳过且不执行）。
 */
export function AfterTranslationOrderEditor(props: AfterTranslationOrderEditorProps) {
  const [problemTypes, setProblemTypes] = createSignal<string[]>([]);
  const [problemTypesError, setProblemTypesError] = createSignal(false);

  async function loadProblemTypes() {
    setProblemTypesError(false);
    try {
      const list = await fetchProblemTypes();
      setProblemTypes(list.map((p) => p.name));
    } catch {
      // 加载失败给出错误态（可重试），不阻塞编辑：后端有兜底校验
      setProblemTypesError(true);
    }
  }

  onMount(loadProblemTypes);

  // 滚动位置兜底：父组件 config signal 每次 setValue 都会重渲染，内联 .map 会整体
  // 替换 stage-item 节点导致 .after-trans-list 的 scrollTop 复位。onScroll 记录用户
  // 实际滚动位置，value 变化后（DOM 已更新）用 rAF 恢复，避免勾选/输入时滚动条跳顶。
  let listRef: HTMLDivElement | undefined;
  let lastScrollTop = 0;

  createEffect(() => {
    // 读取 props.value 以建立响应式依赖：value 变化后恢复滚动位置
    void props.value;
    if (listRef) {
      const top = lastScrollTop;
      requestAnimationFrame(() => {
        if (listRef && listRef.scrollTop === 0 && top > 0) {
          listRef.scrollTop = top;
        }
      });
    }
  });

  const entryKey = (entry: AfterTranslationEntry): string =>
    typeof entry === "string" ? entry : "fix";

  const orderIndex = (key: string) => {
    const idx = props.value.findIndex((entry) => entryKey(entry) === key);
    return idx;
  };

  function handleFocus(key: string) {
    if (orderIndex(key) >= 0) return;
    props.onChange(
      key === "fix" ? [...props.value, createFixEntry()] : [...props.value, key],
    );
  }

  function handleInput(key: string, raw: string) {
    const n = Number(raw);
    if (raw.trim() === "" || !Number.isFinite(n) || n < 1) {
      // 清空 / 非法值 → 移除该后端，其余保持顺序自动紧凑
      props.onChange(props.value.filter((entry) => entryKey(entry) !== key));
      return;
    }
    // 移动前先取出该后端现有条目：fix 条目需复用其已勾选的问题类型组合，
    // 否则重插 createFixEntry() 会静默清空用户已勾选的问题类型
    const existing = props.value.find((entry) => entryKey(entry) === key);
    const rest = props.value.filter((entry) => entryKey(entry) !== key);
    const pos = Math.min(Math.max(Math.floor(n), 1), rest.length + 1);
    const next = [...rest];
    next.splice(pos - 1, 0, existing ?? (key === "fix" ? createFixEntry() : key));
    props.onChange(next);
  }

  // fix 条目唯一：直接命中 isFixEntry 即可。父组件每次重渲染会用 parseAfterTranslation
  // 重建数组与 fix 对象引用，故不能用 entry === target 引用比较（必然为 false 导致改动丢失）。
  function updateFixEntry(mutate: (cfg: FixConfig) => FixConfig) {
    props.onChange(
      props.value.map((entry) =>
        isFixEntry(entry) ? { fix: mutate(entry.fix) } : entry,
      ),
    );
  }

  function handleTypeToggle(name: string, checked: boolean) {
    updateFixEntry((cfg) => {
      const types = checked
        ? [...cfg.types, name]
        : cfg.types.filter((t) => t !== name);
      if (types.length === 0) {
        toast.warning("「统一问题修复」未选择任何问题类型，运行时将跳过且不执行");
      }
      return { ...cfg, types };
    });
  }

  return (
    <div
      class="pipeline-stage-list after-trans-list"
      ref={listRef}
      onScroll={(e) => {
        lastScrollTop = e.currentTarget.scrollTop;
      }}
    >
      <For each={AFTER_TRANSLATION_BACKENDS}>
        {(b) => {
          const idx = orderIndex(b.key);
          const entry = idx >= 0 ? props.value[idx] : undefined;
          const fixEntry = entry !== undefined && isFixEntry(entry) ? entry : undefined;
        return (
          <div>
            <div class="pipeline-stage-item after-trans-item">
              <div class="after-trans-item__num-wrap">
                <input
                  type="number"
                  min={1}
                  max={Math.max(props.value.length, 1)}
                  class="after-trans-item__num"
                  value={idx >= 0 ? idx + 1 : ""}
                  placeholder="—"
                  onFocus={() => handleFocus(b.key)}
                  onInput={(e) => handleInput(b.key, e.currentTarget.value)}
                />
              </div>
              <div class="pipeline-stage-item__body after-trans-item__body">
                <span class="pipeline-stage-item__label">{b.label}</span>
                <span class="pipeline-stage-item__hint">{b.hint}</span>
              </div>
            </div>
            {fixEntry !== undefined && (
              <div class="after-trans-fix-config">
                <div class="after-trans-fix-config__row">
                  <span class="after-trans-fix-config__label">问题类型</span>
                  <div class="after-trans-fix-config__types">
                    {problemTypes().length === 0 &&
                      (problemTypesError() ? (
                        <button
                          type="button"
                          class="after-trans-fix-config__retry"
                          onClick={loadProblemTypes}
                        >
                          加载问题类型失败，点击重试
                        </button>
                      ) : (
                        <span class="after-trans-fix-config__empty">加载中…</span>
                      ))}
                    {problemTypes().map((name) => (
                      <label class="after-trans-fix-config__check">
                        <input
                          type="checkbox"
                          checked={fixEntry.fix.types.includes(name)}
                          onChange={(e) =>
                            handleTypeToggle(name, e.currentTarget.checked)
                          }
                        />
                        {name}
                      </label>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </div>
        );
        }}
      </For>
    </div>
  );
}
