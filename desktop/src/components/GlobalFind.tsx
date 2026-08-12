/**
 * GlobalFind —— 应用级「可见文本」查找浮层（浏览器式 Ctrl+F）
 *
 * 设计要点（对齐需求 B）：
 *  - 纯前端 DOM 查找，扫描 main-area 主区当前渲染的所有可见文本节点。
 *  - 高亮使用 CSS Custom Highlight API（CSS.highlights），不改写 DOM 结构，
 *    因此不会破坏 SolidJS 的响应式绑定与事件。
 *  - 覆盖所有界面（校对审核 / 字典管理 / 问题检测 / 构建输出 / 设置 …），
 *    因为扫描的是 main-area 当前已渲染的 DOM，无需逐界面改造。
 *  - 支持 ↑/↓/Enter 在匹配项间跳转、Esc 关闭并清除高亮。
 */
import { Show, createEffect, onCleanup } from "solid-js";
import { appState, setAppState } from "../stores/appStore";

// 高亮注册名（CSS 中通过 ::highlight() 上色）
const HL_ALL = "galtransl-find-all";
const HL_CURRENT = "galtransl-find-current";

// CSS Custom Highlight API 的类型定义（TS lib.dom 尚未收录，见
// https://drafts.csswg.org/css-highlight-api-1/）。仅在运行时做存在性
// 探测，环境不支持时跳过，不影响其余查找逻辑。
interface HighlightRegistry {
  set(name: string, highlight: object): void;
  delete(name: string): void;
}
interface WindowWithHighlight extends Window {
  Highlight: new (...ranges: Range[]) => object;
  CSS: typeof CSS & { highlights?: HighlightRegistry };
}
type GlobalFindWindow = WindowWithHighlight;

// 不扫描的节点（仅浮层自身与脚本/样式类；表单输入改为单独纳入可见文本搜索）
const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "OPTION"]);

type Match =
  | { kind: "text"; range: Range }
  | { kind: "input"; el: HTMLElement };

let debounceTimer: number | undefined;
let matches: Match[] = [];
let current = -1;

function isVisible(el: Element): boolean {
  if (!el || el.nodeType !== 1) return false;
  const cs = getComputedStyle(el);
  if (cs.display === "none" || cs.visibility === "hidden" || cs.opacity === "0") return false;
  const rect = el.getBoundingClientRect();
  if (rect.width === 0 && rect.height === 0) return false;
  return true;
}

function clearHighlights() {
  try {
    const w = window as GlobalFindWindow;
    if (w.CSS && w.CSS.highlights) {
      w.CSS.highlights.delete(HL_ALL);
      w.CSS.highlights.delete(HL_CURRENT);
    }
  } catch {
    /* 防御性：环境不支持时静默 */
  }
  // 移除 input 类命中元素的外框高亮
  document
    .querySelectorAll(".global-find-input-hit")
    .forEach((e) => e.classList.remove("global-find-input-hit"));
}

function runSearch(query: string) {
  clearHighlights();
  matches = [];
  current = -1;

  const q = query.trim();
  if (!q) {
    setAppState("globalFindCount", 0);
    setAppState("globalFindIndex", -1);
    return;
  }

  const root = document.querySelector("main.main-area");
  if (!root) {
    setAppState("globalFindCount", 0);
    return;
  }

  const lower = q.toLowerCase();

  // 1) 普通文本节点（排除浮层自身、隐藏、脚本样式）
  const textNodes: Text[] = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const n = node as Text;
      if (!n.nodeValue || !n.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
      const parent = n.parentElement;
      if (!parent) return NodeFilter.FILTER_REJECT;
      if (parent.closest(".global-find")) return NodeFilter.FILTER_REJECT;
      if (!isVisible(parent)) return NodeFilter.FILTER_REJECT;
      if (SKIP_TAGS.has(parent.tagName)) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  } as NodeFilter);
  let tn: Node | null;
  while ((tn = walker.nextNode())) textNodes.push(tn as Text);

  for (const node of textNodes) {
    const text = node.nodeValue || "";
    const lowerText = text.toLowerCase();
    let from = 0;
    let idx = lowerText.indexOf(lower, from);
    while (idx !== -1) {
      const range = document.createRange();
      range.setStart(node, idx);
      range.setEnd(node, idx + q.length);
      matches.push({ kind: "text", range });
      from = idx + q.length;
      idx = lowerText.indexOf(lower, from);
    }
  }

  // 2) 表单输入（INPUT/TEXTAREA/SELECT）的 value / placeholder —— 纳入可见文本搜索
  //    注意：CSS Highlight API 无法高亮 input 内部文本，改用外框高亮 + 滚动定位
  const fields = root.querySelectorAll<HTMLElement>("input, textarea, select");
  fields.forEach((el) => {
    if (el.closest(".global-find")) return;
    if (!isVisible(el)) return;
    const f = el as HTMLInputElement;
    const val = f.value || f.placeholder || "";
    if (val && val.toLowerCase().includes(lower)) {
      matches.push({ kind: "input", el });
    }
  });

  setAppState("globalFindCount", matches.length);
  if (matches.length > 0) {
    current = 0;
    setAppState("globalFindIndex", 1);
    applyHighlights();
    scrollToCurrent();
  } else {
    setAppState("globalFindIndex", -1);
  }

  if (import.meta.env?.DEV) {
    console.debug(`[GlobalFind] query=${JSON.stringify(q)} 命中=${matches.length}`);
  }
}

