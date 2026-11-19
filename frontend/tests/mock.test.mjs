import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { createMockTransport, buildAnalysis, rescore, parseJd } from "../js/mock.js";
import { createApi } from "../js/api.js";
import { mergeRanges } from "../js/highlight.js";

const dir = join(dirname(fileURLToPath(import.meta.url)), "..", "fixtures");
const names = ["health", "me", "documents", "jd", "jd_list", "search", "pool", "chunks", "candidates", "pii", "pdf_url", "analyses"];
const fx = Object.fromEntries(names.map((n) => [n, JSON.parse(readFileSync(join(dir, `${n}.json`), "utf8"))]));

function setup(opts = {}) {
  let t = Date.parse("2026-11-08T00:00:00Z"); // realistic clock: new items must sort after the 2026 fixtures
  const clock = { now: () => t, advance: (ms) => { t += ms; } };
  const transport = createMockTransport({ fixtures: fx, now: clock.now, sleep: async () => {}, latency: {}, ...opts });
  const api = createApi({ transport, getToken: async () => "mock" });
  return { api, clock, transport };
}

test("buildAnalysis reproduces the Python-generated fixture exactly for every candidate (JS and Python agree)", () => {
  const spec = fx.jd.spec;
  for (const [cid, expected] of Object.entries(fx.analyses)) {
    const got = buildAnalysis(spec, fx.chunks[cid], fx.candidates[cid].years_experience);
    assert.deepEqual(got.result, expected.result, `result for ${cid}`);
    assert.deepEqual(got.citations_resolved, expected.citations_resolved, `citations for ${cid}`);
  }
});

test("every citation in every fixture analysis is a verbatim substring at its stated offsets", () => {
  let n = 0;
  for (const a of Object.values(fx.analyses)) {
    for (const c of a.citations_resolved) {
      assert.equal(c.text_scrubbed.slice(c.start, c.end), c.quote);
      n++;
    }
  }
  assert.ok(n > 100, `checked ${n} citations`);
});

