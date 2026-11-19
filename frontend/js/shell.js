// Top bar: brand, navigation, backend status chip, signed-in email, theme toggle, sign out.
import { h, mount } from "./dom.js";
import { icon, logoMark } from "./icons.js";
import { button } from "./components/ui.js";
import { buildHash } from "./router.js";

const NAV = [["library", "Library", "library"], ["jd", "Job & search", "jd"], ["results", "Results", null]];

export function createShell(root, { auth, config, mock, onSignOut, onTheme }) {
  const nav = h("nav", { class: "nav", "aria-label": "Main" });
  const tools = h("div", { class: "topbar-tools" });
  const bar = h("header", { class: "topbar" }, h("div", { class: "topbar-inner" },
    h("a", { class: "brand", href: buildHash("library"), "aria-label": "VeriHire home" }, logoMark(26), h("span", { class: "brand-word" }, "VeriHire")), nav, tools));
  mount(root, bar);
  let status = mock ? { tone: "warn", text: "Demo data" } : null;

  function draw(route, lastResults) {
    const authed = auth.isAuthed();
    const active = route?.name === "candidate" ? "results" : route?.name === "jdEdit" ? "jd" : route?.name === "compare" ? "results" : route?.name;
    mount(nav, authed ? NAV.filter(([k]) => k !== "results" || lastResults).map(([key, label]) =>
      h("a", { href: key === "results" ? buildHash("results", { id: lastResults }) : buildHash(key), "aria-current": active === key ? "page" : null }, h("span", null, label))) : null);
    mount(tools,
      status ? h("span", { class: `chip ${status.tone}`, title: mock ? "Running on built-in sample data. No server is involved." : "Backend status" }, status.text) : null,
      authed && auth.getSession()?.email ? h("span", { class: "user-email" }, auth.getSession().email) : null,
      button({ icon: "moon", variant: "ghost", title: "Switch light or dark theme", onClick: onTheme }),
      authed ? button({ icon: "logout", variant: "ghost", title: "Sign out", onClick: onSignOut }) : null);
  }
  return { draw, setStatus(s) { status = s; } };
}
