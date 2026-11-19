// Security lint: untrusted text must never reach innerHTML/outerHTML/insertAdjacentHTML/document.write/eval.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "js");
const files = [];
(function walk(d) { for (const n of readdirSync(d)) { const p = join(d, n); statSync(p).isDirectory() ? walk(p) : p.endsWith(".js") && files.push(p); } })(root);

test("no dangerous DOM sinks in frontend/js", () => {
  const bad = /\b(innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\s*\(|new Function)\b/;
  for (const f of files) {
    const code = readFileSync(f, "utf8").split("\n").filter((l) => !l.trim().startsWith("//"));
    code.forEach((line, i) => assert.ok(!bad.test(line), `${f}:${i + 1} uses a forbidden sink: ${line.trim()}`));
  }
});