test("search: ranks descending with 1-based ranks, respects top_k, and uses the JD's skills", async () => {
  const { api } = setup();
  const res = await api.search({ jd_id: fx.jd.id, top_k: 5, filters: {}, blind: true });
  assert.equal(res.results.length, 5);
  assert.deepEqual(res.results.map((r) => r.rank), [1, 2, 3, 4, 5]);
  for (let i = 1; i < res.results.length; i++) assert.ok(res.results[i - 1].match_score >= res.results[i].match_score);
  for (const r of res.results) {
    assert.equal(r.matched_required.length + r.missing_required.length, fx.jd.spec.required_skills.length);
    assert.match(r.label, /^Candidate #[0-9A-F]{6}$/);
    assert.ok(r.match_score >= 0 && r.match_score <= 100);
  }
});

test("search filters are hard filters", async () => {
  const { api } = setup();
  const yrs = await api.search({ jd_id: fx.jd.id, top_k: 50, filters: { min_years: 6 } });
  assert.ok(yrs.results.length > 0 && yrs.results.every((r) => r.years_experience >= 6));
  const edu = await api.search({ jd_id: fx.jd.id, top_k: 50, filters: { min_education: 3 } });
  assert.ok(edu.results.every((r) => r.education_level >= 3));
  const sk = await api.search({ jd_id: fx.jd.id, top_k: 50, filters: { required_skills: ["python", "docker"] } });
  assert.ok(sk.results.length > 0);
  for (const r of sk.results) assert.ok(fx.candidates[r.candidate_id].profile.skills.includes("python") && fx.candidates[r.candidate_id].profile.skills.includes("docker"));
  const none = await api.search({ jd_id: fx.jd.id, top_k: 50, filters: { min_years: 40 } });
  assert.deepEqual(none.results, []);
});

test("editing the JD's required skills changes who ranks first (the UI edit is meaningful)", async () => {
  const { api } = setup();
  const before = await api.search({ jd_id: fx.jd.id, top_k: 3 });
  const spec = { ...fx.jd.spec, required_skills: ["react", "typescript", "css"], preferred_skills: [], min_years: 2 };
  await api.updateJD(fx.jd.id, "Frontend", spec);
  const after = await api.search({ jd_id: fx.jd.id, top_k: 3 });
  assert.notDeepEqual(before.results.map((r) => r.candidate_id), after.results.map((r) => r.candidate_id));
  const top = fx.candidates[after.results[0].candidate_id].profile.skills;
  assert.ok(top.includes("react"));
});

test("rescore: contributions sum to (score - 50) within rounding", () => {
  const r = fx.pool[0];
  const out = rescore(r, fx.candidates[r.candidate_id], fx.jd.spec);
  const sum = Object.values(out.explanation.feature_contributions).reduce((a, b) => a + b, 0);
  assert.ok(Math.abs(sum - (out.match_score - 50)) < 0.2, `${sum} vs ${out.match_score - 50}`);
});

test("analysis is cached per JD+candidate+skills and recomputed when skills change; errors are drillable", async () => {
  const { api } = setup();
  const cid = fx.search.results[0].candidate_id;
  const a1 = await api.analyze({ jd_id: fx.jd.id, candidate_id: cid });
  const a2 = await api.analyze({ jd_id: fx.jd.id, candidate_id: cid });
  assert.equal(a1.from_cache, false);
  assert.equal(a2.from_cache, true);
  await api.updateJD(fx.jd.id, null, { ...fx.jd.spec, required_skills: ["kubernetes"], preferred_skills: [] });
  const a3 = await api.analyze({ jd_id: fx.jd.id, candidate_id: cid });
  assert.equal(a3.from_cache, false);
  assert.deepEqual(a3.result.skills.map((s) => s.skill), ["kubernetes"]);
  const { api: bad } = setup({ failures: { analysis: "429" } });
  await assert.rejects(() => bad.analyze({ jd_id: fx.jd.id, candidate_id: cid }), { status: 429, retryAfter: 12 });
  const { api: bad2 } = setup({ failures: { analysis: "500" } });
  await assert.rejects(() => bad2.analyze({ jd_id: fx.jd.id, candidate_id: cid }), { status: 500 });
});

test("candidate is blind by default; reveal returns PII; unknown ids give 404 envelope", async () => {
  const { api } = setup();
  const cid = fx.search.results[0].candidate_id;
  const blind = await api.getCandidate(cid);
  assert.equal(blind.pii, null);
  assert.ok(!JSON.stringify(blind).includes("@example.com"), "blind response must not contain e-mail");
  const rev = await api.getCandidate(cid, true);
  assert.match(rev.pii.email, /@example\.com$/);
  await assert.rejects(() => api.getCandidate("nope"), { status: 404, code: "not_found" });
});

test("upload: rejects non-PDFs with reasons, then the job progresses queued -> ready/failed/needs_ocr over time", async () => {
  const { api, clock } = setup();
  const files = [new File(["%PDF"], "resume_a.pdf"), new File(["%PDF"], "corrupt_b.pdf"), new File(["%PDF"], "scan_c.pdf"), new File(["x"], "notes.docx")];
  const prog = [];
  const up = await api.uploadDocuments(files, { onProgress: (l, t) => prog.push(l / t) }).catch((e) => e);
  assert.equal(up.document_ids.length, 3);
  assert.deepEqual(up.rejected, [{ filename: "notes.docx", reason: "Only PDF files are accepted." }]);
  assert.ok(prog.length === 5 && prog.at(-1) === 1);
  let job = await api.getJob(up.job_id);
  assert.equal(job.status, "running");
  assert.equal(job.total, 3);
  clock.advance(20_000);
  job = await api.getJob(up.job_id);
  assert.equal(job.status, "completed_with_errors");
  assert.equal(job.done, 1);
  assert.equal(job.failed, 2);
  const byName = Object.fromEntries(job.documents.map((d) => [d.filename, d]));
  assert.equal(byName["resume_a.pdf"].status, "ready");
  assert.equal(byName["corrupt_b.pdf"].status, "failed");
  assert.match(byName["corrupt_b.pdf"].error, /corrupt/);
  assert.equal(byName["scan_c.pdf"].status, "needs_ocr");
});

test("upload of only invalid files returns a 400 envelope; a forced 413 is reported", async () => {
  const { api } = setup();
  await assert.rejects(() => api.uploadDocuments([new File(["x"], "a.txt")]), { status: 400, code: "no_valid_files" });
  const { api: big } = setup({ failures: { upload: "413" } });
  await assert.rejects(() => big.uploadDocuments([new File(["%PDF"], "a.pdf")]), { status: 413 });
});

test("documents list: filters, search, pagination, counts, delete", async () => {
  const { api } = setup();
  const all = await api.listDocuments({ page_size: 100 });
  assert.equal(all.total, fx.documents.total);
  assert.equal(all.counts.all, fx.documents.total);
  const failed = await api.listDocuments({ status: "failed" });
  assert.ok(failed.items.length === 2 && failed.items.every((d) => d.status === "failed"));
  const proc = await api.listDocuments({ status: "processing" });
  assert.equal(proc.items.length, 3);
  const q = await api.listDocuments({ q: "SCAN" });
  assert.deepEqual(q.items.map((d) => d.filename), ["scan_2019_cv.pdf"]);
  const p2 = await api.listDocuments({ page: 2, page_size: 10 });
  assert.equal(p2.items.length, 10);
  assert.equal(p2.page, 2);
  await api.deleteDocument(failed.items[0].id);
  assert.equal((await api.listDocuments({ status: "failed" })).items.length, 1);
  await assert.rejects(() => api.deleteDocument(failed.items[0].id), { status: 404 });
});

test("in-flight fixture documents settle to ready by themselves", async () => {
  const { api, clock } = setup();
  assert.equal((await api.listDocuments({ status: "processing" })).items.length, 3);
  clock.advance(30_000);
  assert.equal((await api.listDocuments({ status: "processing" })).items.length, 0);
});

test("JD create parses skills/years/education; rejects short text with 422; list and PUT work", async () => {
  const { api } = setup();
  await assert.rejects(() => api.createJD("short"), { status: 422, code: "validation_error" });
  const jd = await api.createJD(fx.jd.spec.raw_text);
  assert.deepEqual(jd.spec.required_skills.slice().sort(), ["docker", "django", "fastapi", "postgresql", "python", "rest"].sort());
  assert.deepEqual(jd.spec.preferred_skills.slice().sort(), ["aws", "kafka", "redis"]);
  assert.equal(jd.spec.min_years, 3);
  assert.equal(jd.spec.min_education, 2);
  assert.equal(jd.title, "Backend Engineer (Python)");
  const list = await api.listJDs();
  assert.equal(list[0].id, jd.id);
  const saved = await api.updateJD(jd.id, "Renamed", { ...jd.spec, required_skills: ["python"] });
  assert.equal(saved.title, "Renamed");
  assert.deepEqual((await api.getJD(jd.id)).spec.required_skills, ["python"]);
});

test("CSV export contains a header and one row per result", async () => {
  const { api } = setup();
  const s = await api.search({ jd_id: fx.jd.id, top_k: 4 });
  const blob = await api.exportCsv(s.search_id);
  const lines = (await blob.text()).split("\r\n");
  assert.equal(lines.length, 5);
  assert.match(lines[0], /^rank,candidate,match_score/);
});

test("unknown mock routes give a 404 envelope", async () => {
  const { api } = setup();
  await assert.rejects(() => api.call("GET", "/nope"), { status: 404, code: "not_found" });
});

test("fixture search.json: citations ranges merge cleanly when all of a candidate's marks are applied to one chunk", () => {
  const a = fx.analyses[fx.search.results[0].candidate_id];
  const byChunk = new Map();
  for (const c of a.citations_resolved) byChunk.set(c.chunk_id, [...(byChunk.get(c.chunk_id) ?? []), { start: c.start, end: c.end }]);
  for (const [chunkId, ranges] of byChunk) {
    const text = a.citations_resolved.find((c) => c.chunk_id === chunkId).text_scrubbed;
    const merged = mergeRanges(ranges, text.length);
    assert.ok(merged.length >= 1 && merged.every(([s, e]) => s < e && e <= text.length));
  }
});
