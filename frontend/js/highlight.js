// Evidence highlighting. The backend returns, for every citation, the exact [start, end) character range of the quote
// inside `text_scrubbed`. These helpers turn those ranges into render-ready segments. Pure functions, unit-tested.

/** Clamp, drop invalid ranges, sort, and merge overlapping/adjacent ones. */
export function mergeRanges(ranges, length) {
  const valid = [];
  for (const r of ranges ?? []) {
    const start = Number(r?.start);
    const end = Number(r?.end);
    if (!Number.isInteger(start) || !Number.isInteger(end)) continue;
    const s = Math.max(0, start);
    const e = Math.min(length, end);
    if (e > s) valid.push([s, e]);
  }
  valid.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const out = [];
  for (const [s, e] of valid) {
    const last = out[out.length - 1];
    if (last && s <= last[1]) last[1] = Math.max(last[1], e);
    else out.push([s, e]);
  }
  return out;
}

/** Split `text` into consecutive segments: [{ text, marked }]. Concatenating segment texts always equals `text`. */
export function buildSegments(text, ranges) {
  const str = String(text ?? "");
  const merged = mergeRanges(ranges, str.length);
  const segments = [];
  let cursor = 0;
  for (const [s, e] of merged) {
    if (s > cursor) segments.push({ text: str.slice(cursor, s), marked: false });
    segments.push({ text: str.slice(s, e), marked: true });
    cursor = e;
  }
  if (cursor < str.length) segments.push({ text: str.slice(cursor), marked: false });
  return segments;
}

/**
 * Show a window of a long passage around the first marked range, cutting on word boundaries.
 * Returns the (possibly shortened) text, ranges shifted to it, and whether the start/end were cut.
 */
export function excerptAround(text, ranges, { before = 240, after = 240 } = {}) {
  const str = String(text ?? "");
  const merged = mergeRanges(ranges, str.length);
  if (merged.length === 0 || str.length <= before + after + 40) {
    return { text: str, ranges: merged.map(([start, end]) => ({ start, end })), cutStart: false, cutEnd: false };
  }
  const firstStart = merged[0][0];
  const lastEnd = merged[merged.length - 1][1];
  let from = Math.max(0, firstStart - before);
  let to = Math.min(str.length, Math.max(lastEnd, firstStart) + after);
  if (from > 0) {
    const nextSpace = str.slice(from, firstStart).search(/\s/);
    if (nextSpace !== -1) from += nextSpace + 1;
  }
  if (to < str.length) {
    const prevSpace = str.slice(lastEnd, to).search(/\s\S*$/);
    if (prevSpace !== -1) to = lastEnd + prevSpace;
  }
  const shifted = merged
    .filter(([s, e]) => s >= from && e <= to)
    .map(([s, e]) => ({ start: s - from, end: e - from }));
  return { text: str.slice(from, to), ranges: shifted, cutStart: from > 0, cutEnd: to < str.length };
}

/** Does `quote` occur in `text` exactly? Used by the UI to refuse to highlight a range that does not match its quote. */
export function rangeMatchesQuote(text, start, end, quote) {
  return typeof text === "string" && typeof quote === "string" && text.slice(start, end) === quote;
}

/** Replace placeholder tokens like [NAME] so the UI can render them as redaction bars. */
export function splitRedactions(text) {
  const parts = [];
  const re = /\[(NAME|EMAIL|PHONE|URL|ADDRESS)\]/g;
  let last = 0;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push({ text: text.slice(last, m.index), redacted: false });
    parts.push({ text: m[1].toLowerCase(), redacted: true });
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push({ text: text.slice(last), redacted: false });
  return parts;
}

/**
 * Defence in depth: only citations whose [start, end) range really contains their quote may be highlighted.
 * Anything else is returned in `rejected` so the UI can say "could not be verified" instead of marking wrong text.
 */
export function usableCitations(citations) {
  const ok = [];
  const rejected = [];
  for (const c of citations ?? []) {
    (rangeMatchesQuote(c?.text_scrubbed, c?.start, c?.end, c?.quote) ? ok : rejected).push(c);
  }
  return { ok, rejected };
}
