import test from "node:test";
import assert from "node:assert/strict";
import { createAuth, AuthError } from "../js/auth.js";

const memStorage = () => {
  const m = new Map();
  return { get: (k) => m.get(k) ?? null, set: (k, v) => m.set(k, v), remove: (k) => m.delete(k), _m: m };
};
const cfg = { mode: "supabase", supabaseUrl: "https://proj.supabase.co/", supabaseAnonKey: "anon-key" };
const json = (status, body) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

test("dev mode: token is dev:<normalised email>; invalid email is rejected", () => {
  const a = createAuth({ config: { mode: "dev" }, storage: memStorage() });
  assert.throws(() => a.signInDev("nope"), AuthError);
  a.signInDev("  You@Example.COM ");
  assert.equal(a.isAuthed(), true);
  assert.equal(a.getSession().token, "dev:you@example.com");
});

test("mock mode signs in without network", () => {
  const a = createAuth({ config: { mode: "mock" }, storage: memStorage(), fetchImpl: () => { throw new Error("no network"); } });
  assert.equal(a.signInMock().email, "demo@verihire.example");
});

test("session persists across instances and is dropped when the mode changes", async () => {
  const storage = memStorage();
  createAuth({ config: { mode: "dev" }, storage }).signInDev("a@b.co");
  assert.equal(createAuth({ config: { mode: "dev" }, storage }).isAuthed(), true);
  assert.equal(createAuth({ config: { mode: "supabase", supabaseUrl: "x" }, storage }).isAuthed(), false);
});

test("password sign-in sends apikey + credentials to the right URL and stores the session", async () => {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init });
    return json(200, { access_token: "AT", refresh_token: "RT", expires_in: 3600, user: { email: "u@x.co" } });
  };
  const a = createAuth({ config: cfg, storage: memStorage(), fetchImpl, now: () => 1_000_000 });
  await a.signInPassword("u@x.co", "secret1");
  assert.equal(calls[0].url, "https://proj.supabase.co/auth/v1/token?grant_type=password");
  assert.equal(calls[0].init.headers.apikey, "anon-key");
  assert.deepEqual(JSON.parse(calls[0].init.body), { email: "u@x.co", password: "secret1" });
  assert.deepEqual(a.getSession(), { token: "AT", refreshToken: "RT", expiresAt: 1_000_000 + 3_600_000, email: "u@x.co" });
});

test("wrong password gives a plain-language error and no session", async () => {
  const fetchImpl = async () => json(400, { error: "invalid_grant", error_description: "Invalid login credentials" });
  const a = createAuth({ config: cfg, storage: memStorage(), fetchImpl });
  await assert.rejects(() => a.signInPassword("u@x.co", "bad"), { message: "Email or password is incorrect." });
  assert.equal(a.isAuthed(), false);
});

test("network failure is reported as an AuthError, not a raw TypeError", async () => {
  const a = createAuth({ config: cfg, storage: memStorage(), fetchImpl: async () => { throw new TypeError("fetch failed"); } });
  await assert.rejects(() => a.signInPassword("u@x.co", "pw"), (e) => e instanceof AuthError && /Cannot reach/.test(e.message));
});

test("signUp: confirmation required vs immediate session; short password rejected locally", async () => {
  let calls = 0;
  const confirm = createAuth({ config: cfg, storage: memStorage(), fetchImpl: async () => { calls++; return json(200, { id: "1", email: "n@x.co" }); } });
  assert.deepEqual(await confirm.signUp("n@x.co", "longenough"), { needsConfirmation: true });
  assert.equal(confirm.isAuthed(), false);
  const direct = createAuth({ config: cfg, storage: memStorage(), fetchImpl: async () => json(200, { access_token: "A", refresh_token: "R", expires_in: 60, user: { email: "n@x.co" } }) });
  assert.deepEqual(await direct.signUp("n@x.co", "longenough"), { needsConfirmation: false });
  assert.equal(direct.isAuthed(), true);
  await assert.rejects(() => direct.signUp("n@x.co", "123"), /at least 6/);
  assert.equal(calls, 1);
});

test("getToken refreshes a token that expires within 60s, once, even for concurrent callers", async () => {
  let t = 0;
  let refreshCalls = 0;
  const fetchImpl = async (url, init) => {
    if (url.includes("grant_type=refresh_token")) {
      refreshCalls++;
      assert.deepEqual(JSON.parse(init.body), { refresh_token: "RT1" });
      return json(200, { access_token: "AT2", refresh_token: "RT2", expires_in: 3600, user: { email: "u@x.co" } });
    }
    return json(200, { access_token: "AT1", refresh_token: "RT1", expires_in: 100, user: { email: "u@x.co" } });
  };
  const a = createAuth({ config: cfg, storage: memStorage(), fetchImpl, now: () => t });
  await a.signInPassword("u@x.co", "pw");
  assert.equal(await a.getToken(), "AT1"); // 100 s left: fresh enough
  t = 50_000; // 50 s left: must refresh
  const [x, y] = await Promise.all([a.getToken(), a.getToken()]);
  assert.equal(x, "AT2");
  assert.equal(y, "AT2");
  assert.equal(refreshCalls, 1);
  assert.equal(a.getSession().refreshToken, "RT2");
});

test("failed refresh clears the session; network error during refresh keeps it", async () => {
  const bad = createAuth({ config: cfg, storage: memStorage(), now: () => 0, fetchImpl: async (u) => u.includes("refresh_token") ? json(400, { error: "invalid_grant" }) : json(200, { access_token: "A", refresh_token: "R", expires_in: 10, user: {} }) });
  await bad.signInPassword("u@x.co", "pw");
  assert.equal(await bad.refresh(), false);
  assert.equal(bad.isAuthed(), false);
  const flaky = createAuth({ config: cfg, storage: memStorage(), now: () => 0, fetchImpl: async (u) => { if (u.includes("refresh_token")) throw new TypeError("offline"); return json(200, { access_token: "A", refresh_token: "R", expires_in: 10, user: {} }); } });
  await flaky.signInPassword("u@x.co", "pw");
  assert.equal(await flaky.refresh(), false);
  assert.equal(flaky.isAuthed(), true);
});

test("signOut clears local state even if the logout call fails, and notifies listeners", async () => {
  const a = createAuth({ config: cfg, storage: memStorage(), fetchImpl: async (u) => { if (u.endsWith("/logout")) throw new TypeError("offline"); return json(200, { access_token: "A", refresh_token: "R", expires_in: 3600, user: {} }); } });
  await a.signInPassword("u@x.co", "pw");
  const seen = [];
  a.onChange((s) => seen.push(s));
  await a.signOut();
  assert.equal(a.isAuthed(), false);
  assert.deepEqual(seen, [null]);
  assert.equal(await a.getToken(), null);
});
