// In-browser fake backend for `?mock=1`. It speaks the same contract as the real API (see docs/contracts/fixtures and
// contract_drafts/api.py) so the frontend can be built, tested and demoed with no server at all.
//
// It is deliberately STATEFUL and a little smart: uploads move through the pipeline stages over a few seconds, editing the
// job description's skills really changes the ranking, and skill-gap analyses are computed from the candidate's passages
// with verbatim quotes and exact character offsets (tests/mock.test.mjs checks this against the Python-generated fixtures).
//
// Failure drills: add `&fail=analysis:429`, `&fail=analysis:500`, `&fail=search:500` or `&fail=upload:413` to the URL.

import { displaySkill, clamp, toCsv } from "./format.js";

export const FIXTURE_FILES = ["health", "me", "documents", "jd", "jd_list", "search", "pool", "chunks", "candidates", "pii", "pdf_url"];

export async function loadFixtures(base = "fixtures", fetchImpl = (...a) => globalThis.fetch(...a)) {
  const entries = await Promise.all(
    FIXTURE_FILES.map(async (name) => {
      const res = await fetchImpl(`${base}/${name}.json`);
      if (!res.ok) throw new Error(`Could not load fixture ${name}.json (${res.status})`);
      return [name, await res.json()];
    }),
  );
  return Object.fromEntries(entries);
}

const uuid = () =>
  globalThis.crypto?.randomUUID?.() ??
  "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });

const json = (data, status = 200, headers = {}) =>
  new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json", ...headers } });
const fail = (status, code, message, headers = {}) => json({ error: { code, message } }, status, headers);

// ---- scoring (mirrors the weights used to generate the fixtures) -------------------------------------------------
const WEIGHTS = {
  required_skill_coverage: 0.3, rrf_sum_top3: 0.15, skills_in_experience_ratio: 0.15, preferred_skill_coverage: 0.1,
  years_gap: 0.1, experience_section_hit: 0.08, title_match: 0.07, edu_gap: 0.05,
};

export function rescore(result, candidate, spec) {
  const have = new Set(candidate.profile.skills.map((s) => s.toLowerCase()));
  const req = spec.required_skills ?? [];
  const pref = spec.preferred_skills ?? [];
  const matchedReq = req.filter((s) => have.has(s));
  const matchedPref = pref.filter((s) => have.has(s));
  const v = { ...result.explanation.feature_values };
  v.required_skill_coverage = req.length ? matchedReq.length / req.length : 0.5;
  v.preferred_skill_coverage = pref.length ? matchedPref.length / pref.length : 0.5;
  v.years_gap = spec.min_years != null ? clamp(0.5 + (candidate.years_experience - spec.min_years) / 10, 0, 1) : 0.5;
  v.edu_gap = spec.min_education == null || candidate.education_level >= spec.min_education ? 1 : 0.25;
  const norm = { ...v, rrf_sum_top3: clamp(v.rrf_sum_top3 / 0.098, 0, 1) };
  let score = 0;
  const contributions = {};
  for (const [k, w] of Object.entries(WEIGHTS)) {
    score += w * norm[k] * 100;
    contributions[k] = Math.round(w * (norm[k] - 0.5) * 100 * 100) / 100;
  }
  return {
    ...result,
    match_score: Math.round(score * 10) / 10,
    matched_required: matchedReq,
    missing_required: req.filter((s) => !have.has(s)),
    matched_preferred: matchedPref,
    missing_preferred: pref.filter((s) => !have.has(s)),
    explanation: { ...result.explanation, feature_values: v, feature_contributions: contributions },
  };
}

// ---- skill-gap analysis (mirrors scripts/make_fixtures.py) ------------------------------------------------------
const ALIASES = {
  rest: ["REST APIs", "REST"], postgresql: ["PostgreSQL", "Postgres"], aws: ["AWS"], "ci/cd": ["CI/CD"],
  "node.js": ["Node.js"], "spring boot": ["Spring Boot"],
};
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function skillPattern(skill) {
  const names = ALIASES[skill] ?? [displaySkill(skill), skill];
  const alts = [...new Set(names)].sort((a, b) => b.length - a.length).map(escapeRe).join("|");
  return new RegExp(`(?<![\\w+#.])(?:${alts})(?![\\w+#])`, "i");
}

