import test from "node:test";
import assert from "node:assert/strict";
import { parseHash, buildHash } from "../js/router.js";

test("empty hash goes to the library", () => {
  assert.equal(parseHash("").name, "library");
  assert.equal(parseHash("#").name, "library");
  assert.equal(parseHash("#/").name, "library");
});

test("static and parameterised routes parse", () => {
  assert.equal(parseHash("#/login").name, "login");
  assert.equal(parseHash("#/jd").name, "jd");
  assert.deepEqual(parseHash("#/jd/abc-123").params, { id: "abc-123" });
  assert.equal(parseHash("#/jd/abc-123").name, "jdEdit");
  const r = parseHash("#/results/s1/c9");
  assert.equal(r.name, "candidate");
  assert.deepEqual(r.params, { id: "s1", cid: "c9" });
  assert.equal(parseHash("#/results/s1").name, "results");
});

test("query string is parsed; compare ids survive", () => {
  const r = parseHash("#/compare/s1?ids=a,b,c&x=1");
  assert.equal(r.name, "compare");
  assert.deepEqual(r.query, { ids: "a,b,c", x: "1" });
});

test("unknown paths and extra segments are notfound; bad encoding does not throw", () => {
  assert.equal(parseHash("#/nope").name, "notfound");
  assert.equal(parseHash("#/results/a/b/c").name, "notfound");
  assert.equal(parseHash("#/jd/%E0%A4%A").name, "notfound");
});

test("buildHash is the inverse of parseHash", () => {
  const cases = [
    ["login", {}, {}],
    ["jdEdit", { id: "a b/c" }, {}],
    ["candidate", { id: "s1", cid: "c9" }, {}],
    ["compare", { id: "s1" }, { ids: "a,b" }],
  ];
  for (const [name, params, query] of cases) {
    const parsed = parseHash(buildHash(name, params, query));
    assert.equal(parsed.name, name);
    assert.deepEqual(parsed.params, params);
    assert.deepEqual(parsed.query, query);
  }
});

test("buildHash rejects unknown routes and missing params; drops empty query values", () => {
  assert.throws(() => buildHash("nope"), /Unknown route/);
  assert.throws(() => buildHash("results", {}), /Missing param/);
  assert.equal(buildHash("library", {}, { q: "", p: undefined }), "#/library");
});
