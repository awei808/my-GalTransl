/**
 * 单条元数据卡片（0.4.10 从 ReviewPage.tsx 抽出）。
 *
 * 用于 FileMetaData / BatchMetadata / GlobalPrompt：id 只读展示，
 * 其余字段按「键值对应」逐行编辑（见 MetaKeyValueEditor）。
 */
import { Show } from "solid-js";
import type { MetadataEntry } from "../../lib/api/types";
import { MetaKeyValueEditor } from "./MetaKeyValueEditor";

/* ── 单条元数据组件（FileMetaData / BatchMetadata / GlobalPrompt）──
   id 只读展示，其余字段按「键值对应」逐行编辑（键、值均可改，见 MetaKeyValueEditor）。 */
export function MetadataCard(props: {
  entry: MetadataEntry;
  index: number;
  onContentChange: (text: string) => void;
  onDelete?: () => void;
  onBlur: () => void;
}) {
  return (
    <div class="meta-card">
      <div class="meta-card-head">
        <span class="meta-id-text" title="条目 id（只读，不可修改）">
          id: {String((props.entry as Record<string, unknown>).id ?? "") || "—"}
        </span>
        {/* 右上角删除按钮（仅多条目模式显示） */}
        <Show when={props.onDelete}>
          <button class="entry-btn entry-btn--danger" title="删除该条目" onClick={props.onDelete!}>
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" />
            </svg>
            <span class="entry-btn-text">删除</span>
          </button>
        </Show>
      </div>
      <MetaKeyValueEditor
        entry={props.entry as Record<string, unknown>}
        onContentChange={props.onContentChange}
        onBlur={props.onBlur}
      />
    </div>
  );
}
