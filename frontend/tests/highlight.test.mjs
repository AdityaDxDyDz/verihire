import test from "node:test";
import assert from "node:assert/strict";
import { mergeRanges, buildSegments, excerptAround, rangeMatchesQuote, splitRedactions } from "../js/highlight.js";

const join = (segs) => segs.map((s) => s.text).join("");

test("buildSegments marks exactly the given range and preserves the text", () => {
  const text = "Built services using FastAPI in production.";
  const start = text.indexOf("FastAPI");
  const segs = buildSegments(text, [{ start, end: start + 7 }]);
  assert.deepEqual(segs, [
    { text: "Built services using ", marked: false },
    { text: "FastAPI", marked: true },
    { text: " in production.", marked: false },
  ]);
  assert.equal(join(segs), text);
});

test("buildSegments handles range at start, end, whole text and no ranges", () => {
  assert.deepEqual(buildSegments("abcdef", [{ start: 0, end: 3 }]), [{ text: "abc", marked: true }, { text: "def", marked: false }]);
  assert.deepEqual(buildSegments("abcdef", [{ start: 3, end: 6 }]), [{ text: "abc", marked: false }, { text: "def", marked: true }]);
  assert.deepEqual(buildSegments("abc", [{ start: 0, end: 3 }]), [{ text: "abc", marked: true }]);
  assert.deepEqual(buildSegments("abc", []), [{ text: "abc", marked: false }]);
  assert.deepEqual(buildSegments("", [{ start: 0, end: 2 }]), []);
});

test("overlapping and adjacent ranges merge; unsorted input is fine", () => {
  assert.deepEqual(mergeRanges([{ start: 5, end: 9 }, { start: 0, end: 3 }, { start: 8, end: 12 }, { start: 3, end: 5 }], 20), [[0, 12]]);
  const segs = buildSegments("0123456789", [{ start: 6, end: 9 }, { start: 2, end: 4 }]);
  assert.deepEqual(segs.filter((s) => s.marked).map((s) => s.text), ["23", "678"]);
  assert.equal(join(segs), "0123456789");
});

test("invalid ranges are ignored or clamped, never throw", () => {
  assert.deepEqual(mergeRanges([{ start: -5, end: 2 }], 10), [[0, 2]]);
  assert.deepEqual(mergeRanges([{ start: 8, end: 99 }], 10), [[8, 10]]);
  assert.deepEqual(mergeRanges([{ start: 5, end: 5 }, { start: 7, end: 3 }, { start: "a", end: 2 }, null, {}], 10), []);
  assert.deepEqual(mergeRanges(undefined, 10), []);
});

test("buildSegments concatenation equals the input for random ranges", () => {
  let seed = 7;
  const rnd = () => ((seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff);
  const text = "The quick brown fox jumps over the lazy dog. ".repeat(6);
  for (let i = 0; i < 200; i++) {
    const ranges = Array.from({ length: 1 + Math.floor(rnd() * 4) }, () => {
      const s = Math.floor(rnd() * text.length);
      return { start: s, end: s + Math.floor(rnd() * 30) };
    });
    assert.equal(join(buildSegments(text, ranges)), text);
  }
});

test("excerptAround returns whole short text unchanged", () => {
  const r = excerptAround("short text here", [{ start: 6, end: 10 }]);
  assert.equal(r.text, "short text here");
  assert.deepEqual(r.ranges, [{ start: 6, end: 10 }]);
  assert.equal(r.cutStart, false);
  assert.equal(r.cutEnd, false);
});

test("excerptAround windows a long passage on word boundaries and keeps the mark aligned", () => {
  const filler = "lorem ipsum dolor sit amet ".repeat(40);
  const text = `${filler}Built the billing module using PostgreSQL for years. ${filler}`;
  const start = text.indexOf("PostgreSQL");
  const r = excerptAround(text, [{ start, end: start + 10 }], { before: 60, after: 60 });
  assert.ok(r.text.length < text.length);
  assert.equal(r.cutStart, true);
  assert.equal(r.cutEnd, true);
  assert.equal(r.text.slice(r.ranges[0].start, r.ranges[0].end), "PostgreSQL");
  assert.ok(!/^\S*\s/.test(r.text) || r.text[0] !== " ");
  assert.ok(text.includes(r.text));
});

test("rangeMatchesQuote verifies the quote really sits at the range", () => {
  const text = "Used Docker daily";
  assert.equal(rangeMatchesQuote(text, 5, 11, "Docker"), true);
  assert.equal(rangeMatchesQuote(text, 5, 11, "docker"), false);
  assert.equal(rangeMatchesQuote(text, 0, 4, "Docker"), false);
  assert.equal(rangeMatchesQuote(null, 0, 1, "x"), false);
});

test("splitRedactions turns placeholder tokens into redaction parts", () => {
  assert.deepEqual(splitRedactions("[NAME]\nEngineer\n[EMAIL] | [PHONE]"), [
    { text: "name", redacted: true },
    { text: "\nEngineer\n", redacted: false },
    { text: "email", redacted: true },
    { text: " | ", redacted: false },
    { text: "phone", redacted: true },
  ]);
  assert.deepEqual(splitRedactions("plain"), [{ text: "plain", redacted: false }]);
  assert.deepEqual(splitRedactions("[NOTATOKEN]"), [{ text: "[NOTATOKEN]", redacted: false }]);
});

import { usableCitations } from "../js/highlight.js";

test("usableCitations keeps only citations whose range contains the quote", () => {
  const text = "Built an admin portal using PostgreSQL daily.";
  const s = text.indexOf("PostgreSQL");
  const good = { text_scrubbed: text, start: s, end: s + 10, quote: "PostgreSQL" };
  const wrongOffset = { text_scrubbed: text, start: 0, end: 10, quote: "PostgreSQL" };
  const wrongCase = { text_scrubbed: text, start: s, end: s + 10, quote: "postgresql" };
  const noText = { start: 0, end: 3, quote: "abc" };
  const { ok, rejected } = usableCitations([good, wrongOffset, wrongCase, noText, null]);
  assert.deepEqual(ok, [good]);
  assert.equal(rejected.length, 4);
});
