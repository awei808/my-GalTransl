import { Button } from '../../components/Button';
import { Panel } from '../../components/Panel';

type StepImportFilesProps = {
  gtInputDir: string;
  importedFiles: string[];
  onFileDrop: (e: React.DragEvent<HTMLDivElement>) => void;
  onFilePick: () => void;
  onOpenInputFolder: () => void;
};

export function StepImportFiles({ gtInputDir, importedFiles, onFileDrop, onFilePick, onOpenInputFolder }: StepImportFilesProps) {
  return (
    <Panel title="导入文件" description="将待翻译的文件导入到项目的 gt_input 目录中，也可以跳过此步骤稍后手动添加。">
      <div className="drop-zone"
        onDragOver={(e) => { e.preventDefault(); e.currentTarget.classList.add('drop-zone--over'); }}
        onDragLeave={(e) => { e.currentTarget.classList.remove('drop-zone--over'); }}
        onDrop={(e) => void onFileDrop(e)}>
        <div className="drop-zone__icon">📁</div>
        <div className="drop-zone__text">拖放文件到此处导入</div>
      </div>
      <div className="wizard-actions">
        <Button variant="secondary" onClick={onFilePick}>选择文件</Button>
        <Button variant="secondary" onClick={onOpenInputFolder} disabled={!gtInputDir}>打开输入文件夹</Button>
      </div>
      {importedFiles.length > 0 && (
        <ul className="wizard-file-list">
          {importedFiles.map((f, i) => (<li key={i} className="wizard-file-list__item">{f}</li>))}
        </ul>
      )}
    </Panel>
  );
}
