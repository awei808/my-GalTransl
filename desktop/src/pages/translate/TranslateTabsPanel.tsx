import type { FileProgress } from '../../lib/api';
import { EmptyState } from '../../components/page-state';
import { RuntimeErrorRow, FileProgressRow } from '../translateRuntimeShared';
import type { ProjectRuntimeErrorEntry } from '../../lib/api';

type RetranslListItem = { key: string; count: number };

type TranslateTabsProps = {
  rightTab: 'errors' | 'files' | 'retransl';
  onTabChange: (tab: 'errors' | 'files' | 'retransl') => void;
  recentErrors: ProjectRuntimeErrorEntry[];
  prioritizedRuntimeFiles: FileProgress[];
  unfinishedRuntimeFilesCount: number;
  selectedSuccessFileSet: Set<string>;
  onToggleSuccessFileFilter: (filename: string) => void;
  retranslKeys: RetranslListItem[];
  continuousRetranslEnabled: boolean;
  onToggleContinuousRetransl: () => void;
  onNavigateToRetranslSettings: () => void;
};

export function TranslateTabsPanel({
  rightTab, onTabChange, recentErrors,
  prioritizedRuntimeFiles, unfinishedRuntimeFilesCount,
  selectedSuccessFileSet, onToggleSuccessFileFilter,
  retranslKeys, continuousRetranslEnabled, onToggleContinuousRetransl,
  onNavigateToRetranslSettings,
}: TranslateTabsProps) {
  return (
    <div className="ptv2-main__side">
      <section className="panel ptv2-tabpanel">
        <header className="panel__header ptv2-tabpanel__header">
          <div role="tablist" aria-label="辅助信息" className="ptv2-tabs">
            <button type="button" role="tab" aria-selected={rightTab === 'errors'}
              className={`ptv2-tab${rightTab === 'errors' ? ' ptv2-tab--active' : ''}`}
              onClick={() => onTabChange('errors')}>
              <span className="ptv2-tab__label">最近错误</span>
              {recentErrors.length > 0 ? <span className="ptv2-tab__badge ptv2-tab__badge--danger">{recentErrors.length}</span> : null}
            </button>
            <button type="button" role="tab" aria-selected={rightTab === 'files'}
              className={`ptv2-tab${rightTab === 'files' ? ' ptv2-tab--active' : ''}`}
              onClick={() => onTabChange('files')}>
              <span className="ptv2-tab__label">文件进度</span>
              {unfinishedRuntimeFilesCount > 0 ? <span className="ptv2-tab__badge">{unfinishedRuntimeFilesCount}</span> : null}
            </button>
            <button type="button" role="tab" aria-selected={rightTab === 'retransl'}
              className={`ptv2-tab${rightTab === 'retransl' ? ' ptv2-tab--active' : ''}`}
              onClick={() => onTabChange('retransl')}>
              <span className="ptv2-tab__label">重翻词条</span>
              {retranslKeys.length > 0 ? <span className="ptv2-tab__badge">{retranslKeys.length}</span> : null}
            </button>
          </div>
        </header>
        <div className="panel__body ptv2-tabpanel__body">
          <div className="ptv2-tabpanel__pane" key={rightTab}>
            {rightTab === 'errors' ? (
              recentErrors.length ? (
                <div className="runtime-event-list runtime-event-list--error ptv2-eventlist">
                  {recentErrors.map((entry) => (<RuntimeErrorRow entry={entry} key={entry.id} />))}
                </div>
              ) : (
                <EmptyState title="最近没有错误" description="接口错误、解析错误会显示在这里。" />
              )
            ) : rightTab === 'files' ? (
              prioritizedRuntimeFiles.length > 0 ? (
                <div className="ptv2-filelist">
                  {prioritizedRuntimeFiles.map((file) => (
                    <FileProgressRow key={file.filename} file={file}
                      isSuccessFileFilterActive={selectedSuccessFileSet.has(file.filename)}
                      onToggleSuccessFileFilter={onToggleSuccessFileFilter} />
                  ))}
                </div>
              ) : (
                <EmptyState title="暂无文件进度" description="启动翻译后，文件级进度会在这里展开。" />
              )
            ) : (
              <div className="ptv2-retransl-pane">
                <div className="ptv2-retransl-auto">
                  <button type="button" role="switch" aria-checked={continuousRetranslEnabled}
                    className={`ptv2-retransl-auto__toggle${continuousRetranslEnabled ? ' ptv2-retransl-auto__toggle--on' : ''}`}
                    onClick={onToggleContinuousRetransl}>
                    <span className="ptv2-retransl-auto__toggle-track" aria-hidden="true"><span className="ptv2-retransl-auto__toggle-thumb" /></span>
                    <span className="ptv2-retransl-auto__toggle-label">自动持续重翻</span>
                  </button>
                  <p className="ptv2-retransl-auto__hint">翻译结束后，自动启动翻译，直到连续3次待重翻的句子仍不减少。</p>
                </div>
                {retranslKeys.length > 0 ? (
                  <ul className="ptv2-retransl-list">
                    {retranslKeys.map((item, idx) => (
                      <li className="ptv2-retransl-list__item ptv2-retransl-list__item--link" key={`${idx}-${item.key}`}
                        role="button" tabIndex={0} title="点击跳转到配置编辑-重翻关键字"
                        onClick={onNavigateToRetranslSettings}
                        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onNavigateToRetranslSettings(); } }}>
                        <span className="ptv2-retransl-list__index">{idx + 1}</span>
                        <span className="ptv2-retransl-list__text">{item.key}</span>
                        <span className="ptv2-retransl-list__count">{item.count} 句</span>
                        <span className="ptv2-retransl-list__arrow">›</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <EmptyState title="暂无重翻词条" description="在项目配置「重翻关键字」中添加后，启动翻译时命中的句子会被重新翻译。" />
                )}
              </div>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
