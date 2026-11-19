import test from "node:test";
import assert from "node:assert/strict";
import { createApi, fetchTransport, ApiError } from "../js/api.js";

const json = (status, body, headers = {}) => new Response(body === null ? null : JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });

function make({ responses, token = "T", onUnauthorized } = {}) {
  const calls = [];
  const queue = [...responses];
  const transport = {
    request: async (req) => { calls.push(req); const r = queue.shift(); if (r instanceof Error) throw r; return r; },
    upload: async (req) => { calls.push({ upload: true, ...req }); const r = queue.shift(); if (r instanceof Error) throw r; return r; },
  };
  const api = createApi({ transport, getToken: async () => token, onUnauthorized });
  return { api, calls };
}

test("adds bearer token and JSON body; returns parsed JSON", async () => {
  const { api, calls } = make({ responses: [json(200, { id: "1" })] });
  const out = await api.createJD("x".repeat(50), "Title");
  assert.deepEqual(out, { id: "1" });
  assert.equal(calls[0].method, "POST");
  assert.equal(calls[0].path, "/job-descriptions");
  assert.equal(calls[0].headers.Authorization, "Bearer T");
  assert.equal(calls[0].headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(calls[0].body), { title: "Title", text: "x".repeat(50) });
});

test("no token means no Authorization header", async () => {
  const { api, calls } = make({ responses: [json(200, {})], token: null });
  await api.health();
  assert.equal("Authorization" in calls[0].headers, false);
});

test("error envelope becomes ApiError with code, message and Retry-After", async () => {
  const { api } = make({ responses: [json(429, { error: { code: "rate_limited", message: "Too many requests. Try again in 12s." } }, { "retry-after": "12" })] });
  await assert.rejects(() => api.analyze({}), (e) => e instanceof ApiError && e.status === 429 && e.code === "rate_limited" && e.retryAfter === 12 && /12s/.test(e.message));
});

test("FastAPI-style {detail} and non-JSON error bodies still give a usable message", async () => {
  const a = make({ responses: [json(400, { detail: "bad thing" })] });
  await assert.rejects(() => a.api.getJob("x"), { message: "bad thing", status: 400 });
  const b = make({ responses: [new Response("<html>Bad gateway</html>", { status: 502 })] });
  await assert.rejects(() => b.api.getJob("x"), { message: "Request failed (502).", status: 502, code: "http_error" });
});

test("network failure -> ApiError status 0 with friendly text; AbortError passes through", async () => {
  const a = make({ responses: [new TypeError("fetch failed")] });
  await assert.rejects(() => a.api.health(), (e) => e instanceof ApiError && e.isNetwork && /Cannot reach the server/.test(e.message));
  const abort = Object.assign(new Error("aborted"), { name: "AbortError" });
  const b = make({ responses: [abort] });
  await assert.rejects(() => b.api.health(), { name: "AbortError" });
});

test("401 triggers one refresh + retry; second 401 surfaces", async () => {
  let refreshed = 0;
  const ok = make({ responses: [json(401, { error: { code: "unauthorized", message: "expired" } }), json(200, { ok: 1 })], onUnauthorized: async () => { refreshed++; return true; } });
  assert.deepEqual(await ok.api.me(), { ok: 1 });
  assert.equal(refreshed, 1);
  assert.equal(ok.calls.length, 2);
  const fail = make({ responses: [json(401, { error: { code: "unauthorized", message: "expired" } }), json(401, { error: { code: "unauthorized", message: "still" } })], onUnauthorized: async () => true });
  await assert.rejects(() => fail.api.me(), { status: 401, message: "still" });
  assert.equal(fail.calls.length, 2);
  const noRefresh = make({ responses: [json(401, { error: { code: "unauthorized", message: "no" } })], onUnauthorized: async () => false });
  await assert.rejects(() => noRefresh.api.me(), { status: 401 });
  assert.equal(noRefresh.calls.length, 1);
});