function sentenceWith(skill, text) {
  const m = skillPattern(skill).exec(text);
  if (!m) return null;
  const ls = text.lastIndexOf("\n", m.index - 1) + 1;
  let le = text.indexOf("\n", m.index + m[0].length);
  if (le === -1) le = text.length;
  const line = text.slice(ls, le);
  const stripped = line.replace(/^[- ]+/, "");
  const start = ls + (line.length - stripped.length);
  return { quote: text.slice(start, le), start, end: le };
}

export function buildAnalysis(spec, chunks, years = null) {
  const required = spec.required_skills ?? [];
  const skills = [];
  const resolved = [];
  for (const skill of [...new Set([...required, ...(spec.preferred_skills ?? [])])]) {
    const cites = [];
    let strong = false;
    for (const ch of chunks) {
      const hit = sentenceWith(skill, ch.text);
      if (!hit) continue;
      let { quote, start, end } = hit;
      const words = [...quote.matchAll(/\S+/g)];
      if (words.length > 25) {
        quote = quote.slice(0, words[24].index + words[24][0].length);
        end = start + quote.length;
      }
      cites.push({ chunk_id: ch.chunk_id, quote });
      resolved.push({ skill, chunk_id: ch.chunk_id, quote, text_scrubbed: ch.text, page_no: ch.page_no, section: ch.section, start, end });
      strong = strong || ch.section === "experience";
      if (cites.length === 2) break;
    }
    if (!cites.length) skills.push({ skill, status: "missing", reasoning: "No mention found in the resume.", citations: [] });
    else if (strong) skills.push({ skill, status: "present", reasoning: "Used in work experience with concrete scope.", citations: cites });
    else skills.push({ skill, status: "partial", reasoning: "Listed, but no work context shows how it was used.", citations: cites });
  }
  const reqPresent = skills.filter((s) => required.includes(s.skill) && s.status === "present");
  const reqMissing = skills.filter((s) => required.includes(s.skill) && s.status === "missing");
  return {
    result: {
      summary: `${reqPresent.length} of ${required.length} required skills are shown in work experience${reqMissing.length ? `; ${reqMissing.length} have no evidence.` : "."}`,
      skills,
      strengths: reqPresent.slice(0, 3).map((s) => `Hands-on ${displaySkill(s.skill)} in production work.`),
      risks: [
        ...reqMissing.slice(0, 3).map((s) => `No evidence of ${displaySkill(s.skill)}.`),
        ...(spec.min_years != null && years != null && years < spec.min_years
          ? [`Only ${Math.round(years)} years of experience against a minimum of ${Math.round(spec.min_years)}.`] : []),
      ],
      interview_questions: [
        ...reqPresent.slice(0, 2).map((s) => `Describe a system you built with ${displaySkill(s.skill)} and how you tested it.`),
        ...reqMissing.slice(0, 2).map((s) => `How would you get productive with ${displaySkill(s.skill)}?`),
      ],
      dropped_unverified_claims: 0,
      model: "fixture-model",
      prompt_version: "skillgap_v1",
    },
    citations_resolved: resolved,
    from_cache: false,
  };
}

// ---- job description parsing (a tiny stand-in for the LLM) -------------------------------------------------------
export function parseJd(text, knownSkills) {
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  const title = lines[0] && lines[0].length <= 80 ? lines[0] : null;
  const prefIdx = text.search(/^.*\b(preferred|nice to have|good to have|bonus)\b.*$/im);
  const requiredText = prefIdx === -1 ? text : text.slice(0, prefIdx);
  const preferredText = prefIdx === -1 ? "" : text.slice(prefIdx);
  const find = (t) =>
    knownSkills
      .map((s) => ({ s, m: skillPattern(s).exec(t) }))
      .filter((x) => x.m)
      .sort((a, b) => a.m.index - b.m.index)
      .map((x) => x.s);
  const required = find(requiredText);
  const preferred = find(preferredText).filter((s) => !required.includes(s));
  const years = [...text.matchAll(/(\d{1,2})\s*\+?\s*(?:years|yrs)/gi)].map((m) => Number(m[1]));
  const low = text.toLowerCase();
  const edu = /\bph\.?d\b/.test(low) ? 4 : /master|m\.?tech|\bmsc\b/.test(low) ? 3 : /bachelor|b\.?tech|degree/.test(low) ? 2 : null;
  return {
    title, raw_text: text, required_skills: required, preferred_skills: preferred,
    min_years: years.length ? Math.min(...years) : null, min_education: edu,
    responsibilities: lines.filter((l) => /^[-*•]/.test(l)).map((l) => l.replace(/^[-*•]\s*/, "").slice(0, 160)).slice(0, 6),
  };
}

