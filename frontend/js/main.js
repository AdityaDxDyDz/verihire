// Bootstrap: pick mock/real backend, wire auth + api + router + shell, and manage views, focus and titles.
import { h, mount } from "./dom.js";
import { Router, buildHash } from "./router.js";
import { createAuth } from "./auth.js";
import { createApi, fetchTransport, ApiError } from "./api.js";
import { createMockTransport, loadFixtures } from "./mock.js";
import { createStore, safeStorage } from "./state.js";
import { createToaster, confirmDialog, openModal } from "./components/overlay.js";
import { createShell } from "./shell.js";
import { loginView } from "./views/login.js";
import { libraryView } from "./views/library.js";
import { jdView } from "./views/jd.js";
import { resultsView } from "./views/results.js";
import { compareView } from "./views/compare.js";
import { interviewsView } from "./views/interviews.js";
import { interviewView } from "./views/interview.js";
import { emptyState } from "./components/ui.js";
import { linkButton } from "./components/ui.js";

const params = new URLSearchParams(location.search);
const userConfig = globalThis.VERIHIRE_CONFIG ?? {};
const mock = params.get("mock") === "1" || userConfig.mock === true;
const config = { apiBaseUrl: "", mode: "dev", ...userConfig, mode: mock ? "mock" : userConfig.mode ?? "dev" };
const storage = safeStorage(() => window.localStorage);

const auth = createAuth({ config, storage: { get: (k) => storage.get(k), set: (k, v) => storage.set(k, v), remove: (k) => storage.remove(k) } });
const toastRoot = document.getElementById("toasts");
const toast = createToaster(toastRoot);
const store = createStore({ lastResults: null });
const cache = new Map();
const viewRoot = document.getElementById("view");

function applyTheme(t) { if (t) document.documentElement.setAttribute("data-theme", t); else document.documentElement.removeAttribute("data-theme"); }
applyTheme(storage.get("verihire.theme"));
function toggleTheme() {
  const dark = document.documentElement.getAttribute("data-theme") === "dark" || (!document.documentElement.getAttribute("data-theme") && matchMedia("(prefers-color-scheme: dark)").matches);
  const next = dark ? "light" : "dark";
  applyTheme(next); storage.set("verihire.theme", next);
}

let router;
const shell = createShell(document.getElementById("shell"), {
  auth, config, mock, onTheme: toggleTheme,
  onSignOut: async () => { await auth.signOut(); cache.clear(); store.set({ lastResults: null }); router.navigate("login"); },
});

function failures() {
  const out = {};
  for (const f of params.getAll("fail")) { const [k, v] = f.split(":"); if (k && v) out[k] = v; }
  return out;
}

async function buildTransport() {
  if (!mock) return fetchTransport({ baseUrl: config.apiBaseUrl ?? "" });
  const fixtures = await loadFixtures("fixtures");
  return createMockTransport({ fixtures, failures: failures(), latency: params.get("fast") === "1" ? { fast: 5, upload: 5, search: 5, analysis: 5, parse: 5 } : undefined });
}

function reportError(err, fallback = "Something went wrong.") {
  if (err instanceof ApiError && err.status === 401) { auth.signOut().then(() => router.navigate("login")); return; }
  const text = err instanceof ApiError || err?.message ? (err.status === 0 ? err.message : `${fallback} ${err.message ?? ""}`.trim()) : fallback;
  toast.show(text, { tone: "bad", timeout: 8000 });
}

let current = null; // {name, key, view}
const VIEWS = { login: loginView, library: libraryView, jd: jdView, jdEdit: jdView, results: resultsView, candidate: resultsView, compare: compareView, interviews: interviewsView, interview: interviewView };
// "interview" is the public candidate page: it is deliberately NOT protected.
const PROTECTED = new Set(["library", "jd", "jdEdit", "results", "candidate", "compare", "interviews"]);

function notFound() {
  return { el: emptyState({ title: "Page not found", body: "That address does not exist.", action: linkButton({ label: "Go to the library", href: buildHash("library"), variant: "primary" }) }), title: "Not found" };
}

async function boot() {
  let transport;
  try { transport = await buildTransport(); }
  catch (err) { mount(viewRoot, emptyState({ title: "Could not start", body: err.message })); return; }
  const api = createApi({ transport, getToken: async () => (await auth.getToken?.()) ?? auth.getSession()?.token ?? null, onUnauthorized: () => auth.refresh() });
  const ctx = {
    api, auth, store, toast, config, cache, reportError,
    navigate: (name, p, q) => router.navigate(name, p, q),
    afterSignIn: () => router.navigate("library"),
    confirm: confirmDialog, openModal,
  };

  if (!mock) api.health().then((hh) => { shell.setStatus({ tone: hh.status === "ok" ? "ok" : "warn", text: hh.status === "ok" ? "Connected" : "Degraded" }); shell.draw(current?.route, store.get().lastResults); }).catch(() => { shell.setStatus({ tone: "bad", text: "Server unreachable" }); shell.draw(current?.route, store.get().lastResults); });

  router = new Router((route) => {
    if (PROTECTED.has(route.name) && !auth.isAuthed()) { router.navigate("login"); return; }
    if (route.name === "login" && auth.isAuthed()) { router.navigate("library"); return; }
    if (route.name === "results" || route.name === "candidate") store.set({ lastResults: route.params.id });
    const key = route.name === "results" || route.name === "candidate" ? `results:${route.params.id}` : route.name === "jd" || route.name === "jdEdit" ? `jd:${route.params.id ?? "new"}` : route.name === "compare" ? `compare:${route.params.id}:${route.query.ids}` : route.name === "interviews" ? `interviews:${route.params.id}` : route.name === "interview" ? `interview:${route.params.token}` : route.name;
    shell.draw(route, store.get().lastResults);
    if (current?.key === key && current.view.update) { current.view.update(route); current.route = route; return; }
    current?.view.destroy?.();
    const make = VIEWS[route.name];
    const view = make ? make(ctx, route) : notFound();
    current = { key, view, route };
    mount(viewRoot, view.el);
    document.title = `${view.title ?? "VeriHire"} · VeriHire`;
    if (route.name !== "candidate") { viewRoot.focus({ preventScroll: true }); window.scrollTo(0, 0); }
  });
  auth.onChange(() => shell.draw(current?.route, store.get().lastResults));
  router.start();
}
boot();