test("204 returns null, empty 201 body returns null, blob responses return a Blob", async () => {
  const { api } = make({ responses: [new Response(null, { status: 204 }), new Response("", { status: 201 }), new Response("a,b\r\n1,2", { status: 200, headers: { "content-type": "text/csv" } })] });
  assert.equal(await api.deleteDocument("d1"), null);
  assert.equal(await api.feedback({}), null);
  const blob = await api.exportCsv("s1");
  assert.equal(await blob.text(), "a,b\r\n1,2");
});

test("paths and query parameters are encoded; empty params are dropped", async () => {
  const { api, calls } = make({ responses: [json(200, {}), json(200, {}), json(200, {})] });
  await api.getCandidate("a/b c", true);
  await api.getCandidate("x", false);
  await api.listDocuments({ status: "ready", q: "", page: 2, page_size: undefined });
  assert.equal(calls[0].path, "/candidates/a%2Fb%20c");
  assert.deepEqual(calls[0].query, { reveal: "true" });
  assert.deepEqual(calls[1].query, { reveal: undefined });
  assert.deepEqual(calls[2].query, { status: "ready", q: "", page: 2, page_size: undefined });
});

test("fetchTransport builds URLs with the /api/v1 prefix and filtered query string", async () => {
  const seen = [];
  const t = fetchTransport({ baseUrl: "https://app.example", fetchImpl: async (url, init) => { seen.push({ url, init }); return json(200, {}); } });
  await t.request({ method: "GET", path: "/documents", query: { status: "ready", q: "", page: 2 }, headers: { A: "b" } });
  assert.equal(seen[0].url, "https://app.example/api/v1/documents?status=ready&page=2");
  assert.equal(seen[0].init.method, "GET");
});

class FakeXhr {
  static last;
  constructor() { FakeXhr.last = this; this.upload = {}; this.headers = {}; }
  open(m, u) { this.method = m; this.url = u; }
  setRequestHeader(k, v) { this.headers[k] = v; }
  getResponseHeader(k) { return k === "content-type" ? "application/json" : null; }
  send(form) {
    this.form = form;
    this.upload.onprogress({ lengthComputable: true, loaded: 50, total: 200 });
    this.upload.onprogress({ lengthComputable: true, loaded: 200, total: 200 });
    this.status = 200;
    this.responseText = JSON.stringify({ job_id: "j1", document_ids: ["d1"], rejected: [] });
    this.onload();
  }
}

test("uploadDocuments posts multipart with doc_type + files, reports progress, and does NOT set Content-Type", async () => {
  const progress = [];
  const transport = fetchTransport({ baseUrl: "", XHR: FakeXhr });
  const api = createApi({ transport, getToken: async () => "T" });
  const file = new File(["%PDF-1.4"], "cv.pdf", { type: "application/pdf" });
  const out = await api.uploadDocuments([file], { onProgress: (l, t) => progress.push([l, t]) });
  assert.deepEqual(out, { job_id: "j1", document_ids: ["d1"], rejected: [] });
  assert.deepEqual(progress, [[50, 200], [200, 200]]);
  const x = FakeXhr.last;
  assert.equal(x.method, "POST");
  assert.equal(x.url, "/api/v1/documents/upload");
  assert.equal(x.headers.Authorization, "Bearer T");
  assert.equal("Content-Type" in x.headers, false, "the browser must set the multipart boundary itself");
  assert.equal(x.form.get("doc_type"), "resume");
  assert.equal(x.form.getAll("files").length, 1);
  assert.equal(x.form.getAll("files")[0].name, "cv.pdf");
});

test("upload network error maps to ApiError status 0", async () => {
  class Boom extends FakeXhr { send() { this.onerror(); } }
  const api = createApi({ transport: fetchTransport({ XHR: Boom }), getToken: async () => "T" });
  await assert.rejects(() => api.uploadDocuments([new File(["x"], "a.pdf")]), { status: 0 });
});
