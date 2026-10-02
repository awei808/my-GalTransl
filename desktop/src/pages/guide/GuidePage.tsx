/**
 * GuidePage — 使用指南（帮助菜单「翻译指南」与各页「指南」入口的落地页）。
 *
 * 布局：左列篇目目录，右列 markdown 渲染内容。篇目来自后端 /api/guides
 * （guides/ 目录白名单），内容按需加载。appState.guideTarget 指定打开篇目，
 * 消费后清空（与 settingsScrollTarget 同一套"读后即清"口径）。
 */
import { createEffect, createSignal, For, on, onMount, Show } from "solid-js";
import { appState, setAppState } from "../../stores/appStore";
import { fetchGuideContent, fetchGuides, fetchVersion } from "../../lib/api/general";
import { getErrorMessage } from "../../lib/errors";
import { guideTitle, renderMarkdown } from "./guideUtils";

type ListState = "loading" | "ready" | "error";
type ContentState = "idle" | "loading" | "ready" | "error";

export function GuidePage() {
  const [guides, setGuides] = createSignal<string[]>([]);
  const [titles, setTitles] = createSignal<Record<string, string>>({});
  const [listState, setListState] = createSignal<ListState>("loading");
  const [selected, setSelected] = createSignal<string | null>(null);
  const [contentHtml, setContentHtml] = createSignal("");
  const [contentState, setContentState] = createSignal<ContentState>("idle");
  const [contentError, setContentError] = createSignal("");
  const [version, setVersion] = createSignal("");

  // 内容请求竞态守卫：快速切换篇目时，过期响应直接丢弃
  let requestSeq = 0;
  let contentRef: HTMLDivElement | undefined;

  onMount(() => {
    void loadList();
    fetchVersion()
      .then((v) => setVersion(v.version))
      .catch(() => setVersion(""));
  });

  async function loadList() {
    setListState("loading");
    try {
      const names = await fetchGuides();
      setGuides(names);
      setListState("ready");
      // 列表就绪后消费 guideTarget（消费即清；空目录也消费，避免残留）。
      // guideTarget 可能已被下方 effect 提前消费（读到 null），也可能因列表
      // 未就绪而留待此处处理，两条路径都收口在这一次读取。
      const target = appState.guideTarget;
      setAppState("guideTarget", null);
      const first = target && names.includes(target) ? target : names[0];
      if (!selected() && first) {
        prefetchTitles(names, first);
        setSelected(first);
        void loadContent(first);
      } else {
        prefetchTitles(names);
      }
    } catch (e) {
      setListState("error");
      console.error("加载指南列表失败", e);
    }
  }

  /** 后台预取各篇标题，避免目录里未打开过的篇目显示原始文件名；失败保留文件名。
      skip 为即将由 loadContent 加载的篇目（避免同一篇重复请求）。 */
  function prefetchTitles(names: string[], skip?: string) {
    for (const name of names) {
      if (name === skip || titles()[name]) continue;
      fetchGuideContent(name)
        .then((res) => setTitles((t) => ({ ...t, [name]: guideTitle(name, res.content) })))
        .catch(() => {});
    }
  }

  // 已在指南页时 guideTarget 变化（如点其他页面的「指南」链接）→ 打开对应篇目。
  // 列表未就绪时不消费，交给 loadList 完成后的兜底选择，避免与列表加载竞态。
  createEffect(
    on(
      () => appState.guideTarget,
      (target) => {
        if (!target) return;
        if (listState() !== "ready") return;
        setAppState("guideTarget", null);
        if (!guides().includes(target) || target === selected()) return;
        setSelected(target);
        void loadContent(target);
      },
    ),
  );

  async function loadContent(name: string) {
    const seq = ++requestSeq;
    setContentState("loading");
    try {
      const res = await fetchGuideContent(name);
      if (seq !== requestSeq) return;
      setTitles((t) => ({ ...t, [name]: guideTitle(name, res.content) }));
      setContentHtml(renderMarkdown(res.content));
      setContentState("ready");
      if (contentRef) contentRef.scrollTop = 0;
    } catch (e) {
      if (seq !== requestSeq) return;
      setContentError(getErrorMessage(e));
      setContentState("error");
      console.error(`加载指南 ${name} 失败`, e);
    }
  }

  function select(name: string) {
    // 已选中且内容正常时不重复请求；内容处于错误态时允许再次点击重试
    if (name === selected() && contentState() !== "error") return;
    setSelected(name);
    void loadContent(name);
  }

  return (
    <div class="guide-page">
      <aside class="guide-menu">
        <div class="guide-menu-header">
          <h3>使用指南</h3>
          <Show when={version()}>
            <span class="guide-version">{version()}</span>
          </Show>
        </div>
        <Show
          when={listState() !== "loading"}
          fallback={<p class="guide-menu-empty">加载中…</p>}
        >
          <Show
            when={listState() === "ready"}
            fallback={
              <div class="guide-menu-empty">
                <p>指南列表加载失败</p>
                <button class="guide-retry-btn" onClick={() => void loadList()}>
                  重试
                </button>
              </div>
            }
          >
            <Show
              when={guides().length > 0}
              fallback={<p class="guide-menu-empty">暂无指南内容</p>}
            >
              <ul class="guide-menu-list">
                <For each={guides()}>
                  {(name) => (
                    <li>
                      <button
                        class={`guide-menu-item ${selected() === name ? "active" : ""}`}
                        onClick={() => select(name)}
                      >
                        {titles()[name] ?? name}
                      </button>
                    </li>
                  )}
                </For>
              </ul>
            </Show>
          </Show>
        </Show>
      </aside>
      <div class="guide-content" ref={contentRef}>
        <Show
          when={selected()}
          fallback={
            <div class="guide-placeholder">
              <p>从左侧选择一篇指南开始阅读。</p>
            </div>
          }
        >
          <Show
            when={contentState() === "ready"}
            fallback={
              <div class="guide-placeholder">
                {contentState() === "loading" ? (
                  <p>加载中…</p>
                ) : (
                  <div class="guide-error-box">
                    <p>{`指南内容加载失败：${contentError() || "请检查后端服务。"}`}</p>
                    <button
                      class="guide-retry-btn"
                      onClick={() => {
                        const current = selected();
                        if (current) void loadContent(current);
                      }}
                    >
                      重试
                    </button>
                  </div>
                )}
              </div>
            }
          >
            <div class="guide-article markdown-body" innerHTML={contentHtml()} />
          </Show>
        </Show>
      </div>
    </div>
  );
}
