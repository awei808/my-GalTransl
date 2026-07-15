import { useState } from 'react';
import type { CacheEntry, CacheSearchResult } from '../../lib/api';
import { speakerStyle, speakerHue } from '../../lib/speaker';
import { resolveSpeakerName } from '../../lib/useNameDict';
import { Icon } from '../../components/icons';

/** Compatible cache field readers: prefer new key, fallback to old key */
export function src(e: CacheEntry): string { return e.post_src || e.post_jp || ''; }
export function dst(e: CacheEntry): string { return e.pre_dst || e.pre_zh || ''; }

/** Encode control characters for display */
export function escapeControlChars(text: string): string {
  return text.replace(/\r/g, '\\r').replace(/\n/g, '\\n');
}

/** Decode control characters from display */
export function unescapeControlChars(text: string): string {
  return text.replace(/\\r/g, '\r').replace(/\\n/g, '\n');
}

/* ── Text highlight helper ── */
export function HighlightText({ text, query }: { text: string; query: string }) {
  if (!query) return <>{text}</>;
  const lower = text.toLowerCase();
  const qLower = query.toLowerCase();
  const parts: React.ReactNode[] = [];
  let lastIdx = 0;
  let searchFrom = 0;
  while (searchFrom < lower.length) {
    const found = lower.indexOf(qLower, searchFrom);
    if (found === -1) break;
    if (found > lastIdx) parts.push(text.slice(lastIdx, found));
    parts.push(<mark key={found} className="search-highlight">{text.slice(found, found + query.length)}</mark>);
    lastIdx = found + query.length;
    searchFrom = lastIdx;
  }
  if (lastIdx < text.length) parts.push(text.slice(lastIdx));
  return <>{parts}</>;
}

/* ── Cache entry card ── */
export function CacheEntryCard({
  entry, filename, projectId, onEntryChange, onDelete, highlightQuery, nameDict,
}: {
  entry: CacheEntry;
  filename: string;
  projectId: string;
  onEntryChange: (index: number, field: keyof CacheEntry, value: string) => void;
  onDelete: (index: number) => void;
  highlightQuery?: string;
  nameDict: Map<string, string>;
}) {
  const hasProblem = !!entry.problem;
  const rawSpeaker = Array.isArray(entry.name) ? entry.name.join('/') : entry.name || '—';
  const speaker = rawSpeaker !== '—'
    ? (Array.isArray(entry.name) ? entry.name.map((s) => resolveSpeakerName(s, nameDict)).join('/') : resolveSpeakerName(rawSpeaker, nameDict))
    : rawSpeaker;
  const [expanded, setExpanded] = useState(false);

  return (
    <article className={`cache-card ${hasProblem ? 'cache-card--problem' : ''}`} data-cache-index={entry.index}>
      <div className="cache-card__row">
        <span className="cache-card__field-label">#{entry.index}</span>
        {speaker !== '—' && (
          <span className="cache-card__pill cache-card__pill--speaker" style={speakerStyle(rawSpeaker)}>{speaker}</span>
        )}
        {hasProblem && (
          <div className="cache-card__problem-slot">
            <span className="cache-card__pill cache-card__pill--problem" title={entry.problem}>{entry.problem}</span>
          </div>
        )}
        <div className="cache-card__spacer" />
        {entry.trans_by && <span className="cache-card__pill cache-card__pill--engine">{entry.trans_by}</span>}
        <button type="button" className="cache-card__expand" onClick={() => setExpanded(!expanded)} title={expanded ? '收起' : '展开详情'}>
          {expanded ? '▾' : '▸'}
        </button>
        <button type="button" className="cache-card__delete" onClick={() => onDelete(entry.index)} title="删除此条"><Icon name="x" size={16} /></button>
      </div>

      <div className="cache-card__fields">
        {!expanded && (
          <>
            <div className="cache-card__field">
              <span className="cache-card__field-label">原文</span>
              <div className="cache-card__input-wrap">
                <span className="cache-card__readonly-input" title={escapeControlChars(src(entry))}>
                  {highlightQuery ? <HighlightText text={escapeControlChars(src(entry))} query={highlightQuery} /> : escapeControlChars(src(entry))}
                </span>
              </div>
            </div>
            <div className="cache-card__field">
              <span className="cache-card__field-label">译文</span>
              <div className="cache-card__input-wrap">
                <input className="cache-card__input cache-card__input--zh" value={escapeControlChars(dst(entry))}
                  onChange={(e) => onEntryChange(entry.index, 'pre_dst', unescapeControlChars(e.target.value))} placeholder="译文" title={escapeControlChars(dst(entry))} />
                {highlightQuery && (
                  <span className="cache-card__input-overlay cache-card__input-overlay--zh">
                    <HighlightText text={escapeControlChars(dst(entry))} query={highlightQuery} />
                  </span>
                )}
              </div>
            </div>
          </>
        )}
        {expanded && (
          <>
            <div className="cache-card__field cache-card__field--textarea">
              <span className="cache-card__field-label">pre_src</span>
              <div className="cache-card__readonly-textarea">{escapeControlChars(entry.pre_src || '')}</div>
            </div>
            <div className="cache-card__field cache-card__field--textarea">
              <span className="cache-card__field-label">post_src</span>
              <div className="cache-card__readonly-textarea">
                {highlightQuery ? <HighlightText text={escapeControlChars(src(entry))} query={highlightQuery} /> : escapeControlChars(src(entry))}
              </div>
            </div>
            <div className="cache-card__field cache-card__field--textarea">
              <span className="cache-card__field-label">pre_dst</span>
              <textarea className="cache-card__textarea cache-card__textarea--zh" value={escapeControlChars(entry.pre_dst || entry.pre_zh || '')}
                onChange={(e) => onEntryChange(entry.index, 'pre_dst', unescapeControlChars(e.target.value))} placeholder="预翻译" rows={3} />
            </div>
            <div className="cache-card__field cache-card__field--textarea">
              <span className="cache-card__field-label">proofread</span>
              <textarea className="cache-card__textarea cache-card__textarea--zh" value={escapeControlChars(entry.proofread_dst || entry.proofread_zh || '')}
                onChange={(e) => onEntryChange(entry.index, 'proofread_dst', unescapeControlChars(e.target.value))} placeholder="校对" rows={3} />
            </div>
            <div className="cache-card__field cache-card__field--textarea">
              <span className="cache-card__field-label">preview</span>
              <div className="cache-card__readonly-textarea">{escapeControlChars(entry.post_dst_preview || entry.post_zh_preview || '')}</div>
            </div>
          </>
        )}
      </div>
    </article>
  );
}

