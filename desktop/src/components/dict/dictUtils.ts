/**
 * Dictionary utility functions — row parsing, formatting, tab/file helpers.
 */
import type { DictFileContent, DictionaryCategory } from '../../lib/api';

export type DictRowType = 'normal' | 'conditional' | 'situation' | 'gpt' | 'comment' | 'blank';

export type DictRow = {
  type: DictRowType;
  values: string[];
  raw: string;
};

export type DictRowWithIndex = {
  row: DictRow;
  rowIndex: number;
};

export type DictRowGroup = {
  type: DictRowType;
  items: DictRowWithIndex[];
};

export type DictTab = DictionaryCategory;

export const PROJECT_DIR_MARKER = '(project_dir)';

export function stripProjectDirMarker(name: string): string {
  return name.replace(PROJECT_DIR_MARKER, '').trim();
}

export function getFilesByTab(
  data: { dict_contents: Record<string, DictFileContent>; pre_dict_files: string[]; gpt_dict_files: string[]; post_dict_files: string[] } | null,
  tab: DictTab,
): string[] {
  if (!data) return [];
  const files = tab === 'pre' ? data.pre_dict_files : tab === 'gpt' ? data.gpt_dict_files : data.post_dict_files;
  return [...files].sort((a, b) => {
    const aMtime = data.dict_contents[a]?.mtime ?? -1;
    const bMtime = data.dict_contents[b]?.mtime ?? -1;
    if (aMtime !== bMtime) return bMtime - aMtime;
    return stripProjectDirMarker(a).localeCompare(stripProjectDirMarker(b));
  });
}

export function parseRows(text: string, tab: DictTab): DictRow[] {
  const lines = text.split('\n');
  return lines.map((line) => {
    if (!line.trim() && !line.includes('|')) return { type: 'blank', values: [], raw: line };
    if (line.startsWith('//') || line.startsWith('#') || line.startsWith('\\\\')) {
      return { type: 'comment', values: [line], raw: line };
    }
    const parts = line.split('|');
    if (tab === 'gpt') {
      const [src = '', dst = '', ...notes] = parts;
      return { type: 'gpt', values: [src, dst, notes.join('|')], raw: line };
    }
    if (
      parts.length >= 4
      && ['pre_jp', 'post_jp', 'pre_zh', 'post_zh', 'pre_src', 'post_src', 'pre_dst', 'post_dst'].includes(parts[0])
    ) {
      const [target = '', cond = '', search = '', replace = '', ...rest] = parts;
      return { type: 'conditional', values: [target, cond, search, replace, rest.join('|')], raw: line };
    }
    if (parts.length >= 3 && ['diag', 'mono'].includes(parts[0])) {
      const [scene = '', search = '', ...replace] = parts;
      return { type: 'situation', values: [scene, search, replace.join('|')], raw: line };
    }
    const [search = '', replace = '', ...rest] = parts;
    return { type: 'normal', values: [search, replace, rest.join('|')], raw: line };
  });
}

export function rowsToText(rows: DictRow[]): string {
  return rows.map((row) => {
    if (row.type === 'blank') return '';
    if (row.type === 'comment') return row.values[0] ?? row.raw;
    return row.values.join('|');
  }).join('\n');
}

export function getTypeLabel(type: DictRowType, _tab: DictTab): string {
  if (type === 'comment') return '注释';
  if (type === 'blank') return '空行';
  if (type === 'gpt') return 'GPT';
  if (type === 'normal') return '普通';
  if (type === 'conditional') return '条件';
  if (type === 'situation') return '场景';
  return type;
}

export function getFieldLabels(type: DictRowType, _tab: DictTab): string[] {
  if (type === 'gpt') return ['原文', '译文', '解释(可空)'];
  if (type === 'normal') return ['搜索', '替换', '备注'];
  if (type === 'conditional') return ['目标', '条件', '搜索', '替换', '备注'];
  if (type === 'situation') return ['场景', '搜索', '替换'];
  if (type === 'comment') return ['内容'];
  return [];
}
