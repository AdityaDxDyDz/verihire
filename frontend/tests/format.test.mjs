import test from "node:test";
import assert from "node:assert/strict";
import * as f from "../js/format.js";

test("eduLabel maps levels 0-4 and falls back for junk", () => {
  assert.equal(f.eduLabel(2), "Bachelor");
  assert.equal(f.eduLabel(4), "PhD");
  assert.equal(f.eduLabel(9), "Not stated");
  assert.equal(f.eduLabel(null), "Not stated");
  assert.equal(f.eduLabel("3"), "Master");
});

test("fmtYears handles singular, decimals, whole numbers and missing", () => {
  assert.equal(f.fmtYears(1), "1 year");
  assert.equal(f.fmtYears(6), "6 years");
  assert.equal(f.fmtYears(6.5), "6.5 years");
  assert.equal(f.fmtYears(0), "0 years");
  assert.equal(f.fmtYears(null), "Not stated");
  assert.equal(f.fmtYears(undefined), "Not stated");
});

test("fmtScore rounds and tolerates garbage; scoreTone thresholds", () => {
  assert.equal(f.fmtScore(92.7), "93");
  assert.equal(f.fmtScore(57.4), "57");
  assert.equal(f.fmtScore("abc"), "0");
  assert.equal(f.scoreTone(75), "high");
  assert.equal(f.scoreTone(74.9), "mid");
  assert.equal(f.scoreTone(50), "mid");
  assert.equal(f.scoreTone(49.9), "low");
});

test("fmtBytes and fmtDuration", () => {
  assert.equal(f.fmtBytes(512), "512 B");
  assert.equal(f.fmtBytes(2048), "2 KB");
  assert.equal(f.fmtBytes(10 * 1024 * 1024), "10.0 MB");
  assert.equal(f.fmtBytes(-1), "");
  assert.equal(f.fmtDuration(61.3), "61 ms");
  assert.equal(f.fmtDuration(1250), "1.3 s");
});

test("fmtDate relative buckets", () => {
  const now = Date.parse("2026-11-07T12:00:00Z");
  assert.equal(f.fmtDate("2026-11-07T11:59:40Z", now), "just now");
  assert.equal(f.fmtDate("2026-11-07T11:55:00Z", now), "5 min ago");
  assert.equal(f.fmtDate("2026-11-07T09:00:00Z", now), "3 h ago");
  assert.equal(f.fmtDate("2026-11-06T09:00:00Z", now), "yesterday");
  assert.match(f.fmtDate("2026-10-01T09:00:00Z", now), /^\d{1,2} (Sep|Oct)$/);
  assert.equal(f.fmtDate("not a date", now), "");
});

test("displaySkill and featureLabel", () => {
  assert.equal(f.displaySkill("postgresql"), "PostgreSQL");
  assert.equal(f.displaySkill("rest"), "REST APIs");
  assert.equal(f.displaySkill("some obscure thing"), "some obscure thing");
  assert.equal(f.featureLabel("required_skill_coverage"), "Required skills covered");
  assert.equal(f.featureLabel("brand_new_feature"), "Brand new feature");
  assert.equal(f.featureLabel(""), "");
});

test("statusMeta covers every contract status; isProcessing only for in-flight ones", () => {
  const all = ["uploaded", "extracting", "scrubbing", "chunking", "parsing", "embedding", "ready", "failed", "needs_ocr"];
  for (const s of all) assert.ok(f.statusMeta(s).label, s);
  for (const s of ["uploaded", "extracting", "scrubbing", "chunking", "parsing", "embedding"]) assert.equal(f.isProcessing(s), true, s);
  for (const s of ["ready", "failed", "needs_ocr", "nonsense"]) assert.equal(f.isProcessing(s), false, s);
  assert.equal(f.statusMeta("ready").tone, "ok");
  assert.equal(f.statusMeta("failed").tone, "bad");
  assert.equal(f.statusMeta("wat").label, "wat");
});

test("safeHref only lets through relative and http(s) URLs", () => {
  assert.equal(f.safeHref("/api/v1/local-files/a%2Fb.pdf?exp=1&sig=ab"), "/api/v1/local-files/a%2Fb.pdf?exp=1&sig=ab");
  assert.equal(f.safeHref("https://x.supabase.co/storage/v1/object/sign/r/a.pdf?token=t"), "https://x.supabase.co/storage/v1/object/sign/r/a.pdf?token=t");
  assert.equal(f.safeHref("fixtures/sample.pdf"), "fixtures/sample.pdf");
  assert.equal(f.safeHref("javascript:alert(1)"), "#");
  assert.equal(f.safeHref("JaVaScRiPt:alert(1)"), "#");
  assert.equal(f.safeHref("data:text/html,<script>"), "#");
  assert.equal(f.safeHref("//evil.example/x"), "#");
  assert.equal(f.safeHref(""), "#");
  assert.equal(f.safeHref(null), "#");
});

test("pdfPageUrl appends a page anchor only for valid pages", () => {
  assert.equal(f.pdfPageUrl("/x.pdf?sig=1", 2), "/x.pdf?sig=1#page=2");
  assert.equal(f.pdfPageUrl("/x.pdf#page=9", 3), "/x.pdf#page=3");
  assert.equal(f.pdfPageUrl("/x.pdf", 0), "/x.pdf");
  assert.equal(f.pdfPageUrl("javascript:1", 2), "#");
});

test("toCsv quotes, escapes and neutralises formulas", () => {
  assert.equal(f.toCsv([["a", "b,c", 'd"e']]), 'a,"b,c","d""e"');
  assert.equal(f.toCsv([["=SUM(A1)", "+1", "-2", "@x", "ok"]]), "'=SUM(A1),'+1,'-2,'@x,ok");
  assert.equal(f.toCsv([[1, null], [2, undefined]]), "1,\r\n2,");
});

test("sectionLabel", () => {
  assert.equal(f.sectionLabel("experience"), "Experience");
  assert.equal(f.sectionLabel("other"), "Header");
  assert.equal(f.sectionLabel(null), "Other");
});
