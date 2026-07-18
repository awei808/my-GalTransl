export function TranslateConsole() {
  return (
    <div class="page page-translate">
      <div class="translate-header">
        <div class="translate-stats">
          <p>翻译控制台 — 项目已加载</p>
        </div>
        <div class="translate-actions">
          <button class="btn btn--primary">启动流程</button>
        </div>
      </div>
      <div class="translate-body">
        <div class="translate-panel translate-prompt">
          <div class="panel-header">当前提示词</div>
          <div class="panel-content">等待翻译开始…</div>
        </div>
        <div class="translate-divider" />
        <div class="translate-panel translate-preview">
          <div class="panel-header">译文拼接</div>
          <div class="panel-content">等待翻译开始…</div>
        </div>
      </div>
    </div>
  );
}
