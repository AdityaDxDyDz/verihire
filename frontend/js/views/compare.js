import { h, mount } from "../dom.js";
import { button, linkButton, skillChip, scoreBlock, emptyState, errorState, skeletonRows } from "../components/ui.js";
import { fmtYears, eduLabel, displaySkill } from "../format.js";
import { buildHash } from "../router.js";

export function compareView(ctx, route) {
  const searchId = route.params.id;
  const ids = (route.query.ids ?? "").split(",").filter(Boolean).slice(0, 4);
  const body = h("div", null, skeletonRows(5));
  const el = h("div", null, body);
  let alive = true;
  const back = linkButton({ label: "Back to results", icon: "chevronLeft", href: buildHash("results", { id: searchId }) });

  async function load() {
    try {
      const data = ctx.cache.get(`search:${searchId}`) ?? (await ctx.api.getSearch(searchId));
      ctx.cache.set(`search:${searchId}`, data);
      if (!alive) return;
      const picked = ids.map((id) => data.results.find((r) => r.candidate_id === id)).filter(Boolean);
      if (picked.length < 2) return mount(body, emptyState({ title: "Pick at least two candidates", body: "Tick the boxes beside two to four candidates on the results page.", action: back }));
      const skills = [...new Set([...(data.job_spec?.required_skills ?? []), ...(data.job_spec?.preferred_skills ?? [])])];
      const req = new Set(data.job_spec?.required_skills ?? []);
      const has = (r, s) => r.matched_required.includes(s) || r.matched_preferred.includes(s);
      const top = Math.max(...picked.map((r) => r.match_score));
      mount(body,
        h("div", { class: "page-head" }, h("div", null, h("h1", null, "Side by side"), h("p", { class: "sub" }, `${data.job_spec?.title ?? "Role"}. Ticks show skills found in the resume.`)), h("div", { class: "page-actions" }, back)),
        h("div", { class: "table-wrap" }, h("table", { class: "dt cmp" },
          h("thead", null, h("tr", null, h("th", { scope: "col" }, h("span", { class: "sr-only" }, "Attribute")), picked.map((r) => h("th", { scope: "col" }, h("a", { class: "cand-link", href: buildHash("candidate", { id: searchId, cid: r.candidate_id }) }, r.label), h("div", { class: "sub muted small" }, `Rank ${r.rank}`))))),
          h("tbody", null,
            h("tr", null, h("th", { scope: "row" }, "Match score"), picked.map((r) => h("td", null, scoreBlock(r.match_score), r.match_score === top ? h("div", { class: "verified" }, "Highest") : null))),
            h("tr", null, h("th", { scope: "row" }, "Experience"), picked.map((r) => h("td", null, r.years_experience == null ? "–" : fmtYears(r.years_experience)))),
            h("tr", null, h("th", { scope: "row" }, "Education"), picked.map((r) => h("td", null, r.education_level ? eduLabel(r.education_level) : "Not stated"))),
            h("tr", null, h("th", { scope: "row" }, "Latest title"), picked.map((r) => h("td", null, r.titles?.[0] ?? "–"))),
            skills.map((s) => h("tr", null, h("th", { scope: "row" }, displaySkill(s), req.has(s) ? "" : h("span", { class: "muted small" }, " (preferred)")), picked.map((r) => h("td", null, skillChip(s, has(r, s) ? "match" : "miss")))))))));
    } catch (err) { if (alive) mount(body, errorState(err, load)); }
  }
  load();
  return { el, title: "Compare", destroy() { alive = false; } };
}
