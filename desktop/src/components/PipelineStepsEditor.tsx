import { createResource, For, Show } from "solid-js";
import { fetchPipelineStages } from "../lib/api/general";
import { FALLBACK_STAGES, stageToggleName } from "../lib/pipelineStages";
import { isFixEntry } from "../lib/afterTranslation";
import {
  PipelineStep,
  isAfterTransStep,
  pipelineStepLabel,
} from "../lib/pipelineSteps";
import { insertNewAt, moveOrRemoveAt } from "../lib/orderedEntryList";
import type { PipelineStageInfo } from "../lib/api/types";

/** 阶段说明文案（后端清单不含 hint，前端按 key 补充；向导与项目设置页共用） */
const STAGE_HINTS: Record<string, string> = {
  validate: "校验输入文件的 message 与 name 完整性。移出流水线后跳过校验（不建议）。",
  compress: "压缩全文供全局分析使用。移出后全局分析因无压缩文本将自动跳过。",
  global_analysis: "先按剧情路线图逐路线分析，再融合为全局档案（GlobalPrompt.json）；无路线图时回退为全文分析。",
  gen_dic: "提取项目术语生成 GPT 字典。该阶段未优化，运行效果较差，不建议启用。",
  file_meta: "为每个文件生成剧情背景（FileMetaData）。",
  plot_route: "基于各文件的剧情摘要生成剧情路线图（PlotRouteMap.json），并标记每个文件所属路线。依赖文件级元数据。",
  batch_meta: "按剧情分段划分翻译区间。",
  translate: "调用 AI 翻译。移出后流水线只执行前置分析阶段，不进行翻译。",
  improve: "翻译完成后逐文件执行「AI 初步处理」编辑器中选中的后端（按数字顺序）。",
};

interface PipelineStepsEditorProps {
  /** 有序步骤数组（数组顺序即执行顺序；空数组 = 不执行任何阶段） */
  value: PipelineStep[];
  onChange: (steps: PipelineStep[]) => void;
  /** 向导专用：勾选「生成示例文件」的阶段短名集合与回调（不传则不渲染该控件） */
  sampleKeys?: Set<string>;
  onSampleToggle?: (shortName: string, checked: boolean) => void;
}

/**
 * 完整流水线步骤顺序编辑器：阶段行用数字框定执行顺序（留空 = 不执行），
 * 交互约定与「AI 初步处理」编辑器一致（聚焦追加、输入移动、清空移除、自动紧凑重排）。
 * 配置文件手写的 AI 初步处理步骤（fix/检测类）原样保留展示、可排序可移除，
 * 但不在本编辑器新增——新增请用「AI 初步处理」编辑器（阶段 8 后处理链）。
 */
