import { describe, it, expect } from 'vitest';
import {
  stripProjectDirMarker,
  getFilesByTab,
  parseRows,
  rowsToText,
  getTypeLabel,
  getFieldLabels,
  type DictRow,
  type DictTab,
} from '../dictUtils';

function makeContent(lines: string[], mtime = 0) {
  return { path: '/test/file.txt', lines, count: lines.length, mtime };
}

// ── stripProjectDirMarker ──

describe('stripProjectDirMarker', () => {
  it('removes (project_dir) prefix', () => {
    expect(stripProjectDirMarker('(project_dir)myfile.txt')).toBe('myfile.txt');
  });

  it('trims whitespace after removal', () => {
    expect(stripProjectDirMarker('(project_dir)  myfile.txt')).toBe('myfile.txt');
  });

  it('does not modify names without the marker', () => {
    expect(stripProjectDirMarker('myfile.txt')).toBe('myfile.txt');
  });

  it('handles empty string', () => {
    expect(stripProjectDirMarker('')).toBe('');
  });

  it('handles only the marker', () => {
    expect(stripProjectDirMarker('(project_dir)')).toBe('');
  });

  it('handles marker in the middle of the name (only removes first occurrence)', () => {
    // replace() only removes the first occurrence
    expect(stripProjectDirMarker('prefix(project_dir)suffix(project_dir)end')).toBe('prefixsuffix(project_dir)end');
  });

  it('handles marker with no trimming needed', () => {
    expect(stripProjectDirMarker('(project_dir)file.txt')).toBe('file.txt');
  });
});

// ── getFilesByTab ──

describe('getFilesByTab', () => {
  const sampleData = {
    pre_dict_files: ['pre_b.txt', 'pre_a.txt'],
    gpt_dict_files: ['gpt_1.txt'],
    post_dict_files: ['post_z.txt', 'post_a.txt'],
    dict_contents: {
      'pre_a.txt': makeContent(['a'], 100),
      'pre_b.txt': makeContent(['b'], 200),
      'gpt_1.txt': makeContent(['g'], 50),
      'post_a.txt': makeContent(['pa'], 10),
      'post_z.txt': makeContent(['pz'], 30),
    },
  };

  it('returns pre files sorted by mtime desc', () => {
    const files = getFilesByTab(sampleData, 'pre');
    expect(files).toEqual(['pre_b.txt', 'pre_a.txt']);
  });

  it('returns gpt files', () => {
    const files = getFilesByTab(sampleData, 'gpt');
    expect(files).toEqual(['gpt_1.txt']);
  });

  it('returns post files sorted by mtime desc', () => {
    const files = getFilesByTab(sampleData, 'post');
    expect(files).toEqual(['post_z.txt', 'post_a.txt']);
  });

  it('returns empty array for null data', () => {
    expect(getFilesByTab(null, 'pre')).toEqual([]);
    expect(getFilesByTab(null, 'gpt')).toEqual([]);
    expect(getFilesByTab(null, 'post')).toEqual([]);
  });

  it('returns empty array for empty file lists', () => {
    const empty = { ...sampleData, pre_dict_files: [], gpt_dict_files: [], post_dict_files: [] };
    expect(getFilesByTab(empty, 'pre')).toEqual([]);
  });

  it('sorts alphabetically when mtime is equal (no mtime key)', () => {
    const data = {
      pre_dict_files: ['z.txt', 'a.txt', 'm.txt'],
      gpt_dict_files: [], post_dict_files: [],
      dict_contents: {},
    };
    const files = getFilesByTab(data, 'pre');
    // No mtime = -1 for all, sorts alphabetically
    expect(files[0]).toBe('a.txt');
    expect(files[2]).toBe('z.txt');
  });

  it('handles file not in dict_contents (mtime = -1)', () => {
    const data = {
      pre_dict_files: ['missing.txt', 'present.txt'],
      gpt_dict_files: [], post_dict_files: [],
      dict_contents: { 'present.txt': makeContent([''], 100) },
    };
    const files = getFilesByTab(data, 'pre');
    // present.txt has mtime 100, missing.txt has -1
    expect(files[0]).toBe('present.txt');
    expect(files[1]).toBe('missing.txt');
  });
});

