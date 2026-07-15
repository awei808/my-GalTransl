/**
 * DictEntryGroupCard — grouped card-based dictionary entry editor.
 */
import type { CSSProperties } from 'react';
import { Icon } from '../icons';
import { getTypeLabel, getFieldLabels, type DictRowGroup, type DictTab, type DictRowType } from './dictUtils';

type DictEntryGroupCardProps = {
  group: DictRowGroup;
  tab: DictTab;
  onCellChange: (rowIndex: number, cellIndex: number, value: string) => void;
  onDelete: (rowIndex: number) => void;
  onAddRow: (rowType: DictRowType, insertAfterRowIndex: number) => void;
};

export function DictEntryGroupCard({
  group,
  tab,
  onCellChange,
  onDelete,
  onAddRow,
}: DictEntryGroupCardProps) {
  const labels = getFieldLabels(group.type, tab);
  const tableStyle = { '--dict-column-count': labels.length } as CSSProperties;

  return (
    <article className={`dict-card dict-card--${group.type} dict-card--grouped`}>
      <div className="dict-card__header">
        <div className="dict-card__badges">
          <span className={`dict-card__pill dict-card__pill--${group.type}`}>
            {getTypeLabel(group.type, tab)}
          </span>
          <span className="dict-card__pill dict-card__pill--index">{group.items.length}条</span>
        </div>
      </div>

      <div className="dict-card__table" style={tableStyle}>
        <div className="dict-card__table-head">
          <div className="dict-card__head-cell dict-card__head-cell--index">ID</div>
          {labels.map((label, ci) => (
            <div key={ci} className="dict-card__head-cell">{label || `列${ci + 1}`}</div>
          ))}
        </div>

        {group.items.map(({ row, rowIndex }) => (
          <div key={`${rowIndex}`} className="dict-card__table-row">
            <div className="dict-card__cell dict-card__cell--index">#{rowIndex + 1}</div>
            {labels.map((_label, ci) => (
              <div key={ci} className="dict-card__cell">
                <input
                  className="dict-card__input"
                  value={row.values[ci] ?? ''}
                  onChange={(e) => onCellChange(rowIndex, ci, e.target.value)}
                  placeholder={_label || `列${ci + 1}`}
                />
              </div>
            ))}
            <button
              type="button"
              className="dict-card__row-delete"
              onClick={() => onDelete(rowIndex)}
              title="删除此条"
            >
              ✕
            </button>
          </div>
        ))}

        <div className="dict-card__table-add-row">
          <button
            type="button"
            className="dict-card__add-row-btn"
            onClick={() => onAddRow(group.type, group.items[group.items.length - 1]?.rowIndex ?? -1)}
            title="新增同类型条目"
          >
            +
          </button>
        </div>
      </div>
    </article>
  );
}
