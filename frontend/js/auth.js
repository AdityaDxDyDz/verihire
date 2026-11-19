// Authentication. Three modes, chosen by config:
//   "mock"     - `?mock=1`: no network, a demo user.
//   "dev"      - local development: token is `dev:<email>` (the backend only accepts it when AUTH_MODE=dev).
//   "supabase" - Supabase Auth over its REST API (email + password). No SDK needed, so it is easy to test.
// The browser only ever holds the user's access token. The Supabase anon key is public by design; the service key
// never reaches the frontend.

import { isEmail } from "./validate.js";

const KEY = "verihire.session";

export class AuthError extends Error {
  constructor(message) {
    super(message);
    this.name = "AuthError";
  }
}

function friendly(body, fallback) {
  const raw = String(body?.error_description ?? body?.msg ?? body?.message ?? body?.error ?? "");
  if (/invalid login credentials/i.test(raw)) return "Email or password is incorrect.";
  if (/email not confirmed/i.test(raw)) return "Confirm your email first. Check your inbox for the link.";
  if (/already registered|already been registered/i.test(raw)) return "An account with this email already exists. Sign in instead.";
  if (/password should be at least/i.test(raw)) return raw;
  if (/rate limit/i.test(raw)) return "Too many attempts. Wait a minute and try again.";
  return raw || fallback;
}

export function createAuth({ config, fetchImpl, storage, now = () => Date.now() }) {
  const mode = config.mode;
  const doFetch = fetchImpl ?? ((...a) => globalThis.fetch(...a));
  const listeners = new Set();
  let session = null;
  let refreshing = null;

  try {
    const raw = storage.get(KEY);
    if (raw) session = JSON.parse(raw);
  } catch {
    session = null;
  }
  if (session && session.mode && session.mode !== mode) session = null; // session from another mode (e.g. dev -> supabase)

  function persist() {
    if (session) storage.set(KEY, JSON.stringify({ ...session, mode }));
    else storage.remove(KEY);
    for (const fn of [...listeners]) fn(session);
  }

  const base = () => `${String(config.supabaseUrl ?? "").replace(/\/$/, "")}/auth/v1`;
  const headers = (extra = {}) => ({ apikey: config.supabaseAnonKey ?? "", "Content-Type": "application/json", ...extra });

  async function post(path, body, extraHeaders) {
    let res;
    try {
      res = await doFetch(`${base()}${path}`, { method: "POST", headers: headers(extraHeaders), body: JSON.stringify(body) });
    } catch {
      throw new AuthError("Cannot reach the sign-in service. Check your connection and try again.");
    }
    let data = null;
    try {
      data = await res.json();
    } catch { /* empty body */ }
    return { res, data };
  }

  function setFromTokenResponse(data, fallbackEmail) {
    session = {
      token: data.access_token,
      refreshToken: data.refresh_token ?? null,
      expiresAt: data.expires_in ? now() + Number(data.expires_in) * 1000 : (data.expires_at ? Number(data.expires_at) * 1000 : null),
      email: data.user?.email ?? fallbackEmail ?? null,
    };
    persist();
  }

  const api = {
    mode,
    getSession: () => session,
    isAuthed: () => Boolean(session?.token),
    onChange(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },

    signInMock() {
      session = { token: "mock", refreshToken: null, expiresAt: null, email: "demo@verihire.example" };
      persist();
      return session;
    },

    signInDev(email) {
      const e = String(email ?? "").trim().toLowerCase();
      if (!isEmail(e)) throw new AuthError("Enter a valid email address, for example you@example.com.");
      session = { token: `dev:${e}`, refreshToken: null, expiresAt: null, email: e };
      persist();
      return session;
    },

    async signInPassword(email, password) {
      const e = String(email ?? "").trim();
      if (!isEmail(e)) throw new AuthError("Enter a valid email address.");
      if (!password) throw new AuthError("Enter your password.");
      const { res, data } = await post("/token?grant_type=password", { email: e, password });
      if (!res.ok || !data?.access_token) throw new AuthError(friendly(data, "Could not sign in. Try again."));
      setFromTokenResponse(data, e);
      return session;
    },

    /** @returns {Promise<{needsConfirmation: boolean}>} */
    async signUp(email, password) {
      const e = String(email ?? "").trim();
      if (!isEmail(e)) throw new AuthError("Enter a valid email address.");
      if (String(password ?? "").length < 6) throw new AuthError("Use a password with at least 6 characters.");
      const { res, data } = await post("/signup", { email: e, password });
      if (!res.ok) throw new AuthError(friendly(data, "Could not create the account. Try again."));
      if (data?.access_token) {
        setFromTokenResponse(data, e);
        return { needsConfirmation: false };
      }
      return { needsConfirmation: true };
    },

    /** Refresh the access token. Returns true on success; clears the session and returns false otherwise. */
    async refresh() {
      if (mode !== "supabase" || !session?.refreshToken) return false;
      if (!refreshing) {
        refreshing = (async () => {
          try {
            const { res, data } = await post("/token?grant_type=refresh_token", { refresh_token: session.refreshToken });
            if (!res.ok || !data?.access_token) {
              session = null;
              persist();
              return false;
            }
            setFromTokenResponse(data, session?.email);
            return true;
          } catch {
            return false; // network problem: keep the session, caller decides
          } finally {
            refreshing = null;
          }
        })();
      }
      return refreshing;
    },

    /** Token for the Authorization header. Refreshes a token that expires within 60 seconds. */
    async getToken() {
      if (!session?.token) return null;
      if (mode === "supabase" && session.expiresAt && session.expiresAt - now() < 60_000) await api.refresh();
      return session?.token ?? null;
    },

    async signOut() {
      const token = session?.token;
      session = null;
      persist();
      if (mode === "supabase" && token) {
        try {
          await doFetch(`${base()}/logout`, { method: "POST", headers: headers({ Authorization: `Bearer ${token}` }) });
        } catch { /* signing out locally is enough */ }
      }
    },
  };
  return api;
}