// ---- the transport -----------------------------------------------------------------------------------------------
const STAGES = {
  ready: [["uploaded", 0], ["extracting", 900], ["embedding", 2400], ["ready", 3600]],
  failed: [["uploaded", 0], ["extracting", 900], ["failed", 1800]],
  needs_ocr: [["uploaded", 0], ["extracting", 900], ["needs_ocr", 1500]],
};

function outcomeFor(filename) {
  if (/corrupt|broken|bad[_-]/i.test(filename)) return { kind: "failed", error: "The file is corrupt or not a readable PDF." };
  if (/locked|password|encrypted/i.test(filename)) return { kind: "failed", error: "The PDF is password protected." };
  if (/scan|ocr/i.test(filename)) return { kind: "needs_ocr", error: "Scanned PDF: no selectable text found (OCR is not enabled)." };
  return { kind: "ready", error: null };
}

export function createMockTransport({ fixtures, now = () => Date.now(), sleep, latency, failures = {} } = {}) {
  const wait = sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)));
  const lat = { fast: 120, upload: 140, search: 700, analysis: 1800, parse: 900, ...(latency ?? {}) };
  const t0 = now();
  const candidates = fixtures.candidates;
  const knownSkills = [...new Set([...Object.values(candidates).flatMap((c) => c.profile.skills), "redis", "kafka", "aws", "graphql"])];

  // documents: fixture rows that are still "in flight" become simulated uploads so the library settles by itself
  const docs = fixtures.documents.items.map((d) => {
    const inFlight = ["uploaded", "extracting", "scrubbing", "chunking", "parsing", "embedding"].includes(d.status);
    return inFlight ? { ...d, _sim: { start: t0 - 600, delay: 0, outcome: outcomeFor(d.filename) } } : { ...d };
  });
  const jobs = new Map();
  const jds = new Map();
  jds.set(fixtures.jd.id, structuredClone(fixtures.jd));
  const jdItems = Array.isArray(fixtures.jd_list) ? fixtures.jd_list : (fixtures.jd_list?.items ?? []);  // backend: {items}
  for (const s of jdItems) {
    if (!jds.has(s.id)) {
      jds.set(s.id, {
        id: s.id, title: s.title,
        spec: s.spec ?? { title: s.title, raw_text: `${s.title}\nRequirements: ${s.required_skills.join(", ")}.`, required_skills: s.required_skills, preferred_skills: [], min_years: null, min_education: null, responsibilities: [] },
        created_at: s.created_at, updated_at: s.updated_at ?? s.created_at,
      });
    }
  }
  const lastSearchByJd = new Map(jdItems.filter((s) => s.last_search_id).map((s) => [s.id, s.last_search_id]));
  const searches = new Map([[fixtures.search.search_id, structuredClone(fixtures.search)]]);
  const analysisCache = new Map();
  const feedback = [];

  function statusOf(doc, t) {
    if (!doc._sim) return { status: doc.status, error: doc.error };
    const { start, delay, outcome } = doc._sim;
    const elapsed = t - start - delay;
    const stages = STAGES[outcome.kind];
    let current = stages[0][0];
    for (const [name, at] of stages) if (elapsed >= at) current = name;
    return { status: current, error: current === "failed" || current === "needs_ocr" ? outcome.error : null };
  }

  const view = (doc, t) => {
    const { status, error } = statusOf(doc, t);
    const { _sim, ...rest } = doc;
    return { ...rest, status, error, page_count: status === "ready" ? (doc.page_count ?? 1) : doc.page_count };
  };

  const countsFor = (items) => ({
    all: items.length,
    ready: items.filter((d) => d.status === "ready").length,
    processing: items.filter((d) => !["ready", "failed", "needs_ocr"].includes(d.status)).length,
    failed: items.filter((d) => d.status === "failed").length,
    needs_ocr: items.filter((d) => d.status === "needs_ocr").length,
  });

  function jobView(job, t) {
    const items = job.docIds.map((id) => docs.find((d) => d.id === id)).filter(Boolean).map((d) => view(d, t));
    const done = items.filter((d) => d.status === "ready").length;
    const failed = items.filter((d) => d.status === "failed" || d.status === "needs_ocr").length;
    const finished = done + failed >= job.docIds.length;
    return {
      id: job.id, status: !finished ? "running" : failed ? "completed_with_errors" : "completed", total: job.docIds.length, done, failed,
      created_at: job.created_at, finished_at: finished ? new Date(t).toISOString() : null,
      documents: items.map((d) => ({ id: d.id, filename: d.filename, status: d.status, error: d.error })),
    };
  }

  function newJob(ids) {
    const job = { id: uuid(), docIds: ids, created_at: new Date(now()).toISOString() };
    jobs.set(job.id, job);
    return job;
  }

  function startSim(doc, index) {
    doc._sim = { start: now(), delay: index * 450, outcome: outcomeFor(doc.filename) };
  }

  async function runSearch(body) {
    const jd = jds.get(body.jd_id);
    if (!jd) return fail(404, "not_found", "Job description not found");
    if (failures.search) return fail(Number(failures.search), "internal_error", "Internal server error");
    const spec = jd.spec;
    const f = body.filters ?? {};
    let rows = fixtures.pool
      .filter((r) => !f.min_years || r.years_experience >= f.min_years)
      .filter((r) => !f.min_education || r.education_level >= f.min_education)
      .filter((r) => (f.required_skills ?? []).every((s) => candidates[r.candidate_id].profile.skills.includes(s)))
      .map((r) => rescore(r, candidates[r.candidate_id], spec))
      .sort((a, b) => b.match_score - a.match_score || a.label.localeCompare(b.label))
      .slice(0, body.top_k ?? 10)
      .map((r, i) => ({ ...r, rank: i + 1 }));
    await wait(lat.search);
    const res = {
      search_id: uuid(), jd_id: jd.id, created_at: new Date(now()).toISOString(), blind: body.blind !== false, results: rows,
      timings: { query_build_ms: 4.1, retrieval_ms: 36.8, rerank_ms: 12.4, total_ms: 58.9 }, warnings: [],
      reranker_version: "mock-rerank-1", embedder: "mock-embedder-384", job_spec: spec,
    };
    searches.set(res.search_id, res);
    lastSearchByJd.set(jd.id, res.search_id);
    return json(res);
  }

  async function runAnalysis(body) {
    const jd = jds.get(body.jd_id);
    const chunks = fixtures.chunks[body.candidate_id];
    if (!jd) return fail(404, "not_found", "Job description not found");
    if (!chunks) return fail(404, "not_found", "Candidate not found");
    const key = `${jd.id}:${body.candidate_id}:${JSON.stringify([jd.spec.required_skills, jd.spec.preferred_skills])}`;
    if (analysisCache.has(key)) {
      await wait(lat.fast);
      return json({ ...analysisCache.get(key), from_cache: true });
    }
    await wait(lat.analysis);
    if (failures.analysis) {
      const status = Number(failures.analysis);
      return status === 429
        ? fail(429, "rate_limited", "Too many requests. Try again in 12s.", { "retry-after": "12" })
        : fail(status, "internal_error", "The analysis model did not respond. Try again.");
    }
    const out = buildAnalysis(jd.spec, chunks, candidates[body.candidate_id]?.years_experience ?? null);
    analysisCache.set(key, out);
    return json(out);
  }

  async function handle(method, path, query, body) {
    const t = now();
    let m;
    if (method === "GET" && path === "/health") return json({ ...fixtures.health, auth_mode: "mock" });
    if (method === "GET" && path === "/me") return json(fixtures.me);

    if (method === "GET" && path === "/documents") {
      await wait(lat.fast);
      const all = docs.map((d) => view(d, t)).sort((a, b) => b.created_at.localeCompare(a.created_at));
      const q = String(query?.q ?? "").toLowerCase();
      const status = query?.status;
      const filtered = all
        .filter((d) => !q || d.filename.toLowerCase().includes(q))
        .filter((d) => !status || status === "all" || (status === "processing" ? !["ready", "failed", "needs_ocr"].includes(d.status) : d.status === status));
      const pageSize = Math.min(100, Math.max(1, Number(query?.page_size) || 20));
      const page = Math.max(1, Number(query?.page) || 1);
      return json({ items: filtered.slice((page - 1) * pageSize, page * pageSize), total: filtered.length, page, page_size: pageSize, counts: countsFor(all) });
    }
    if (method === "POST" && path === "/documents/retry") {
      const ids = body?.document_ids ?? [];
      const hit = docs.filter((d) => ids.includes(d.id));
      hit.forEach((d, i) => startSim(d, i));
      return json({ job_id: newJob(hit.map((d) => d.id)).id });
    }
    if ((m = path.match(/^\/documents\/([^/]+)\/pdf-url$/)) && method === "GET") {
      return docs.some((d) => d.id === m[1]) ? json(fixtures.pdf_url) : fail(404, "not_found", "Document not found");
    }
    if ((m = path.match(/^\/documents\/([^/]+)$/)) && method === "DELETE") {
      const i = docs.findIndex((d) => d.id === m[1]);
      if (i === -1) return fail(404, "not_found", "Document not found");
      docs.splice(i, 1);
      return new Response(null, { status: 204 });
    }
    if ((m = path.match(/^\/jobs\/([^/]+)$/)) && method === "GET") {
      const job = jobs.get(m[1]);
      return job ? json(jobView(job, t)) : fail(404, "not_found", "Job not found");
    }

    if (method === "GET" && path === "/job-descriptions") {
      await wait(lat.fast);
      const items = [...jds.values()].sort((a, b) => b.created_at.localeCompare(a.created_at))
        .map((j) => ({ ...j, required_skills: j.spec.required_skills, last_search_id: lastSearchByJd.get(j.id) ?? null }));
      return json({ items });  // same shape as the backend
    }
    if (method === "POST" && path === "/job-descriptions") {
      const text = String(body?.text ?? "");
      if (text.trim().length < 40) return fail(422, "validation_error", "text: String should have at least 40 characters");
      await wait(lat.parse);
      const spec = parseJd(text, knownSkills);
      const nowIso = new Date(now()).toISOString();
      const jd = { id: uuid(), title: body?.title || spec.title, spec: { ...spec, title: body?.title || spec.title }, created_at: nowIso, updated_at: nowIso };
      jds.set(jd.id, jd);
      return json(jd);
    }
    if ((m = path.match(/^\/job-descriptions\/([^/]+)$/))) {
      const jd = jds.get(m[1]);
      if (!jd) return fail(404, "not_found", "Job description not found");
      if (method === "GET") return json(jd);
      if (method === "PUT") {
        jd.title = body?.title ?? body?.spec?.title ?? jd.title;
        jd.spec = { ...body.spec, raw_text: jd.spec.raw_text };
        jd.updated_at = new Date(now()).toISOString();
        return json(jd);
      }
    }

    if (method === "POST" && path === "/search") return runSearch(body ?? {});
    if ((m = path.match(/^\/search\/([^/]+)\/export\.csv$/)) && method === "GET") {
      const s = searches.get(m[1]);
      if (!s) return fail(404, "not_found", "Search not found");
      const rows = [["rank", "candidate", "match_score", "years_experience", "education_level", "matched_required", "missing_required"],
        ...s.results.map((r) => [r.rank, r.label, r.match_score, r.years_experience, r.education_level, r.matched_required.join("; "), r.missing_required.join("; ")])];
      return new Response(toCsv(rows), { status: 200, headers: { "content-type": "text/csv" } });
    }
    if ((m = path.match(/^\/search\/([^/]+)$/)) && method === "GET") {
      const s = searches.get(m[1]);
      return s ? json(s) : fail(404, "not_found", "Search not found");
    }
    if (method === "POST" && path === "/analysis") return runAnalysis(body ?? {});
    if ((m = path.match(/^\/candidates\/([^/]+)$/)) && method === "GET") {
      const c = candidates[m[1]];
      if (!c) return fail(404, "not_found", "Candidate not found");
      await wait(lat.fast);
      return json(query?.reveal === "true" ? { ...c, pii: fixtures.pii[m[1]] } : { ...c, pii: null });
    }
    if (method === "POST" && path === "/feedback") {
      feedback.push(body);
      return new Response("", { status: 201 });
    }
    return fail(404, "not_found", `No mock route for ${method} ${path}`);
  }

  return {
    async request({ method, path, query, body }) {
      return handle(method, path, query, body ? JSON.parse(body) : undefined);
    },
    async upload({ path, form, onProgress }) {
      if (path === "/job-descriptions/upload") {
        const file = form.get("file");
        if (!file) return fail(422, "missing_file", "Choose a job description file.");
        if (!/\.txt$/i.test(file.name)) return fail(501, "mock_file_type_unsupported", "PDF and DOCX extraction needs the local API server; TXT uploads work in mock mode.");
        if (!file.size) return fail(422, "empty", "The selected file is empty.");
        if (file.size > 10 * 1024 * 1024) return fail(413, "file_too_large", "The file must be 10 MB or smaller.");
        const text = (await file.text()).replace(/\r\n?/g, "\n").replace(/[\t\f\v ]+/g, " ").replace(/ *\n */g, "\n").trim();
        if (text.length < 40) return fail(422, "validation_error", "The file must contain at least 40 characters of job description text.");
        if (text.length > 20000) return fail(413, "jd_text_too_long", "Extracted text exceeds 20,000 characters.");
        await wait(lat.parse);
        const spec = parseJd(text, knownSkills);
        const nowIso = new Date(now()).toISOString();
        const title = form.get("title") || spec.title;
        const jd = { id: uuid(), title, spec: { ...spec, title, raw_text: text }, created_at: nowIso, updated_at: nowIso };
        jds.set(jd.id, jd);
        return json(jd, 201);
      }
      if (path !== "/documents/upload") return fail(404, "not_found", `No mock route for POST ${path}`);
      if (failures.upload) return fail(Number(failures.upload), "payload_too_large", "The upload is too large.");
      const files = form.getAll("files");
      const accepted = [];
      const rejected = [];
      for (const f of files) {
        if (!/\.pdf$/i.test(f.name)) rejected.push({ filename: f.name, reason: "Only PDF files are accepted." });
        else if (f.size > 10 * 1024 * 1024) rejected.push({ filename: f.name, reason: "Larger than 10 MB." });
        else if (accepted.length >= 100) rejected.push({ filename: f.name, reason: "Over the limit of 100 files per upload." });
        else accepted.push(f);
      }
      const total = files.reduce((n, f) => n + (f.size || 1), 0) || 1;
      for (let i = 1; i <= 5; i++) {
        await wait(lat.upload);
        onProgress?.(Math.round((total * i) / 5), total);
      }
      if (!accepted.length) return fail(400, "no_valid_files", "None of the files could be accepted.");
      const created = accepted.map((f, i) => {
        const doc = {
          id: uuid(), filename: f.name, doc_type: "resume", status: "uploaded", error: null, page_count: null,
          suspicious_hidden_text: false, created_at: new Date(now()).toISOString(), candidate_id: null,
          years_experience: null, education_level: null, skills: [],
        };
        startSim(doc, i);
        docs.unshift(doc);
        return doc;
      });
      return json({ job_id: newJob(created.map((d) => d.id)).id, document_ids: created.map((d) => d.id), rejected });
    },
    _state: { docs, jds, searches, feedback },
  };
}