// ── parseRows ──

describe('parseRows', () => {
  describe('blank lines', () => {
    it('classifies empty line as blank', () => {
      const rows = parseRows('', 'pre');
      expect(rows).toHaveLength(1);
      expect(rows[0].type).toBe('blank');
    });

    it('classifies whitespace-only line as blank (no tab)', () => {
      const rows = parseRows('   ', 'pre');
      expect(rows).toHaveLength(1);
      expect(rows[0].type).toBe('blank');
    });
  });

  describe('comment lines', () => {
    it('classifies // as comment', () => {
      const rows = parseRows('// comment', 'pre');
      expect(rows[0].type).toBe('comment');
      expect(rows[0].values[0]).toBe('// comment');
    });

    it('classifies # as comment', () => {
      const rows = parseRows('# comment', 'pre');
      expect(rows[0].type).toBe('comment');
    });

    it('classifies \\\\ as comment', () => {
      const rows = parseRows('\\\\comment', 'pre');
      expect(rows[0].type).toBe('comment');
    });
  });

  describe('GPT rows', () => {
    it('parses src\\tdst as GPT row', () => {
      const rows = parseRows('hello\tworld', 'gpt');
      expect(rows[0].type).toBe('gpt');
      expect(rows[0].values).toEqual(['hello', 'world', '']);
    });

    it('parses src\\tdst\\tnotes as GPT row with notes', () => {
      const rows = parseRows('hello\tworld\tsome notes here', 'gpt');
      expect(rows[0].type).toBe('gpt');
      expect(rows[0].values).toEqual(['hello', 'world', 'some notes here']);
    });

    it('parses single column as GPT row (empty dst)', () => {
      const rows = parseRows('hello', 'gpt');
      expect(rows[0].type).toBe('gpt');
      expect(rows[0].values).toEqual(['hello', '', '']);
    });
  });

  describe('conditional rows', () => {
    it('parses pre_src conditional', () => {
      const rows = parseRows('pre_src\tcond\tsearch\treplace', 'pre');
      expect(rows[0].type).toBe('conditional');
      expect(rows[0].values).toEqual(['pre_src', 'cond', 'search', 'replace', '']);
    });

    it('parses post_dst conditional', () => {
      const rows = parseRows('post_dst\tcond\tsearch\treplace\textra', 'pre');
      expect(rows[0].type).toBe('conditional');
      expect(rows[0].values).toEqual(['post_dst', 'cond', 'search', 'replace', 'extra']);
    });

    it('recognizes all valid conditional prefixes', () => {
      const validTargets = ['pre_jp', 'post_jp', 'pre_zh', 'post_zh', 'pre_src', 'post_src', 'pre_dst', 'post_dst'];
      for (const target of validTargets) {
        const rows = parseRows(`${target}\tcond\tsearch\treplace`, 'pre');
        expect(rows[0].type).toBe('conditional');
      }
    });

    it('falls back to normal when < 4 parts but valid prefix', () => {
      const rows = parseRows('pre_src\tcond\tsearch', 'pre');
      // Only 3 parts with pre_src — falls to normal (parts.length < 4)
      expect(rows[0].type).toBe('normal');
    });
  });

  describe('situation rows', () => {
    it('parses diag as situation', () => {
      const rows = parseRows('diag\tsearch\treplace', 'pre');
      expect(rows[0].type).toBe('situation');
      expect(rows[0].values).toEqual(['diag', 'search', 'replace']);
    });

    it('parses mono as situation', () => {
      const rows = parseRows('mono\tsearch\treplace', 'pre');
      expect(rows[0].type).toBe('situation');
    });

    it('parses diag with only 2 parts (falls to normal, needs >=3 for situation)', () => {
      const rows = parseRows('diag\tsearch', 'pre');
      // Only 2 parts, situation check requires >=3 → falls to normal
      expect(rows[0].type).toBe('normal');
    });

    it('falls back to normal when < 3 parts and not diag/mono', () => {
      const rows = parseRows('other\tsearch', 'pre');
      expect(rows[0].type).toBe('normal');
    });
  });

  describe('normal rows', () => {
    it('parses search\\treplace as normal', () => {
      const rows = parseRows('search\treplace', 'pre');
      expect(rows[0].type).toBe('normal');
      expect(rows[0].values).toEqual(['search', 'replace', '']);
    });

    it('parses search\\treplace\\tnotes as normal', () => {
      const rows = parseRows('search\treplace\textra', 'pre');
      expect(rows[0].type).toBe('normal');
      expect(rows[0].values).toEqual(['search', 'replace', 'extra']);
    });

    it('parses single column as normal', () => {
      const rows = parseRows('only_search', 'pre');
      expect(rows[0].type).toBe('normal');
      expect(rows[0].values).toEqual(['only_search', '', '']);
    });
  });

  describe('edge cases', () => {
    it('handles empty text', () => {
      const rows = parseRows('', 'pre');
      expect(rows).toHaveLength(1);
      expect(rows[0].type).toBe('blank');
    });

    it('handles multiple lines', () => {
      const text = '// comment\nnormal\treplace\n\ngpt_src\tgpt_dst';
      const rows = parseRows(text, 'pre');
      expect(rows).toHaveLength(4);
      expect(rows[0].type).toBe('comment');
      expect(rows[1].type).toBe('normal');
      expect(rows[2].type).toBe('blank');
    });

    it('preserves raw line content', () => {
      const text = 'hello\tworld\textra';
      const rows = parseRows(text, 'pre');
      expect(rows[0].raw).toBe(text);
    });

    it('handles very long values', () => {
      const long = 'a'.repeat(10000);
      const rows = parseRows(`${long}\t${long}`, 'pre');
      expect(rows[0].values[0]).toBe(long);
      expect(rows[0].values[1]).toBe(long);
    });

    it('handles unicode content', () => {
      const rows = parseRows('日本語\t中文\t한국어', 'pre');
      expect(rows[0].values).toEqual(['日本語', '中文', '한국어']);
    });

    it('handles tab-only line', () => {
      const rows = parseRows('\t', 'pre');
      // Has tab → not classified as blank → normal with 2 empty columns
      expect(rows[0].type).toBe('normal');
      expect(rows[0].values[0]).toBe('');
      expect(rows[0].values[1]).toBe('');
    });

    it('handles line with only tabs', () => {
      const rows = parseRows('\t\t\t', 'pre');
      expect(rows[0].type).toBe('normal');
    });
  });
});

