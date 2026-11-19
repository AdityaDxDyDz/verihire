// Homepage (#/home, also the default route). Signed out: what VeriHire does and how to start.
// Signed in: a small dashboard with the library counts, recent job descriptions and interview replies.
import { h, mount } from "../dom.js";
import { icon, logoMark } from "../icons.js";
import { button, linkButton, chip, skeletonRows } from "../components/ui.js";
import { buildHash } from "../router.js";
import { fmtDate, plural } from "../format.js";

const STEPS = [
  ["upload", "Upload resumes", "Drop in PDF resumes. Each one is read, checked for hidden text, and stripped of names, e-mails and phone numbers."],
  ["file", "Add the job", "Paste or upload a job description. VeriHire pulls out the required and preferred skills, experience and education."],
  ["search", "Rank candidates", "Hybrid search plus a trained reranker orders the pool, with a bar chart showing why each person ranks where they do."],
  ["sparkle", "Check the evidence", "Every skill claim links to the exact sentence in the resume. Quotes that cannot be found are dropped, not shown."],
];
const FEATURES = [
  ["shield", "Blind by default", "Names and contact details stay hidden until you choose to reveal them, and every reveal is audited."],
  ["check", "Verified citations", "Skill-gap analysis quotes the resume verbatim, and the backend re-checks every quote."],
  ["compare", "Side-by-side compare", "Put two to four candidates next to each other with their matched and missing skills."],
  ["eye", "Interview scheduling", "E-mail your shortlist a private link and see their preferred interview times in one table."],
  ["download", "CSV export", "Download the ranked list for your ATS or a spreadsheet."],
  ["alert", "Bad-file detection", "Corrupt, encrypted, scanned and hidden-text PDFs are flagged instead of silently ranked."],
];

function stat(label, value, href, tone = "") {
  return h("a", { class: `home-stat ${tone}`.trim(), href }, h("b", null, String(value)), h("span", null, label));
}

