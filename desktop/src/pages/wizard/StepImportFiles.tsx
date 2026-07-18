import { open } from "@tauri-apps/plugin-dialog";
import { invoke } from "@tauri-apps/api/core";
import { Icon } from "../../components/icons/Icon";

interface StepImportFilesProps {
  gtInputDir: string;
  importedFiles: string[];
  onFileDrop: (e: DragEvent) => void;
  onFilePick: () => void;
  onOpenInputFolder: () => void;
}

export function StepImportFiles(props: StepImportFilesProps) {
  function handleDragOver(e: DragEvent) {
    e.preventDefault();
    (e.currentTarget as HTMLElement).classList.add("drop-zone--over");
  }
  function handleDragLeave(e: DragEvent) {
    (e.currentTarget as HTMLElement).classList.remove("drop-zone--over");
  }
  function handleDrop(e: DragEvent) {
    e.preventDefault();
    (e.currentTarget as HTMLElement).classList.remove("drop-zone--over");
    props.onFileDrop(e);
  }

  return (
    <div class="wizard-panel">
      <h3 class="wizard-panel-title">导入文件</h3>
      <p class="wizard-panel-desc">将待翻译的文件导入到项目的 gt_input 目录中，也可以跳过此步骤稍后手动添加。</p>
      <div class="drop-zone"
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        <div class="drop-zone__icon">
          <Icon name="folder" size={48} />
        </div>
        <div class="drop-zone__text">拖放文件到此处导入</div>
      </div>
      <div class="wizard-actions">
        <button class="btn btn--sm" onClick={props.onFilePick}>选择文件</button>
        <button class="btn btn--sm" onClick={props.onOpenInputFolder} disabled={!props.gtInputDir}>打开输入文件夹</button>
      </div>
      {props.importedFiles.length > 0 && (
        <ul class="wizard-file-list">
          {props.importedFiles.map((f, i) => (<li class="wizard-file-list__item" key={i}>{f}</li>))}
        </ul>
      )}
    </div>
  );
}
