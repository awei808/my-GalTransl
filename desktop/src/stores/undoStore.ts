import { createStore } from "solid-js/store";

/**
 * 全局撤销/重做系统
 *
 * 支持场景：
 * - 校对审核：编辑字段失焦自动保存后撤销
 * - 校对审核：删除 JSON 后撤销恢复
 * - 查找替换：批量替换后逐条撤销
 */

export interface UndoEntry {
  /** 唯一标识，指向被修改的条目 */
  id: string;
  /** 文件路径 */
  file: string;
  /** 条目在文件中的 index */
  index: number;
  /** 编辑前的字段快照 */
  before: Record<string, unknown>;
  /** 编辑后的字段快照 */
  after: Record<string, unknown>;
  /** 操作描述（用于菜单显示） */
  description?: string;
}

interface UndoState {
  stack: UndoEntry[];
  pointer: number;
  maxSize: number;
}

const [undoState, setUndoState] = createStore<UndoState>({
  stack: [],
  pointer: -1,
  maxSize: 100,
});

/** 入栈：记录一次编辑操作 */
export function pushUndo(entry: UndoEntry): void {
  setUndoState((state) => {
    // 丢弃指针之后的所有历史（新操作后无法重做）
    const trimmed = state.stack.slice(0, state.pointer + 1);
    const next = [...trimmed, entry];
    if (next.length > state.maxSize) next.shift();
    return {
      stack: next,
      pointer: next.length - 1,
      maxSize: state.maxSize,
    };
  });
}

/** 撤销：返回上一步的 entry，栈空返回 null */
export function undo(): UndoEntry | null {
  const state = { ...undoState };
  if (state.pointer < 0) return null;
  const entry = state.stack[state.pointer];
  setUndoState("pointer", (p) => p - 1);
  return entry;
}

/** 重做：返回下一步的 entry，没有则返回 null */
export function redo(): UndoEntry | null {
  const state = { ...undoState };
  if (state.pointer >= state.stack.length - 1) return null;
  const nextPointer = state.pointer + 1;
  const entry = state.stack[nextPointer];
  setUndoState("pointer", nextPointer);
  return entry;
}

/** 清空历史（切换文件、关闭项目时调用） */
export function clearUndo(): void {
  setUndoState({ stack: [], pointer: -1 });
}

/** 获取当前栈状态（用于 UI 显示撤销/重做按钮状态） */
export function getUndoState() {
  return {
    canUndo: undoState.pointer >= 0,
    canRedo: undoState.pointer < undoState.stack.length - 1,
    pointer: undoState.pointer,
    stackSize: undoState.stack.length,
  };
}
