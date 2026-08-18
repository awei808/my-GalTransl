/**
 * 缓存文件夹监控：轮询 /files，按文件大小 diff 检测变动。
 * - 结构/大小变化 → 更新 appStore.cacheTree（文件浏览器自动重渲染）。
 * - 仅当“当前打开的文件”大小变化（或新增） → bump appStore.cacheVersion，
 *   驱动 ReviewPage 局部重新拉取该文件的条目并渲染。
 */
import { appState, setAppState } from "../stores/appStore";
import { fetchProjectFiles } from "../lib/api/project";
import type { FileNode } from "../lib/api/types";

let timer: ReturnType<typeof setInterval> | undefined;
let lastSig = "";
let lastSizes: Record<string, number> = {};
let activePid: string | null = null;

const POLL_INTERVAL_MS = 3000;

function collectSizes(node: FileNode, out: Record<string, number>) {
  if (node.is_file) out[node.path] = node.size;
  else if (node.children) {
    for (const c of node.children) collectSizes(c, out);
  }
}

/** 用 路径:大小:条数 拼接出结构签名，仅在真正变动时才更新 store，避免无谓重渲染 */
function signature(tree: FileNode[]): string {
  const parts: string[] = [];
  const walk = (ns: FileNode[]) => {
    for (const n of ns) {
      if (n.is_file) parts.push(`${n.path}:${n.size}:${n.entry_count ?? -1}`);
      else {
        parts.push(`d:${n.path}`);
        walk(n.children ?? []);
      }
    }
  };
  walk(tree);
  return parts.join("|");
}

async function tick() {
  const pid = activePid;
  if (!pid) return;
  try {
    const res = await fetchProjectFiles(pid);
    // await 期间可能已切换/停止监控：丢弃过期响应，避免旧项目数据写入 cacheTree/版本号
    if (activePid !== pid) return;
    const tree = res.cache_files ?? [];

    const sizes: Record<string, number> = {};
    for (const n of tree) collectSizes(n, sizes);

    // 仅当“当前打开的文件”大小变化（或首次出现）时，触发局部刷新
    let openChanged = false;
    // 任一非当前打开文件变化 → 递增 problemVersion，驱动问题侧栏刷新
    let otherChanged = false;
    const open = appState.activeFilePath;
    if (open) {
      const prev = lastSizes[open];
      const cur = sizes[open];
      if (cur !== undefined && (prev === undefined || prev !== cur)) {
        openChanged = true;
      }
    }
    for (const [p, cur] of Object.entries(sizes)) {
      if (p === open) continue;
      const prev = lastSizes[p];
      if (cur !== undefined && (prev === undefined || prev !== cur)) {
        otherChanged = true;
        break;
      }
    }

    const sig = signature(tree);
    if (sig !== lastSig) {
      setAppState("cacheTree", tree);
      lastSig = sig;
    }

    lastSizes = sizes;
    if (openChanged) {
      setAppState("cacheVersion", (v: number) => v + 1);
    }
    if (openChanged || otherChanged) {
      setAppState("problemVersion", (v: number) => v + 1);
    }
  } catch {
    // 轮询失败静默忽略（后端可能正在重启等）
  }
}

export function startCacheWatcher(pid: string) {
  if (activePid === pid && timer) return;
  stopCacheWatcher();
  activePid = pid;
  lastSig = "";
  lastSizes = {};
  tick();
  timer = setInterval(tick, POLL_INTERVAL_MS);
}

export function stopCacheWatcher() {
  if (timer) {
    clearInterval(timer);
    timer = undefined;
  }
  activePid = null;
  lastSig = "";
  lastSizes = {};
}
