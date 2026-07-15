import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { src, dst, escapeControlChars, unescapeControlChars, HighlightText } from '../cacheUtils';
import type { CacheEntry } from '../../../lib/api';

// ── src / dst ──
describe('src / dst — cache field accessors', () => {
  const base: CacheEntry = { index: 0, name: '', pre_src: '', post_src: '', pre_dst: '', pre_jp: '', post_jp: '', pre_zh: '' };

  it('src prefers post_src over post_jp', () => {
    expect(src({ ...base, post_src: 'a', post_jp: 'b' })).toBe('a');
  });

  it('src falls back to post_jp', () => {
    expect(src({ ...base, post_src: '', post_jp: 'b' })).toBe('b');
  });

  it('src returns empty string when both missing', () => {
    expect(src({ ...base })).toBe('');
  });

  it('dst prefers pre_dst over pre_zh', () => {
    expect(dst({ ...base, pre_dst: 'a', pre_zh: 'b' })).toBe('a');
  });

  it('dst falls back to pre_zh', () => {
    expect(dst({ ...base, pre_dst: '', pre_zh: 'b' })).toBe('b');
  });

  it('dst returns empty string when both missing', () => {
    expect(dst({ ...base })).toBe('');
  });
});

// ── escapeControlChars / unescapeControlChars ──
describe('escapeControlChars / unescapeControlChars', () => {
  it('escapes \\r to \\\\r', () => {
    expect(escapeControlChars('hello\rworld')).toBe('hello\\rworld');
  });

  it('escapes \\n to \\\\n', () => {
    expect(escapeControlChars('hello\nworld')).toBe('hello\\nworld');
  });

  it('escapes both \\r and \\n', () => {
    expect(escapeControlChars('a\r\nb')).toBe('a\\r\\nb');
  });

  it('does not modify text without control chars', () => {
    expect(escapeControlChars('hello world')).toBe('hello world');
  });

  it('handles empty string', () => {
    expect(escapeControlChars('')).toBe('');
  });

  it('unescapeControlChars reverses escapeControlChars', () => {
    const original = 'line1\r\nline2\rline3\nline4';
    const escaped = escapeControlChars(original);
    const unescaped = unescapeControlChars(escaped);
    expect(unescaped).toBe(original);
  });

  it('unescapeControlChars handles text without escape sequences', () => {
    expect(unescapeControlChars('plain text')).toBe('plain text');
  });

  it('unescapeControlChars handles empty string', () => {
    expect(unescapeControlChars('')).toBe('');
  });

  it('escapeControlChars handles unicode', () => {
    expect(escapeControlChars('日本語\r中文')).toBe('日本語\\r中文');
  });
});

// ── HighlightText ──
describe('HighlightText — rendering edge cases', () => {
  it('renders text without mark when query is empty', () => {
    const { container } = render(<HighlightText text="hello world" query="" />);
    expect(container.querySelector('mark')).toBeNull();
    expect(container.textContent).toBe('hello world');
  });

  it('renders text without mark when query not found', () => {
    const { container } = render(<HighlightText text="hello world" query="xyz" />);
    expect(container.querySelector('mark')).toBeNull();
  });

  it('highlights single match', () => {
    const { container } = render(<HighlightText text="hello world" query="hello" />);
    const marks = container.querySelectorAll('mark');
    expect(marks).toHaveLength(1);
    expect(marks[0].textContent).toBe('hello');
  });

  it('highlights multiple matches (loop-based, ALL occurrences)', () => {
    const { container } = render(<HighlightText text="hello hello hello" query="hello" />);
    const marks = container.querySelectorAll('mark');
    expect(marks).toHaveLength(3);
    marks.forEach((m) => expect(m.textContent).toBe('hello'));
  });

  it('case-insensitive matching', () => {
    const { container } = render(<HighlightText text="Hello World" query="hello" />);
    expect(container.querySelectorAll('mark')).toHaveLength(1);
  });

  it('highlights query within sentence', () => {
    const { container } = render(<HighlightText text="This is a test sentence" query="test" />);
    const marks = container.querySelectorAll('mark');
    expect(marks).toHaveLength(1);
  });

  it('preserves text between matches', () => {
    const { container } = render(<HighlightText text="ababa" query="a" />);
    // "ababa" → match 'a' at 0, 'b', match 'a' at 2, 'b', match 'a' at 4
    const marks = container.querySelectorAll('mark');
    expect(marks).toHaveLength(3);
    // text content should still be 'ababa' (a + b + a + b + a)
    expect(container.textContent).toBe('ababa');
  });

  it('handles empty text', () => {
    const { container } = render(<HighlightText text="" query="hello" />);
    expect(container.querySelector('mark')).toBeNull();
  });

  it('handles overlapping matches (non-overlapping by spec)', () => {
    // Searching for "aa" in "aaa" — should match once at index 0 (then searchFrom advances to 2)
    const { container } = render(<HighlightText text="aaa" query="aa" />);
    const marks = container.querySelectorAll('mark');
    expect(marks).toHaveLength(1);
    expect(marks[0].textContent).toBe('aa');
  });

  it('applies search-highlight class to marks', () => {
    const { container } = render(<HighlightText text="test" query="t" />);
    const marks = container.querySelectorAll('mark');
    marks.forEach((m) => expect(m.className).toBe('search-highlight'));
  });

  it('handles unicode text with match', () => {
    const { container } = render(<HighlightText text="日本語テストです" query="テスト" />);
    const marks = container.querySelectorAll('mark');
    expect(marks).toHaveLength(1);
  });

  it('handles very long text', () => {
    const long = 'x'.repeat(10000) + 'TARGET' + 'x'.repeat(10000);
    const { container } = render(<HighlightText text={long} query="TARGET" />);
    expect(container.querySelectorAll('mark')).toHaveLength(1);
  });
});
