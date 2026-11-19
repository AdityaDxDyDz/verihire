import test from "node:test";
import assert from "node:assert/strict";
import * as v from "../js/validate.js";

const pdf = (name, size = 1000, type = "application/pdf") => ({ name, size, type });

test("accepts PDFs by extension or MIME type", () => {
  const r = v.validateFiles([pdf("a.pdf"), pdf("b.PDF", 5, ""), pdf("noext", 5, "application/pdf")]);
  assert.equal(r.accepted.length, 3);
  assert.equal(r.rejected.length, 0);
});

test("rejects non-PDF, empty, oversize and duplicates with specific reasons", () => {
  const r = v.validateFiles([
    pdf("notes.docx", 10, "application/msword"),
    pdf("empty.pdf", 0),
    pdf("huge.pdf", v.MAX_BYTES + 1),
    pdf("ok.pdf", 50),
    pdf("OK.pdf", 50),
  ]);
  assert.deepEqual(r.accepted.map((f) => f.name), ["ok.pdf"]);
  assert.deepEqual(r.rejected, [
    { name: "notes.docx", reason: "Only PDF files are accepted." },
    { name: "empty.pdf", reason: "The file is empty." },
    { name: "huge.pdf", reason: "Larger than 10 MB." },
    { name: "OK.pdf", reason: "Selected twice." },
  ]);
});

test("exactly the size limit is allowed; one byte more is not", () => {
  assert.equal(v.validateFiles([pdf("a.pdf", v.MAX_BYTES)]).accepted.length, 1);
  assert.equal(v.validateFiles([pdf("a.pdf", v.MAX_BYTES + 1)]).accepted.length, 0);
});

test("file-count limit counts files already in the batch", () => {
  const files = Array.from({ length: 5 }, (_, i) => pdf(`f${i}.pdf`, 10 + i));
  const r = v.validateFiles(files, { maxFiles: 3 });
  assert.equal(r.accepted.length, 3);
  assert.equal(r.rejected.length, 2);
  assert.match(r.rejected[0].reason, /limit of 3/);
  assert.equal(v.validateFiles(files, { maxFiles: 3, existingCount: 3 }).accepted.length, 0);
});

test("jdTextProblem enforces 40..20000 characters on trimmed text", () => {
  assert.match(v.jdTextProblem("too short"), /at least 40/);
  assert.match(v.jdTextProblem(`   ${"x".repeat(39)}   `), /39 so far/);
  assert.equal(v.jdTextProblem("x".repeat(40)), null);
  assert.match(v.jdTextProblem("x".repeat(20001)), /too long/);
});

test("skill helpers normalise, dedupe, limit and remove", () => {
  assert.equal(v.normalizeSkill("  Node.JS  "), "node.js");
  assert.equal(v.normalizeSkill("machine   learning,"), "machine learning");
  assert.deepEqual(v.parseSkillInput("Python, FastAPI;docker\n  Redis ,, "), ["python", "fastapi", "docker", "redis"]);
  assert.deepEqual(v.addSkills(["python"], "Python, Go"), ["python", "go"]);
  assert.deepEqual(v.addSkills(["a", "b"], "c, d", 3), ["a", "b", "c"]);
  assert.deepEqual(v.removeSkill(["a", "b", "c"], "b"), ["a", "c"]);
  assert.deepEqual(v.removeSkill(["a"], "zzz"), ["a"]);
});

test("sameJson detects edits, isEmail accepts/rejects", () => {
  assert.equal(v.sameJson({ a: [1, 2] }, { a: [1, 2] }), true);
  assert.equal(v.sameJson({ a: [1, 2] }, { a: [2, 1] }), false);
  assert.equal(v.isEmail("you@example.com"), true);
  assert.equal(v.isEmail("you@example"), false);
  assert.equal(v.isEmail("you example@x.com"), false);
  assert.equal(v.isEmail(""), false);
});