// ── rowsToText ──

describe('rowsToText', () => {
  it('round-trips with simple normal rows (trailing empty notes column is expected)', () => {
    const text = 'search\treplace\nfind\tresult';
    const rows = parseRows(text, 'pre');
    // Normal rows always have 3 values: [search, replace, notes]
    // So rowsToText adds a trailing tab for the empty notes column
    const output = rowsToText(rows);
    expect(output).toBe('search\treplace\t\nfind\tresult\t');
    // Verify idempotent: second pass produces same output
    expect(rowsToText(parseRows(output, 'pre'))).toBe(output);
  });

  it('round-trips with gpt rows', () => {
    const text = 'hello\tworld\tsome notes';
    const rows = parseRows(text, 'gpt');
    expect(rowsToText(rows)).toBe(text);
  });

  it('round-trips with conditional rows (trailing empty notes column is expected)', () => {
    const text = 'pre_src\tcond\tsearch\treplace';
    const rows = parseRows(text, 'pre');
    // Conditional rows always have 5 values, so trailing tab appears
    const output = rowsToText(rows);
    expect(output).toBe('pre_src\tcond\tsearch\treplace\t');
    // Idempotent
    expect(rowsToText(parseRows(output, 'pre'))).toBe(output);
  });

  it('round-trips with situation rows', () => {
    const text = 'diag\tsearch\treplace';
    const rows = parseRows(text, 'pre');
    expect(rowsToText(rows)).toBe(text);
  });

  it('preserves blank lines (split adds trailing empty for trailing newline)', () => {
    // '\n\n\n' → split('\n') = ['', '', '', ''] → 4 blank rows → 3 newlines
    const rows = parseRows('\n\n\n', 'pre');
    const output = rowsToText(rows);
    expect(output).toBe('\n\n\n');
  });

  it('round-trips mixed content', () => {
    const text = '// comment line\nhello\tworld\npre_src\tcond\tsearch\treplace\n\nnormal\treplace';
    const rows = parseRows(text, 'pre');
    const output = rowsToText(rows);
    // parseRows + rowsToText should be idempotent
    const rows2 = parseRows(output, 'pre');
    const output2 = rowsToText(rows2);
    expect(output2).toBe(output);
  });

  it('returns empty string for empty array', () => {
    expect(rowsToText([])).toBe('');
  });

  it('handles unicode round-trip (trailing empty column expected)', () => {
    const text = '日本語\t中文翻訳';
    const rows = parseRows(text, 'pre');
    // 2-column normal → 3-value normal row → trailing tab
    const output = rowsToText(rows);
    expect(output).toBe('日本語\t中文翻訳\t');
    // Idempotent
    expect(rowsToText(parseRows(output, 'pre'))).toBe(output);
  });

  it('handles rows with more values than fields (extra values preserved)', () => {
    const rows: DictRow[] = [
      { type: 'normal', values: ['a', 'b', 'c', 'd'], raw: 'a\tb\tc\td' },
    ];
    const output = rowsToText(rows);
    expect(output).toBe('a\tb\tc\td');
  });
});

