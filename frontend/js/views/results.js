import { h, mount, downloadBlob } from "../dom.js";
import { button, linkButton, chip, skillChip, scoreBlock, banner, emptyState, errorState, skeletonRows } from "../components/ui.js";
import { fmtYears, eduLabel, fmtDate, plural, sectionLabel, displaySkill } from "../format.js";
import { buildHash } from "../router.js";
import { candidateDrawer } from "./candidate.js";

const SORTS = {
  score: { label: "Match score", fn: (a, b) => b.match_score - a.match_score },
  years: { label: "Years of experience", fn: (a, b) => (b.years_experience ?? -1) - (a.years_experience ?? -1) },
  coverage: { label: "Required skills met", fn: (a, b) => cover(b) - cover(a) || b.match_score - a.match_score },
};
const cover = (r) => { const n = r.matched_required.length + r.missing_required.length; return n ? r.matched_required.length / n : 1; };

export function resultsView(ctx, route) {
  const searchId = route.params.id;
  let alive = true;
  const st = { data: null, error: null, sort: "score", onlyFull: false, selected: new Set(), feedback: new Map(), drawer: null, drawerFor: null };
  const body = h("div", null, skeletonRows(6, 56));
  const drawerHost = h("div");
  const el = h("div", null, body, drawerHost);

  async function load() {
    mount(body, skeletonRows(6, 56));
    try {
      st.data = ctx.cache.get(`search:${searchId}`) ?? (await ctx.api.getSearch(searchId));
      ctx.cache.set(`search:${searchId}`, st.data);
      if (!alive) return;
      draw();
      syncDrawer(route);
    } catch (err) {
      if (!alive) return;
      mount(body, err.status === 404 ? emptyState({ title: "That search was not found", body: "Run a new search from a job description.", action: linkButton({ label: "Go to job description", href: buildHash("jd"), variant: "primary" }) }) : errorState(err, load));
    }
  }

  function rows() {
    let list = [...st.data.results];
    if (st.onlyFull) list = list.filter((r) => r.missing_required.length === 0);
    return list.sort(SORTS[st.sort].fn);
  }

  async function rate(item, rating, btns) {
    const prev = st.feedback.get(item.candidate_id);
    const next = prev === rating ? null : rating;
    if (next === null) { st.feedback.delete(item.candidate_id); draw(); return; }
    try {
      await ctx.api.feedback({ search_id: st.data.search_id, candidate_id: item.candidate_id, rating: next });
      st.feedback.set(item.candidate_id, next);
      ctx.toast.show("Thanks. Feedback saved.", { tone: "ok", timeout: 2500 });
      draw();
    } catch (err) { ctx.reportError(err, "Could not save feedback."); }
  }

  function row(r) {
    const picked = st.selected.has(r.candidate_id);
    const fb = st.feedback.get(r.candidate_id);
    const required = r.matched_required.length + r.missing_required.length;
    const snippet = r.top_chunks?.[0];
    return h("tr", { class: picked ? "selected" : "" },
      h("td", null, h("input", { type: "checkbox", checked: picked, "aria-label": `Select ${r.label} to compare`, disabled: !picked && st.selected.size >= 4, onChange: (e) => { e.target.checked ? st.selected.add(r.candidate_id) : st.selected.delete(r.candidate_id); draw(); } })),
      h("td", { class: "rank-no" }, r.rank),
      h("td", { class: "cand-cell" },
        h("a", { class: "cand-link", href: buildHash("candidate", { id: st.data.search_id, cid: r.candidate_id }) }, r.label),
        h("div", { class: "sub" }, (r.titles ?? []).slice(0, 1).join(", ") || "Title not stated"),
        r.suspicious_hidden_text ? h("div", { style: { marginTop: "4px" } }, chip("Hidden text found", "warn", { iconName: "alert", title: "This PDF contains hidden text, such as white or tiny text. Review the original." })) : null),
      h("td", null, scoreBlock(r.match_score)),
      h("td", { class: "cover" },
        h("span", { class: "count" }, required ? `${r.matched_required.length} of ${required} required` : "No required skills set"),
        h("div", { class: "chip-list" }, r.matched_required.map((s) => skillChip(s, "match")), r.missing_required.map((s) => skillChip(s, "miss")))),
      h("td", { class: "nowrap" }, r.years_experience == null ? h("span", { class: "muted" }, "–") : fmtYears(r.years_experience), h("div", { class: "sub muted small" }, r.education_level ? eduLabel(r.education_level) : "")),
      h("td", null, snippet ? h("div", { class: "snippet", title: snippet.text_scrubbed }, snippet.text_scrubbed.replace(/\[(NAME|EMAIL|PHONE|URL|ADDRESS)\]/g, "▮▮▮")) : null),
      h("td", null, h("div", { class: "fb" },
        button({ icon: "thumbUp", size: "sm", variant: "ghost", title: `Good match: ${r.label}`, pressed: fb === 1, onClick: () => rate(r, 1) }),
        button({ icon: "thumbDown", size: "sm", variant: "ghost", title: `Poor match: ${r.label}`, pressed: fb === -1, onClick: () => rate(r, -1) }))));
  }

  async function exportCsv() {
    try { downloadBlob(await ctx.api.exportCsv(st.data.search_id), `verihire-${st.data.search_id.slice(0, 8)}.csv`); }
    catch (err) { ctx.reportError(err, "Could not export the CSV."); }
  }

  function draw() {
    const d = st.data;
    const spec = d.job_spec;
    const list = rows();
    const t = d.timings ?? {};
    const parts = [
      h("div", { class: "page-head" },
        h("div", null,
          h("h1", null, spec?.title || "Ranked candidates"),
          h("div", { class: "run-meta" },
            h("span", null, h("b", null, plural(d.results.length, "candidate")), " ranked"),
            d.created_at ? h("span", null, fmtDate(d.created_at)) : null,
            t.total_ms != null ? h("span", null, `${Math.round(t.total_ms)} ms`) : null,
            h("span", null, d.blind ? "Blind: names hidden" : "Names shown"))),
        h("div", { class: "page-actions" },
          d.jd_id ? linkButton({ label: "Edit requirements", icon: "refresh", href: buildHash("jdEdit", { id: d.jd_id }) }) : null,
          button({ label: "Export CSV", icon: "download", onClick: exportCsv }),
          linkButton({ label: `Compare${st.selected.size ? ` (${st.selected.size})` : ""}`, icon: "compare", variant: "primary", href: st.selected.size >= 2 ? buildHash("compare", { id: d.search_id }, { ids: [...st.selected].join(",") }) : "#", title: st.selected.size >= 2 ? "Compare selected candidates" : "Select 2 to 4 candidates to compare" }))),
    ];
    for (const w of d.warnings ?? []) parts.push(banner("warn", w));
    if (d.embedder?.startsWith("stub") || d.reranker_version?.startsWith("stub")) parts.push(banner("info", "Ranking is running on placeholder models, so order may not be meaningful yet."));
    parts.push(h("p", { class: "muted small", style: { margin: "0 0 12px" } }, "Match score is a relative ranking out of 100, not a probability of being hired. Use it to decide who to read first."));
    parts.push(h("div", { class: "results-bar" },
      h("label", { class: "small", for: "sort" }, "Sort by"),
      h("select", { class: "select", id: "sort", style: { width: "auto" }, onChange: (e) => { st.sort = e.target.value; draw(); } }, Object.entries(SORTS).map(([k, v]) => h("option", { value: k, selected: st.sort === k }, v.label))),
      h("label", { class: "switch" }, h("input", { type: "checkbox", checked: st.onlyFull, onChange: (e) => { st.onlyFull = e.target.checked; draw(); } }), h("span", { class: "track", "aria-hidden": "true" }), "Only those with every required skill")));
    parts.push(list.length ? h("div", { class: "table-wrap" }, h("table", { class: "dt" },
      h("thead", null, h("tr", null, [["", "Compare"], ["#", "Rank"], ["Candidate"], ["Score"], ["Required skills"], ["Experience"], ["Best passage"], ["Rate"]].map(([t, sr]) => h("th", { scope: "col" }, sr ? h("span", { class: "sr-only" }, sr) : null, sr ? null : t, sr && t ? h("span", { "aria-hidden": "true" }, t) : null)))),
      h("tbody", null, list.map(row)))) : emptyState({ title: d.results.length ? "No one has every required skill" : "No candidates matched", body: d.results.length ? "Turn off the filter to see everyone." : "Upload more resumes or loosen the filters on the job description page.", action: d.results.length ? button({ label: "Show everyone", onClick: () => { st.onlyFull = false; draw(); } }) : linkButton({ label: "Back to job description", href: buildHash("jdEdit", { id: d.jd_id ?? "x" }) }) }));
    mount(body, parts);
  }

  // The candidate route renders a drawer over this same page, so the list keeps its scroll and selection.
  function syncDrawer(r) {
    if (!st.data) return;
    if (r.name !== "candidate") { closeDrawer(); return; }
    if (st.drawerFor === r.params.cid) return;
    closeDrawer();
    const item = st.data.results.find((x) => x.candidate_id === r.params.cid);
    if (!item) { ctx.toast.show("That candidate is not part of this search.", { tone: "warn" }); ctx.navigate("results", { id: searchId }); return; }
    st.drawerFor = r.params.cid;
    st.drawer = candidateDrawer(ctx, { search: st.data, item, onClose: () => ctx.navigate("results", { id: searchId }) });
    mount(drawerHost, st.drawer.el);
  }
  function closeDrawer() { st.drawer?.destroy(); st.drawer = null; st.drawerFor = null; mount(drawerHost); }

  load();
  return {
    el, title: "Results",
    update(r) { route = r; syncDrawer(r); },
    destroy() { alive = false; st.drawer?.destroy(); },
  };
}
