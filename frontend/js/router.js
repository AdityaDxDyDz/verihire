// Tiny hash router. parseHash/buildHash are pure (unit-tested); Router wires them to window.location.

const ROUTES = [
  { name: "login", pattern: "/login" },
  { name: "library", pattern: "/library" },
  { name: "jd", pattern: "/jd" },
  { name: "jdEdit", pattern: "/jd/:id" },
  { name: "results", pattern: "/results/:id" },
  { name: "candidate", pattern: "/results/:id/:cid" },
  { name: "compare", pattern: "/compare/:id" },
  { name: "interviews", pattern: "/interviews/:id" }, // recruiter: invitations sent for one job description
  { name: "interview", pattern: "/interview/:token" }, // candidate: public, no login
];

export const DEFAULT_PATH = "/library";

function splitPath(path) {
  return path.split("/").filter(Boolean);
}

export function parseHash(hash) {
  let raw = String(hash ?? "").replace(/^#/, "");
  if (!raw.startsWith("/")) raw = raw ? `/${raw}` : DEFAULT_PATH;
  const [pathPart, queryPart = ""] = raw.split("?");
  let segs = splitPath(pathPart);
  if (segs.length === 0) segs = splitPath(DEFAULT_PATH); // "#/" and "#/?x=1" mean "home"
  const query = Object.fromEntries(new URLSearchParams(queryPart));
  for (const route of ROUTES) {
    const pat = splitPath(route.pattern);
    if (pat.length !== segs.length) continue;
    const params = {};
    let ok = true;
    for (let i = 0; i < pat.length; i++) {
      if (pat[i].startsWith(":")) {
        try {
          params[pat[i].slice(1)] = decodeURIComponent(segs[i]);
        } catch {
          ok = false;
        }
      } else if (pat[i] !== segs[i]) ok = false;
      if (!ok) break;
    }
    if (ok) return { name: route.name, params, query, path: `/${segs.join("/")}` };
  }
  return { name: "notfound", params: {}, query, path: `/${segs.join("/")}` };
}

export function buildHash(name, params = {}, query = {}) {
  const route = ROUTES.find((r) => r.name === name);
  if (!route) throw new Error(`Unknown route: ${name}`);
  const path = splitPath(route.pattern)
    .map((seg) => {
      if (!seg.startsWith(":")) return seg;
      const v = params[seg.slice(1)];
      if (v === undefined || v === null || v === "") throw new Error(`Missing param ${seg} for route ${name}`);
      return encodeURIComponent(String(v));
    })
    .join("/");
  const qs = new URLSearchParams(Object.entries(query).filter(([, v]) => v !== undefined && v !== null && v !== ""));
  const q = qs.toString();
  return `#/${path}${q ? `?${q}` : ""}`;
}

export class Router {
  constructor(onRoute) {
    this.onRoute = onRoute;
    this._handler = () => this.onRoute(parseHash(window.location.hash));
  }
  start() {
    window.addEventListener("hashchange", this._handler);
    this._handler();
  }
  stop() {
    window.removeEventListener("hashchange", this._handler);
  }
  navigate(name, params, query) {
    const next = buildHash(name, params, query);
    if (window.location.hash === next) this._handler();
    else window.location.hash = next;
  }
}