// ── getTypeLabel ──

describe('getTypeLabel', () => {
  it('returns correct labels for all known types', () => {
    expect(getTypeLabel('comment', 'pre')).toBe('注释');
    expect(getTypeLabel('blank', 'pre')).toBe('空行');
    expect(getTypeLabel('gpt', 'gpt')).toBe('GPT');
    expect(getTypeLabel('normal', 'pre')).toBe('普通');
    expect(getTypeLabel('conditional', 'pre')).toBe('条件');
    expect(getTypeLabel('situation', 'pre')).toBe('场景');
  });

  it('returns the type string itself for unknown types', () => {
    expect(getTypeLabel('unknown' as never, 'pre')).toBe('unknown');
  });

  it('gpt tab does not affect label (tab is unused)', () => {
    expect(getTypeLabel('normal', 'gpt')).toBe('普通');
    expect(getTypeLabel('normal', 'pre')).toBe('普通');
    expect(getTypeLabel('normal', 'post')).toBe('普通');
  });

  it('handles empty string type gracefully', () => {
    expect(getTypeLabel('' as never, 'pre')).toBe('');
  });
});

// ── getFieldLabels ──

describe('getFieldLabels', () => {
  it('returns correct labels for gpt type', () => {
    expect(getFieldLabels('gpt', 'gpt')).toEqual(['原文', '译文', '解释(可空)']);
  });

  it('returns correct labels for normal type', () => {
    expect(getFieldLabels('normal', 'pre')).toEqual(['搜索', '替换', '备注']);
  });

  it('returns correct labels for conditional type', () => {
    expect(getFieldLabels('conditional', 'pre')).toEqual(['目标', '条件', '搜索', '替换', '备注']);
  });

  it('returns correct labels for situation type', () => {
    expect(getFieldLabels('situation', 'pre')).toEqual(['场景', '搜索', '替换']);
  });

  it('returns single label for comment type', () => {
    expect(getFieldLabels('comment', 'pre')).toEqual(['内容']);
  });

  it('returns empty array for blank type', () => {
    expect(getFieldLabels('blank', 'pre')).toEqual([]);
  });

  it('returns empty array for unknown type', () => {
    expect(getFieldLabels('unknown' as never, 'pre')).toEqual([]);
  });
});
