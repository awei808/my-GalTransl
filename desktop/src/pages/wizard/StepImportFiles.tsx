import { onMount, onCleanup } from "solid-js";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import { Icon } from "../../components/icons/Icon";

interface StepImportFilesProps {
  gtInputDir: string;
  importedFiles: string[];
  onImportPaths: (paths: string[]) => void;
  onFilePick: () => void;
  onOpenInputFolder: () => void;
}

export function StepImportFiles(props: StepImportFilesProps) {
  let dropZoneRef: HTMLDivElement | undefined;

  function highlight(on: boolean) {
    dropZoneRef?.classList.toggle("drop-zone--over", on);
  }

  onMount(async () => {
    // Tauri v2 中 HTML5 拖放拿不到真实文件路径（File.path 被屏蔽），
    // 必须用原生 onDragDropEvent 获取操作系统级真实路径。
    try {
      const unlisten = await getCurrentWebview().onDragDropEvent((event) => {
        const payload = event.payload;
        if (payload.type === "over" || payload.type === "enter") {
          highlight(true);
        } else if (payload.type === "leave") {
          highlight(false);
        } else if (payload.type === "drop") {
          highlight(false);
          const paths = (payload.paths || []).filter(
            (p) => typeof p === "string" && p.trim()
          );
          if (paths.length > 0) props.onImportPaths(paths);
        }
      });
      onCleanup(() => {
        unlisten();
      });
    } catch {
      // 原生拖放不可用时静默降级（文件选择按钮仍可正常使用）
    }
  });

  return (
    <div class="wizard-panel">
      <h3 class="wizard-panel-title">导入文件</h3>
      <p class="wizard-panel-desc">
        将待翻译的文件拖放到此处（或下方窗口任意位置）即可导入到项目的 gt_input
        目录，也可以跳过此步骤稍后手动添加。
      </p>
      <div
        class="drop-zone"
        ref={dropZoneRef}
        onDragOver={(e) => {
          e.preventDefault();
          highlight(true);
        }}
        onDragLeave={() => highlight(false)}
        onDrop={(e) => e.preventDefault()}
      >
        <div class="drop-zone__icon">
          <Icon name="folder" size={48} />
        </div>
        <div class="drop-zone__text">拖放文件到此处导入</div>
      </div>
      <div class="wizard-actions">
        <button class="btn btn--sm" onClick={props.onFilePick}>
          选择文件
        </button>
        <button
          class="btn btn--sm"
          onClick={props.onOpenInputFolder}
          disabled={!props.gtInputDir}
        >
          打开输入文件夹
        </button>
      </div>
      {props.importedFiles.length > 0 && (
        <ul class="wizard-file-list">
          {props.importedFiles.map((f) => (
            <li class="wizard-file-list__item">
              {f}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