export function PipelineStepsEditor(props: PipelineStepsEditorProps) {
  // 阶段清单以后端为准（唯一真源），后端不可用时回退到内置同名清单
  const [stagesResource] = createResource(async () => {
    try {
      const resp = await fetchPipelineStages();
      return resp.stages?.length ? resp.stages : FALLBACK_STAGES;
    } catch {
      return FALLBACK_STAGES;
    }
  });
  const stages = () => stagesResource() ?? FALLBACK_STAGES;

  const occurrencesOf = (key: string): number[] =>
    props.value.reduce<number[]>((acc, step, i) => {
      if (typeof step === "string" && step === key) acc.push(i);
      return acc;
    }, []);

  /** 译后步骤（fix 对象/检测类 key）在数组中的位置：手写配置的可视化保真 */
  const afterTransIndices = (): number[] =>
    props.value.reduce<number[]>((acc, step, i) => {
      if (isAfterTransStep(step)) acc.push(i);
      return acc;
    }, []);

  function handleInputAt(idx: number, raw: string) {
    props.onChange(moveOrRemoveAt(props.value, idx, raw));
  }

  function handleFocusAppend(key: string) {
    props.onChange([...props.value, key]);
  }

  function handlePlaceholderInput(key: string, raw: string) {
    if (occurrencesOf(key).length > 0) return;
    props.onChange(insertNewAt(props.value, key, raw));
  }

  const numBox = (opts: { value: string; idx?: number; key?: string }) => (
    <input
      type="number"
      min={1}
      max={Math.max(props.value.length, 1)}
      class="after-trans-item__num"
      value={opts.value}
      placeholder="—"
      onFocus={() => opts.key !== undefined && handleFocusAppend(opts.key)}
      onInput={(e) =>
        opts.idx !== undefined
          ? handleInputAt(opts.idx, e.currentTarget.value)
          : opts.key !== undefined && handlePlaceholderInput(opts.key, e.currentTarget.value)
      }
    />
  );

  return (
    <div class="pipeline-stage-list after-trans-list">
      <div class="after-trans-group-head">
        <span class="after-trans-group-head__label">翻译流水线阶段</span>
        <span class="after-trans-group-head__hint">
          数字几就代表该阶段第几个执行；留空则不执行，其余自动紧凑重排。
        </span>
      </div>
      <For each={stages()}>
        {(s: PipelineStageInfo) => {
          const occs = occurrencesOf(s.key);
          const toggleKey = stageToggleName(s);
          return (
            <div>
              {occs.length === 0 ? (
                <div class="pipeline-stage-item after-trans-item">
                  <div class="after-trans-item__num-wrap">
                    {numBox({ value: "", key: s.key })}
                  </div>
                  <div class="pipeline-stage-item__body after-trans-item__body">
                    <span class="pipeline-stage-item__label">{s.label}</span>
                    <span class="pipeline-stage-item__hint">
                      {STAGE_HINTS[s.key] ?? ""}
                    </span>
                  </div>
                  <Show when={props.onSampleToggle && s.sample_key}>
                    <label class="pipeline-stage-item__sample">
                      <input
                        type="checkbox"
                        checked={props.sampleKeys?.has(toggleKey) ?? false}
                        onChange={(e) =>
                          props.onSampleToggle?.(toggleKey, e.currentTarget.checked)
                        }
                      />
                      <span>生成示例文件</span>
                    </label>
                  </Show>
                </div>
              ) : (
                occs.map((idx, n) => (
                  <div class="pipeline-stage-item after-trans-item">
                    <div class="after-trans-item__num-wrap">
                      {numBox({ value: String(idx + 1), idx })}
                    </div>
                    <div class="pipeline-stage-item__body after-trans-item__body">
                      <span class="pipeline-stage-item__label">
                        {s.label}
                        {n > 0 ? `（第 ${n + 1} 处）` : ""}
                      </span>
                      {n === 0 && (
                        <span class="pipeline-stage-item__hint">
                          {STAGE_HINTS[s.key] ?? ""}
                        </span>
                      )}
                    </div>
                    <Show when={n === 0 && props.onSampleToggle && s.sample_key}>
                      <label class="pipeline-stage-item__sample">
                        <input
                          type="checkbox"
                          checked={props.sampleKeys?.has(toggleKey) ?? false}
                          onChange={(e) =>
                            props.onSampleToggle?.(toggleKey, e.currentTarget.checked)
                          }
                        />
                        <span>生成示例文件</span>
                      </label>
                    </Show>
                  </div>
                ))
              )}
            </div>
          );
        }}
      </For>
      <Show when={afterTransIndices().length > 0}>
        <div class="after-trans-group-head">
          <span class="after-trans-group-head__label">自定义 AI 初步处理步骤</span>
          <span class="after-trans-group-head__hint">
            来自配置文件 internals.pipeline.steps 的译后步骤（含各 fix
            实例的问题类型组合），在此仅排序/移除；新增请用「AI 初步处理」编辑器。
          </span>
        </div>
        <For each={afterTransIndices()}>
          {(idx) => (
            <div class="pipeline-stage-item after-trans-item">
              <div class="after-trans-item__num-wrap">{numBox({ value: String(idx + 1), idx })}</div>
              <div class="pipeline-stage-item__body after-trans-item__body">
                <span class="pipeline-stage-item__label">
                  {pipelineStepLabel(props.value[idx])}
                </span>
                <Show when={isFixEntry(props.value[idx])}>
                  <span class="pipeline-stage-item__hint">
                    问题类型在此不可编辑：请直接修改配置文件，或在「AI 初步处理」编辑器中调整后另存。
                  </span>
                </Show>
              </div>
            </div>
          )}
        </For>
      </Show>
    </div>
  );
}
