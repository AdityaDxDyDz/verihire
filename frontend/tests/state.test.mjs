import test from "node:test";
import assert from "node:assert/strict";
import { createStore, safeStorage } from "../js/state.js";

test("store merges patches, notifies subscribers, and unsubscribes", () => {
  const s = createStore({ a: 1, b: 2 });
  const seen = [];
  const off = s.subscribe((st) => seen.push(st.a));
  s.set({ a: 5 });
  s.set((st) => ({ a: st.a + 1 }));
  off();
  s.set({ a: 99 });
  assert.deepEqual(seen, [5, 6]);
  assert.deepEqual(s.get(), { a: 99, b: 2 });
});

test("safeStorage works with a normal backend", () => {
  const m = new Map();
  const st = safeStorage(() => ({ getItem: (k) => m.get(k) ?? null, setItem: (k, v) => m.set(k, v), removeItem: (k) => m.delete(k) }));
  st.set("k", "v");
  assert.equal(st.get("k"), "v");
  st.remove("k");
  assert.equal(st.get("k"), null);
});

test("safeStorage falls back to memory when storage throws", () => {
  const boom = () => { throw new Error("blocked"); };
  const st = safeStorage(() => ({ getItem: boom, setItem: boom, removeItem: boom }));
  st.set("k", "v");
  assert.equal(st.get("k"), "v");
  st.remove("k");
  assert.equal(st.get("k"), null);
  const st2 = safeStorage(boom);
  st2.set("a", "1");
  assert.equal(st2.get("a"), "1");
});