function applyHighlights() {
  try {
    const w = window as GlobalFindWindow;
    if (!(w.CSS && w.CSS.highlights)) {
      console.warn("[GlobalFind] 当前环境不支持 CSS Custom Highlight API，跳过高亮");
      return;
    }
    const textRanges = matches
      .filter((m): m is { kind: "text"; range: Range } => m.kind === "text")
      .map((m) => m.range);
    const all = new w.Highlight(...textRanges);
    w.CSS.highlights.set(HL_ALL, all);
    const cur = matches[current];
    if (cur && cur.kind === "text") {
      const c = new w.Highlight(cur.range);
      w.CSS.highlights.set(HL_CURRENT, c);
    } else {
      w.CSS.highlights.delete(HL_CURRENT);
    }
  } catch (e) {
    console.warn("[GlobalFind] 注册高亮失败:", e);
  }
  // input 命中项：清除旧外框，给当前项加
  document
    .querySelectorAll(".global-find-input-hit")
    .forEach((e) => e.classList.remove("global-find-input-hit"));
  const cur = matches[current];
  if (cur && cur.kind === "input") cur.el.classList.add("global-find-input-hit");
}

function scrollToCurrent() {
  const cur = matches[current];
  if (!cur) return;
  if (cur.kind === "text") {
    const el = cur.range.startContainer.parentElement;
    if (el && typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  } else {
    const el = cur.el;
    if (typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ block: "center", behavior: "smooth" });
    }
    if (typeof (el as HTMLInputElement).focus === "function") {
      (el as HTMLInputElement).focus();
    }
  }
}

function move(delta: number) {
  if (matches.length === 0) return;
  // 循环跳转：最后一个的下一个 → 第一个；第一个的上一个 → 最后一个
  current = (current + delta + matches.length) % matches.length;
  if (current < 0) current = 0;
  setAppState("globalFindIndex", current + 1);
  applyHighlights();
  scrollToCurrent();
  if (import.meta.env?.DEV) {
    console.log(`[GlobalFind] 跳转到匹配 ${current + 1}/${matches.length} (delta=${delta})`);
  }
}

function close() {
  clearHighlights();
  matches = [];
  current = -1;
  setAppState("globalFindOpen", false);
  setAppState("globalFindCount", 0);
  setAppState("globalFindIndex", -1);
}

export function GlobalFind() {
  let inputRef: HTMLInputElement | undefined;

  // 打开时聚焦输入框
  createEffect(() => {
    if (appState.globalFindOpen) {
      // 等下一帧确保已渲染
      requestAnimationFrame(() => inputRef?.focus());
    } else {
      clearHighlights();
    }
  });

  // 界面切换（activeView 变化）时清除残留高亮，避免跨页残留
  createEffect(() => {
    void appState.activeView; // 读取以建立响应式依赖（Solid 惯用法）
    if (!appState.globalFindOpen) clearHighlights();
  });

  onCleanup(() => {
    if (debounceTimer) clearTimeout(debounceTimer);
    clearHighlights();
  });

  function onInput(e: InputEvent & { currentTarget: HTMLInputElement }) {
    const v = e.currentTarget.value;
    setAppState("globalFindQuery", v);
    if (debounceTimer) clearTimeout(debounceTimer);
    if (!v.trim()) {
      clearHighlights();
      matches = [];
      current = -1;
      setAppState("globalFindCount", 0);
      setAppState("globalFindIndex", -1);
      return;
    }
    debounceTimer = window.setTimeout(() => runSearch(v), 250);
  }

  function onKeyDown(e: KeyboardEvent & { currentTarget: HTMLInputElement }) {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "Enter") {
      e.preventDefault();
      move(e.shiftKey ? -1 : 1);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      move(1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      move(-1);
    }
  }

  return (
    <Show when={appState.globalFindOpen}>
      <div class="global-find" role="dialog" aria-label="查找">
        <div class="global-find-input-row">
          <input
            ref={inputRef}
            class="find-input"
            type="text"
            placeholder="查找（main 区可见文本）"
            value={appState.globalFindQuery}
            onInput={onInput}
            onKeyDown={onKeyDown}
            autofocus
          />
          <Show when={appState.globalFindCount > 0}>
            <span class="global-find-count">
              {appState.globalFindIndex} / {appState.globalFindCount}
            </span>
          </Show>
          <Show when={appState.globalFindCount === 0 && appState.globalFindQuery.trim()}>
            <span class="global-find-count global-find-count--none">无匹配</span>
          </Show>
          <Show when={appState.globalFindCount > 0}>
            <div class="global-find-nav">
              <button
                class="global-find-prev"
                title="上一个匹配 (Shift+Enter / ↑)"
                onClick={() => move(-1)}
              >
                ↑
              </button>
              <button
                class="global-find-next"
                title="下一个匹配 (Enter / ↓) — 到末尾后循环到第一个"
                onClick={() => move(1)}
              >
                ↓
              </button>
            </div>
          </Show>
          <button class="global-find-close" title="关闭 (Esc)" onClick={close}>
            ×
          </button>
        </div>
      </div>
    </Show>
  );
}
