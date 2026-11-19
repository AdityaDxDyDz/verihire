// Candidate dossier: a drawer over the results page. Shows facts, why the score is what it is, and (on request)
// the skill-gap analysis with every claim tied to highlighted resume text.
import { h, mount } from "../dom.js";
import { icon } from "../icons.js";
import { button, chip, skillChip, banner, spinner, scoreBlock } from "../components/ui.js";
import { confirmDialog, trapFocus } from "../components/overlay.js";
import { paper } from "../components/evidence.js";
import { openPdf } from "../components/pdf.js";
import { usableCitations } from "../highlight.js";
import { fmtYears, eduLabel, featureLabel, fmtScore, fmtDuration, displaySkill } from "../format.js";

const STATUS_TEXT = { present: "Shown in work", partial: "Partly shown", missing: "No evidence" };

export function candidateDrawer(ctx, { search, item, onClose }) {
  let alive = true;
  const previous = document.activeElement;
  const st = { pii: null, analysis: null, analysing: null, picked: null, error: null };
  const body = h("div", { class: "drawer-body" });
  const closeBtn = button({ icon: "x", variant: "ghost", title: "Close", onClick: () => onClose?.() });
  const head = h("div", { class: "drawer-head" },
    h("div", { style: { flex: 1, minWidth: 0 } }, h("h2", { id: "dr-title" }, item.label), h("div", { class: "muted small" }, (item.titles ?? []).join(", ") || "Title not stated")),
    h("div", { class: "score-big" }, h("b", null, fmtScore(item.match_score)), h("div", { class: "muted small" }, `Rank ${item.rank} · out of 100`)),
    closeBtn);
  const drawer = h("aside", { class: "drawer", role: "dialog", "aria-modal": "true", "aria-labelledby": "dr-title" }, head, body);
  const backdrop = h("div", { class: "drawer-backdrop", onClick: () => onClose?.() });
  const root = h("div", null, backdrop, drawer);
  const untrap = trapFocus(drawer, () => onClose?.());
  setTimeout(() => closeBtn.focus(), 0);
  // Escape must work even when focus has dropped to <body> (e.g. after a button was replaced), unless a modal is open.
  const onDocKey = (e) => { if (e.key === "Escape" && !document.querySelector("#modal-root .modal")) onClose?.(); };
  document.addEventListener("keydown", onDocKey);

  // ---- identity --------------------------------------------------------------------------------------------
  function identitySection() {
    if (st.pii) {
      const p = st.pii;
      return h("div", { class: "identity" }, icon("lock", { size: 16 }),
        h("dl", null, h("dt", null, "Name"), h("dd", null, p.full_name ?? "Not found"), h("dt", null, "Email"), h("dd", null, p.email ?? "Not found"), h("dt", null, "Phone"), h("dd", null, p.phone ?? "Not found")),
        h("span", { class: "spacer" }), h("span", { class: "muted small" }, "This view was logged."));
    }
    return h("div", { class: "identity" }, icon("shield", { size: 16 }),
      h("div", { style: { flex: 1 } }, h("strong", null, "Identity hidden"), h("div", { class: "muted small" }, "Contact details are revealed only when you ask. Each reveal is recorded.")),
      button({ label: "Reveal identity", icon: "eye", onClick: reveal }));
  }
  async function reveal() {
    const ok = await confirmDialog({ title: "Reveal this candidate's identity?", message: "Their name and contact details will be shown, and the reveal is recorded in the audit log. Do this once you have decided on merit.", confirmLabel: "Reveal identity" });
    if (!ok) return;
    try { const c = await ctx.api.getCandidate(item.candidate_id, true); st.pii = c.pii ?? { full_name: null, email: null, phone: null }; if (alive) draw(); }
    catch (err) { ctx.reportError(err, "Could not reveal the identity."); }
  }

  // ---- score breakdown -------------------------------------------------------------------------------------
  function breakdown() {
    const c = item.explanation?.feature_contributions ?? {};
    const entries = Object.entries(c).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
    if (!entries.length) return null;
    const max = Math.max(...entries.map(([, v]) => Math.abs(v)), 1);
    return h("section", { class: "dsec", "aria-labelledby": "why-h" },
      h("h3", { id: "why-h" }, "Why this score"),
      h("p", { class: "muted small", style: { margin: "0 0 10px" } }, "Every score starts at 50. Bars to the right added points, bars to the left took points away."),
      h("div", { class: "dbars" }, entries.map(([k, v]) => h("div", { class: "dbar" },
        h("span", null, featureLabel(k)),
        h("div", { class: "axis", role: "presentation" }, h("div", { class: `fill ${v >= 0 ? "pos" : "neg"}`, style: { width: `${(Math.abs(v) / max) * 50}%` } })),
        h("span", { class: `val ${v >= 0 ? "pos" : "neg"}` }, `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(1)}`)))));
  }

  // ---- analysis --------------------------------------------------------------------------------------------
  async function analyse() {
    if (st.analysing) return;
    const started = Date.now();
    st.analysing = { started };
    st.error = null;
    draw();
    const tick = setInterval(() => { if (alive && st.analysing) { const el = body.querySelector("[data-elapsed]"); if (el) el.textContent = fmtDuration(Date.now() - started); } }, 500);
    try {
      st.analysis = await ctx.api.analyze({ jd_id: search.jd_id, candidate_id: item.candidate_id, search_id: search.search_id });
      st.picked = st.analysis.result.skills[0]?.skill ?? null;
    } catch (err) {
      st.error = err.status === 429 ? `The AI service is busy. Try again in ${err.retryAfter ?? 30} seconds.` : err.status === 0 ? "Could not reach the server. Check your connection and try again." : (err.message || "The analysis failed.");
    }
    clearInterval(tick);
    st.analysing = null;
    if (alive) draw();
  }

  function analysisSection() {
    if (st.analysing) return h("div", { class: "analyse-cta", role: "status" }, spinner(), h("p", null, "Reading the resume against each requirement. This usually takes 10 to 30 seconds. ", h("span", { "data-elapsed": "" }, "0 s")));
    if (!st.analysis) {
      return h("div", null,
        st.error ? banner("bad", st.error) : null,
        h("div", { class: "analyse-cta" }, icon("sparkle", { size: 20 }),
          h("p", null, h("strong", null, "Check the fit in detail. "), "An AI reads the resume and shows, for each required skill, the exact sentence that proves it. Anything it cannot quote is dropped."),
          button({ label: st.error ? "Try again" : "Analyse fit", variant: "primary", icon: "sparkle", onClick: analyse })));
    }
    const { result, citations_resolved: cites } = st.analysis;
    const { ok, rejected } = usableCitations(cites);
    const skills = result.skills ?? [];
    const cur = skills.find((s) => s.skill === st.picked) ?? skills[0];
    const mine = ok.filter((c) => c.skill === cur?.skill);
    const byChunk = new Map();
    for (const c of mine) { if (!byChunk.has(c.chunk_id)) byChunk.set(c.chunk_id, { c, ranges: [] }); byChunk.get(c.chunk_id).ranges.push({ start: c.start, end: c.end }); }
    const claimed = (cur?.citations?.length ?? 0);
    return h("div", { class: "stack" },
      h("p", { style: { margin: 0, fontWeight: 600 } }, result.summary),
      h("div", { class: "verified" }, icon("check", { size: 14 }), `${ok.length} quoted ${ok.length === 1 ? "passage" : "passages"} checked against the resume`, result.dropped_unverified_claims ? ` · ${result.dropped_unverified_claims} unverifiable ${result.dropped_unverified_claims === 1 ? "claim" : "claims"} removed` : ""),
      rejected.length ? banner("warn", `${rejected.length} quoted ${rejected.length === 1 ? "passage" : "passages"} could not be matched to the resume text and ${rejected.length === 1 ? "is" : "are"} not shown.`) : null,
      h("div", { class: "gap" },
        h("div", { class: "gap-rail", role: "tablist", "aria-label": "Skills" }, skills.map((s) =>
          h("button", { type: "button", role: "tab", "aria-selected": String(s.skill === cur?.skill), "aria-current": String(s.skill === cur?.skill), onClick: () => { st.picked = s.skill; draw(); } }, displaySkill(s.skill), h("span", { class: `st ${s.status}` }, STATUS_TEXT[s.status] ?? s.status)))),
        h("div", { class: "gap-main", role: "tabpanel" },
          cur ? [h("div", { class: "row" }, h("strong", null, displaySkill(cur.skill)), h("span", { class: `chip ${cur.status === "present" ? "ok" : cur.status === "partial" ? "warn" : "bad"}` }, STATUS_TEXT[cur.status])),
            cur.reasoning ? h("div", { class: "reason" }, cur.reasoning) : null,
            byChunk.size ? [...byChunk.values()].map(({ c, ranges }) => paper({ text: c.text_scrubbed, ranges, page: c.page_no, section: c.section, sweep: true, onOpenPdf: (p) => openPdf(ctx, item.document_id, { title: item.label, page: p }) }))
              : h("div", { class: "muted small" }, cur.status === "missing" ? "No mention of this skill was found in the resume." : claimed ? "The quoted passages could not be verified." : "No supporting text.")] : null)),
      h("div", { class: "lists" },
        h("div", null, h("h3", null, "Strengths"), result.strengths?.length ? h("ul", null, result.strengths.map((s) => h("li", null, s))) : h("p", { class: "muted small" }, "None listed.")),
        h("div", null, h("h3", null, "Risks"), result.risks?.length ? h("ul", null, result.risks.map((s) => h("li", null, s))) : h("p", { class: "muted small" }, "None found."))),
      result.interview_questions?.length ? h("div", null, h("h3", null, "Questions to ask"), h("ol", { style: { paddingLeft: "1.2em", display: "grid", gap: "6px", fontSize: "0.875rem" } }, result.interview_questions.map((q) => h("li", null, q)))) : null,
      h("div", { class: "muted small" }, `Model ${result.model} · prompt ${result.prompt_version}${st.analysis.from_cache ? " · cached result" : ""}`));
  }

  function draw() {
    const req = item.matched_required.length + item.missing_required.length;
    mount(body,
      item.suspicious_hidden_text ? banner("warn", h("strong", null, "Hidden text detected. "), "This PDF contains text a person cannot see, such as white or tiny text. It may be an attempt to game the ranking. Check the original.") : null,
      identitySection(),
      h("div", { class: "facts" },
        h("div", { class: "fact" }, h("div", { class: "k" }, "Experience"), h("div", { class: "v" }, item.years_experience == null ? "–" : fmtYears(item.years_experience))),
        h("div", { class: "fact" }, h("div", { class: "k" }, "Education"), h("div", { class: "v" }, item.education_level ? eduLabel(item.education_level) : "Not stated")),
        h("div", { class: "fact" }, h("div", { class: "k" }, "Required skills"), h("div", { class: "v" }, req ? `${item.matched_required.length} of ${req}` : "–"))),
      h("section", { class: "dsec" }, h("h3", null, "Skills"),
        h("div", { class: "chip-list" }, item.matched_required.map((s) => skillChip(s, "match")), item.missing_required.map((s) => skillChip(s, "miss")), item.matched_preferred.map((s) => skillChip(s, "match")), item.missing_preferred.map((s) => h("span", { class: "chip outline", title: "Preferred, not found" }, displaySkill(s), " (preferred)")))),
      breakdown(),
      h("section", { class: "dsec", "aria-labelledby": "fit-h" }, h("h3", { id: "fit-h" }, "Skill-gap analysis"), analysisSection()),
      h("div", null, button({ label: "View original PDF", icon: "external", onClick: () => openPdf(ctx, item.document_id, { title: item.label }) })));
  }

  draw();
  return { el: root, destroy() { alive = false; untrap(); document.removeEventListener("keydown", onDocKey); if (previous && document.contains(previous)) previous.focus?.(); } };
}
