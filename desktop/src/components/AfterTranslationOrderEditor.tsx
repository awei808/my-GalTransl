import { createEffect, createSignal, onMount } from "solid-js";
import { toast } from "../stores/toastStore";
import {
  AFTER_TRANSLATION_BACKENDS,
  AFTER_TRANSLATION_GROUPS,
  AfterTranslationEntry,
  FixConfig,
  createFixEntry,
  isFixEntry,
} from "../lib/afterTranslation";
import { insertNewAt, moveOrRemoveAt } from "../lib/orderedEntryList";
import { fetchProblemTypes } from "../lib/api/general";

interface AfterTranslationOrderEditorProps {
  /** 有序后端条目数组（数组顺序即执行顺序；空数组 = 不执行） */
  value: AfterTranslationEntry[];
  onChange: (order: AfterTranslationEntry[]) => void;
}

/**
 * 「AI 初步处理」后处理顺序编辑器：样式参考流水线阶段列表，
 * 但勾选框换成数字框——数字几就代表该后端在第几步执行，留空则不执行。
 * 后端按五个分组展示（基本问题处理/修正翻译风格/词语色彩一致性检查/节奏冗长检测/标注疑似错误），
 * 分组仅为视觉分区，跨组编号连续、数组顺序即执行顺序。
 *
 * 交互约定（与需求确认一致）：
 * - 点击（聚焦）占位数字框：追加该后端到末尾（数组始终紧凑无空洞，
 *   故追加即分配当前最小的可用序号）。
 * - 手动输入数字：把该条目移到对应执行位（越界自动夹取到首/尾）。
 * - 清空 / 输入非法值：移除该条目，其余自动紧凑重排（序号始终连续 1..N）。
 * - 同一后端允许多次出现：统一问题修复（fix）为对象条目，实例行下方可展开编辑
 *   该实例的问题类型组合（输入模式由所选问题类型自动推导）；问题类型为空时
 *   toast 告警（运行时会跳过且不执行）。「＋ 再添加」追加下一个 fix 实例。
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

  /** 该后端在数组中的全部位置（索引升序）；多实例（重复条目）各占一行 */
  const occurrencesOf = (key: string): number[] =>
    props.value.reduce<number[]>((acc, entry, i) => {
      if (entryKey(entry) === key) acc.push(i);
      return acc;
    }, []);

  /** 移动/移除数组中第 idx 个条目：清空或非法值=移除，数字=移到对应执行位 */
  function handleInputAt(idx: number, raw: string) {
    props.onChange(moveOrRemoveAt(props.value, idx, raw));
  }

  /** 未入选时聚焦占位框：追加一个实例到末尾（数组紧凑，追加即分配最小可用序号） */
  function handleFocusAppend(key: string) {
    props.onChange(
      key === "fix" ? [...props.value, createFixEntry()] : [...props.value, key],
    );
  }

  /** 占位框输入兜底：聚焦未及追加时的极速输入（onFocus 先触发，通常不会走到） */
  function handlePlaceholderInput(key: string, raw: string) {
    if (occurrencesOf(key).length > 0) return;
    const entry = key === "fix" ? createFixEntry() : key;
    props.onChange(insertNewAt(props.value, entry, raw));
  }

  /** 按索引修改某个 fix 实例的参数（多实例下不能 map 全体 fix 条目） */
  function updateFixEntryAt(idx: number, mutate: (cfg: FixConfig) => FixConfig) {
    props.onChange(
      props.value.map((entry, i) =>
        i === idx && isFixEntry(entry) ? { fix: mutate(entry.fix) } : entry,
      ),
    );
  }

  function handleTypeToggle(idx: number, name: string, checked: boolean) {
    updateFixEntryAt(idx, (cfg) => {
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
      {/* 清单为静态常量：按分组顺序渲染，组头在分组切换处插入 */}
      {AFTER_TRANSLATION_BACKENDS.map((b, i) => {
        const prev = i > 0 ? AFTER_TRANSLATION_BACKENDS[i - 1] : undefined;
        const groupHead = prev === undefined || prev.group !== b.group;
        const groupMeta = AFTER_TRANSLATION_GROUPS.find((g) => g.key === b.group);
        const occs = occurrencesOf(b.key);
        return (
          <div>
            {groupHead && groupMeta && (
              <div class="after-trans-group-head">
                <span class="after-trans-group-head__label">{groupMeta.label}</span>
                <span class="after-trans-group-head__hint">{groupMeta.hint}</span>
              </div>
            )}
            {occs.length === 0 ? (
              <div class="pipeline-stage-item after-trans-item">
                <div class="after-trans-item__num-wrap">
                  <input
                    type="number"
                    min={1}
                    max={Math.max(props.value.length, 1)}
                    class="after-trans-item__num"
                    value=""
                    placeholder="—"
                    onFocus={() => handleFocusAppend(b.key)}
                    onInput={(e) => handlePlaceholderInput(b.key, e.currentTarget.value)}
                  />
                </div>
                <div class="pipeline-stage-item__body after-trans-item__body">
                  <span class="pipeline-stage-item__label">{b.label}</span>
                  <span class="pipeline-stage-item__hint">{b.hint}</span>
                </div>
              </div>
            ) : (
              occs.map((idx, n) => {
                const entry = props.value[idx];
                return (
                  <div>
                    <div class="pipeline-stage-item after-trans-item">
                      <div class="after-trans-item__num-wrap">
                        <input
                          type="number"
                          min={1}
                          max={Math.max(props.value.length, 1)}
                          class="after-trans-item__num"
                          value={idx + 1}
                          placeholder="—"
                          onInput={(e) => handleInputAt(idx, e.currentTarget.value)}
                        />
                      </div>
                      <div class="pipeline-stage-item__body after-trans-item__body">
                        <span class="pipeline-stage-item__label">
                          {b.label}
                          {n > 0 ? `（第 ${n + 1} 处）` : ""}
                        </span>
                        {n === 0 && (
                          <span class="pipeline-stage-item__hint">{b.hint}</span>
                        )}
                      </div>
                    </div>
                    {isFixEntry(entry) && (
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
                                  checked={entry.fix.types.includes(name)}
                                  onChange={(e) =>
                                    handleTypeToggle(idx, name, e.currentTarget.checked)
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
              })
            )}
            {b.key === "fix" && occs.length > 0 && (
              <button
                type="button"
                class="after-trans-add"
                onClick={() => handleFocusAppend("fix")}
              >
                ＋ 再添加一个统一问题修复
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}
