import { AFTER_TRANSLATION_BACKENDS } from "../lib/afterTranslation";

interface AfterTranslationOrderEditorProps {
  /** 有序后端 key 数组（数组顺序即执行顺序；空数组 = 不执行） */
  value: string[];
  onChange: (order: string[]) => void;
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
 */
export function AfterTranslationOrderEditor(props: AfterTranslationOrderEditorProps) {
  const orderIndex = (key: string) => props.value.indexOf(key);

  function handleFocus(key: string) {
    if (orderIndex(key) >= 0) return;
    props.onChange([...props.value, key]);
  }

  function handleInput(key: string, raw: string) {
    const n = Number(raw);
    if (raw.trim() === "" || !Number.isFinite(n) || n < 1) {
      // 清空 / 非法值 → 移除该后端，其余保持顺序自动紧凑
      props.onChange(props.value.filter((k) => k !== key));
      return;
    }
    // 移到第 n 位（夹取到 1..N），其余保持相对顺序
    const rest = props.value.filter((k) => k !== key);
    const pos = Math.min(Math.max(Math.floor(n), 1), rest.length + 1);
    const next = [...rest];
    next.splice(pos - 1, 0, key);
    props.onChange(next);
  }

  return (
    <div class="pipeline-stage-list after-trans-list">
      {AFTER_TRANSLATION_BACKENDS.map((b) => {
        const idx = orderIndex(b.key);
        return (
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
        );
      })}
    </div>
  );
}