export function homeView(ctx) {
  const authed = ctx.auth.isAuthed();
  const dash = h("div", null, authed ? skeletonRows(3, 64) : null);
  let alive = true;

  const hero = h("section", { class: "home-hero" },
    h("div", { class: "home-hero-text" },
      h("div", { class: "brand", style: { pointerEvents: "none" } }, logoMark(34), h("span", { class: "brand-word" }, "VeriHire")),
      h("h1", null, "Shortlist with evidence you can check."),
      h("p", { class: "lead" }, "VeriHire ranks resumes against a job description using semantic and keyword search together, and links each skill it credits to the line in the resume that mentions it."),
      h("div", { class: "row" },
        authed
          ? [linkButton({ label: "Open the library", icon: "upload", variant: "primary", href: buildHash("library") }), linkButton({ label: "New job search", icon: "search", href: buildHash("jd") })]
          : [linkButton({ label: "Sign in to start", variant: "primary", href: buildHash("login") }), h("a", { class: "btn", href: "#how" }, "How it works")])),
    h("ol", { class: "home-steps", id: "how", "aria-label": "How it works" },
      STEPS.map(([ic, title, body], i) => h("li", { class: "sheet pad" },
        h("div", { class: "home-step-no" }, icon(ic, { size: 18 }), h("span", null, `Step ${i + 1}`)),
        h("h3", null, title), h("p", { class: "muted small" }, body)))));

  const features = h("section", { "aria-labelledby": "feat-h", class: "home-section" },
    h("h2", { id: "feat-h" }, "What it does"),
    h("div", { class: "home-features" }, FEATURES.map(([ic, title, body]) => h("div", { class: "home-feature" },
      h("span", { class: "home-ic" }, icon(ic, { size: 18 })), h("div", null, h("h3", null, title), h("p", { class: "muted small" }, body))))));

  async function loadDash() {
    const [docs, jds, invites] = await Promise.allSettled([
      ctx.api.listDocuments({ page: 1, page_size: 1 }),
      ctx.api.listJDs(),
      ctx.api.listInterviewRequests({}),
    ]);
    if (!alive) return;
    const c = docs.status === "fulfilled" ? docs.value.counts ?? {} : {};
    const jdList = jds.status === "fulfilled" ? jds.value : [];
    const inv = invites.status === "fulfilled" ? invites.value.items ?? [] : [];
    const replied = inv.filter((i) => i.reply);
    const recent = [...jdList].sort((a, b) => String(b.updated_at ?? b.created_at).localeCompare(String(a.updated_at ?? a.created_at))).slice(0, 5);
    mount(dash, h("section", { class: "home-section", "aria-labelledby": "dash-h" },
      h("div", { class: "page-head", style: { marginBottom: "12px" } },
        h("div", null, h("h2", { id: "dash-h" }, "Your workspace"), ctx.auth.getSession()?.email ? h("p", { class: "sub" }, `Signed in as ${ctx.auth.getSession().email}`) : null)),
      h("div", { class: "home-stats" },
        stat("resumes ready", c.ready ?? 0, buildHash("library")),
        stat("processing", c.processing ?? 0, buildHash("library")),
        stat("need attention", (c.failed ?? 0) + (c.needs_ocr ?? 0), buildHash("library"), (c.failed ?? 0) + (c.needs_ocr ?? 0) ? "warn" : ""),
        stat("job descriptions", jdList.length, buildHash("jd")),
        stat("interview replies", replied.length, recent[0] ? buildHash("interviews", { id: (replied[0] ?? inv[0])?.jd_id ?? recent[0].id }) : buildHash("jd"), replied.length ? "ok" : "")),
      h("div", { class: "home-cols" },
        h("div", { class: "sheet pad stack" },
          h("h3", null, "Recent job descriptions"),
          recent.length
            ? h("ul", { class: "home-list" }, recent.map((j) => h("li", null,
              h("div", null, h("a", { href: buildHash("jdEdit", { id: j.id }) }, j.title || "Untitled job"), h("div", { class: "muted small" }, `${plural((j.required_skills ?? []).length, "required skill")} · ${fmtDate(j.updated_at ?? j.created_at)}`)),
              h("div", { class: "row", style: { gap: "6px" } },
                j.last_search_id ? linkButton({ label: "Results", size: "sm", href: buildHash("results", { id: j.last_search_id }) }) : chip("Not searched yet", "dim"),
                linkButton({ label: "Interviews", size: "sm", href: buildHash("interviews", { id: j.id }) })))))
            : h("p", { class: "muted small" }, "No job descriptions yet.", " ", h("a", { href: buildHash("jd") }, "Add one"), ".")),
        h("div", { class: "sheet pad stack" },
          h("h3", null, "Latest interview replies"),
          replied.length
            ? h("ul", { class: "home-list" }, replied.slice(0, 5).map((r) => h("li", null,
              h("div", null, h("b", null, r.label), h("div", { class: "muted small" }, `${plural(r.reply.slots.length, "time option")} · ${r.reply.timezone}`)),
              linkButton({ label: "View", size: "sm", href: buildHash("interviews", { id: r.jd_id }) }))))
            : h("p", { class: "muted small" }, "No replies yet. Tick candidates on a results page and choose Request interview times."))),
      (c.ready ?? 0) === 0 ? h("div", { class: "banner info", role: "status", style: { marginTop: "16px" } }, icon("info", { size: 18 }), h("div", null, "Start by uploading a few resumes in the ", h("a", { href: buildHash("library") }, "library"), ".")) : null));
  }

  const el = h("div", { class: "home" }, hero, authed ? dash : null, features,
    authed ? null : h("section", { class: "home-cta sheet pad" }, h("h2", null, "Ready to try it?"), h("p", { class: "muted" }, "Sign in and upload a few resumes. Fictional sample resumes work well for a first run."), button({ label: "Sign in", variant: "primary", onClick: () => ctx.navigate("login") })));
  if (authed) loadDash().catch(() => alive && mount(dash));
  return { el, title: "Home", destroy() { alive = false; } };
}