/* ── Search result card ── */
export function SearchResultCard({
  result, query, onJumpToFile, nameDict, selected, onSelect, onContextMenu, idx,
}: {
  result: CacheSearchResult;
  query: string;
  onJumpToFile: (filename: string, index: number) => void;
  nameDict: Map<string, string>;
  selected: boolean;
  onSelect: () => void;
  onContextMenu: (event: React.MouseEvent<HTMLButtonElement>) => void;
  idx: number;
}) {
  const rawSpeaker = Array.isArray(result.speaker) ? result.speaker.join('/') : result.speaker || '—';
  const speaker = rawSpeaker !== '—'
    ? (Array.isArray(result.speaker) ? result.speaker.map((s) => resolveSpeakerName(s, nameDict)).join('/') : resolveSpeakerName(rawSpeaker, nameDict))
    : rawSpeaker;

  return (
    <button type="button" className={`search-result-card${selected ? ' search-result-card--selected' : ''}`} data-search-idx={idx}
      onClick={() => { onSelect(); onJumpToFile(result.filename, result.index); }}
      onContextMenu={(e) => { e.preventDefault(); onSelect(); onContextMenu(e); }}
      title={`跳转到 ${result.filename} #${result.index}`}>
      <div className="search-result-card__header">
        {(result.match_src || result.match_dst || result.match_problem) && (
          <span className="search-result-card__match-badges">
            {result.match_src && <span className="search-result-card__badge search-result-card__badge--src">原文</span>}
            {result.match_dst && <span className="search-result-card__badge search-result-card__badge--dst">译文</span>}
            {result.match_problem && <span className="search-result-card__badge search-result-card__badge--problem">问题</span>}
          </span>
        )}
        <span className="search-result-card__file">{result.filename}</span>
      </div>
      {(result.index !== undefined || speaker !== '—' || result.problem) && (
        <div className="search-result-card__tags">
          <span className="search-result-card__index">#{result.index}</span>
          {speaker !== '—' && <span className="search-result-card__speaker" style={{ color: `hsl(${speakerHue(rawSpeaker)}, 55%, 32%)` }}>{speaker}</span>}
          {result.problem && <span className="search-result-card__problem">{result.problem}</span>}
        </div>
      )}
      <div className="search-result-card__text">
        <div className="search-result-card__src" title={escapeControlChars(result.post_src)}>
          {matchMarks(result, 'src', query)}
        </div>
        <div className="search-result-card__dst" title={escapeControlChars(result.pre_dst)}>
          {matchMarks(result, 'dst', query)}
        </div>
      </div>
      {result.trans_by && <div className="search-result-card__footer"><span className="search-result-card__engine">{result.trans_by}</span></div>}
    </button>
  );
}

/** Render match indicators for search result text */
function matchMarks(result: CacheSearchResult, field: 'src' | 'dst', query: string) {
  const text = field === 'src' ? escapeControlChars(result.post_src) : escapeControlChars(result.pre_dst);
  const isMatch = field === 'src' ? result.match_src : result.match_dst;
  return <HighlightText text={text} query={isMatch ? query : text.slice(0, 1) + '!!!NO_MATCH!!!'} />;
}
