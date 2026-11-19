import { h, mount } from "../dom.js";
import { button, field, banner, emptyState, errorState, skeletonRows, switchControl, spinner, skillChip } from "../components/ui.js";
import { chipInput } from "../components/chipinput.js";
import { jdTextProblem, sameJson, JD_MAX_CHARS } from "../validate.js";
import { EDU_LABELS, fmtDate, displaySkill } from "../format.js";
import { buildHash } from "../router.js";
import { icon } from "../icons.js";

const eduSelect = (id, value) => {
  const s = h("select", { class: "select", id }, EDU_LABELS.map((l, i) => h("option", { value: String(i), selected: Number(value ?? 0) === i }, i === 0 ? "No requirement" : l)));
  return s;
};

export function jdView(ctx, route) {
  const id = route.params.id ?? null;
  let alive = true;
  const st = { list: null, listError: null, jd: null, error: null, busy: false, searching: false, problem: null };
  const listHost = h("div", { class: "sheet jd-list" });
  const mainHost = h("div");
  const el = h("div", null,
    h("div", { class: "page-head" }, h("div", null, h("h1", null, "Job description"), h("p", { class: "sub" }, "Upload a PDF, DOCX, or TXT job description, or paste its text. We extract the role, skills, experience, and education for you to review."))),
    h("div", { class: "jd-layout" }, h("div", null, h("h2", { class: "sr-only" }, "Saved job descriptions"), listHost), mainHost));

  function drawList() {
    if (st.listError) return mount(listHost, h("div", { class: "pad", style: { padding: "16px" } }, errorState(st.listError, loadList)));
    if (!st.list) return mount(listHost, h("div", { style: { padding: "12px" } }, skeletonRows(3, 40)));
    mount(listHost,
      h("a", { class: "item", href: buildHash("jd"), "aria-current": String(!id) }, h("div", { class: "t" }, "+ New job description"), h("div", { class: "m" }, "Paste text to start")),
      st.list.length > 5 ? h("div", { class: "jd-filter" }, h("input", { class: "input", type: "search", placeholder: "Filter by title", "aria-label": "Filter job descriptions by title", value: st.q ?? "", onInput: (e) => { st.q = e.target.value; drawList(); listHost.querySelector(".jd-filter input")?.focus(); } })) : null,
      st.list.filter((j) => !st.q || (j.title || "").toLowerCase().includes(st.q.toLowerCase())).map((j) => h("div", { class: "jd-row", "aria-current": String(j.id === id) },
        h("a", { class: "item", href: buildHash("jdEdit", { id: j.id }), "aria-current": String(j.id === id) },
          h("div", { class: "t" }, j.title || "Untitled role"),
          h("div", { class: "m" }, `${(j.required_skills ?? j.spec?.required_skills ?? []).length} required skills · Updated ${fmtDate(j.updated_at)}`)),
        h("div", { class: "jd-row-actions" },
          j.last_search_id ? h("a", { class: "btn ghost icon sm", href: buildHash("results", { id: j.last_search_id }), title: `Latest results for ${j.title || "this role"}`, "aria-label": `Latest results for ${j.title || "this role"}` }, icon("search", { size: 14 })) : null,
          button({ icon: "trash", size: "sm", variant: "ghost", title: `Delete ${j.title || "this job description"}`, onClick: () => removeJd(j) })))));
  }
  async function loadList() {
    try { st.list = await ctx.api.listJDs(); st.listError = null; } catch (e) { st.listError = e; }
    if (alive) drawList();
  }

  async function removeJd(jd) {
    const ok = await ctx.confirm({ title: "Delete this job description?", message: `"${jd.title || "Untitled role"}" will be removed together with its skill-gap analyses and interview invitations. Candidates who were invited can no longer use their link. Past search results stay available.`, confirmLabel: "Delete job description", danger: true });
    if (!ok) return;
    try {
      await ctx.api.deleteJD(jd.id);
      ctx.toast.show("Job description deleted.", { tone: "ok" });
      for (const [k, v] of ctx.cache) if (v?.jd_id === jd.id) ctx.cache.delete(k); // stale searches of that JD
      if (jd.id === id) { ctx.navigate("jd"); return; }
      st.list = st.list?.filter((x) => x.id !== jd.id) ?? null;
      drawList();
    } catch (err) { ctx.reportError(err, "Could not delete that job description."); }
  }

  // ---- composer (new JD) ---------------------------------------------------------------------------------
  function drawComposer() {
    const title = h("input", { class: "input", id: "jd-title", placeholder: "e.g. Backend Engineer", maxlength: "120" });
    const file = h("input", { class: "input", id: "jd-file", type: "file", accept: ".pdf,.docx,.txt,application/pdf,text/plain,application/vnd.openxmlformats-officedocument.wordprocessingml.document" });
    const text = h("textarea", { class: "textarea", id: "jd-text", placeholder: "Paste the full job description here…", style: { minHeight: "300px" } });
    const msg = h("div", { "aria-live": "polite" });
    const go = button({ label: "Extract requirements", icon: "sparkle", variant: "primary", type: "submit" });
    const form = h("form", { class: "sheet pad form-grid", novalidate: true, onSubmit: async (e) => {
      e.preventDefault();
      const selectedFile = file.files?.[0] ?? null;
      if (!selectedFile) {
        const problem = jdTextProblem(text.value);
        if (problem) { mount(msg, banner("warn", problem)); text.focus(); return; }
      }
      go.disabled = true; mount(msg, h("span", { class: "muted" }, spinner(), " Reading the job description…"));
      try {
        const jd = selectedFile
          ? await ctx.api.uploadJD(selectedFile, title.value.trim())
          : await ctx.api.createJD(text.value, title.value.trim());
        ctx.toast.show("Requirements extracted. Please check them.", { tone: "ok" });
        st.list = null; loadList();
        ctx.navigate("jdEdit", { id: jd.id });
      } catch (err) { go.disabled = false; mount(msg); ctx.reportError(err, "Could not read that job description."); }
    } },
      field({ label: "Role title (optional)", id: "jd-title", control: title }),
      field({ label: "Upload JD file", id: "jd-file", hint: "PDF, DOCX, or TXT · up to 10 MB · text is extracted before parsing.", control: file }),
      h("div", { class: "muted small", style: { textAlign: "center" } }, "or paste text below"),
      field({ label: "Job description text", id: "jd-text", hint: `Up to ${JD_MAX_CHARS.toLocaleString("en-US")} characters.`, control: text }),
      msg, h("div", null, go));
    mount(mainHost, form);
    return { el: form, title: "Job description" };
  }

  // ---- editor --------------------------------------------------------------------------------------------
  function drawEditor() {
    const jd = st.jd;
    const spec = jd.spec;
    const original = JSON.parse(JSON.stringify(spec));
    const title = h("input", { class: "input", id: "e-title", value: jd.title ?? spec.title ?? "" });
    const req = chipInput({ label: "Required skills", values: spec.required_skills });
    const pref = chipInput({ label: "Preferred skills", values: spec.preferred_skills });
    const years = h("input", { class: "input", id: "e-years", type: "number", min: "0", max: "60", step: "0.5", value: spec.min_years ?? "", placeholder: "Not specified" });
    const edu = eduSelect("e-edu", spec.min_education);
    const resp = h("textarea", { class: "textarea", id: "e-resp", style: { minHeight: "120px" }, "aria-describedby": "resp-hint" }, "");
    resp.value = (spec.responsibilities ?? []).join("\n");
    const saveMsg = h("div", { "aria-live": "polite", class: "small muted" });

    const current = () => ({
      ...spec,
      title: title.value.trim() || null,
      required_skills: req.value,
      preferred_skills: pref.value.filter((s) => !req.value.includes(s)),
      min_years: years.value === "" ? null : Number(years.value),
      min_education: Number(edu.value) || null,
      responsibilities: resp.value.split("\n").map((l) => l.trim()).filter(Boolean),
    });
    const dirty = () => !sameJson(current(), { ...original, title: original.title ?? null });

    const save = button({ label: "Save changes", variant: "primary", onClick: () => persist() });
    async function persist() {
      save.disabled = true;
      try {
        const next = await ctx.api.updateJD(jd.id, title.value.trim(), current());
        st.jd = next; ctx.toast.show("Saved.", { tone: "ok" }); st.list = null; loadList(); drawEditor();
        return true;
      } catch (err) { save.disabled = false; ctx.reportError(err, "Could not save your changes."); return false; }
    }

    // search panel
    const topK = h("select", { class: "select", id: "s-topk" }, [5, 10, 20, 50].map((n) => h("option", { value: String(n), selected: n === 10 }, `Top ${n}`)));
    const fYears = h("input", { class: "input", id: "s-years", type: "number", min: "0", max: "60", step: "0.5", placeholder: "No minimum" });
    const fEdu = eduSelect("s-edu", 0);
    const must = h("div", { class: "req-pick" });
    const mustSel = new Set();
    mount(must, spec.required_skills.length ? spec.required_skills.map((s) => h("label", { class: "switch", style: { fontSize: "0.875rem" } }, h("input", { type: "checkbox", onChange: (e) => (e.target.checked ? mustSel.add(s) : mustSel.delete(s)) }), h("span", { class: "track", "aria-hidden": "true" }), displaySkill(s))) : h("span", { class: "muted small" }, "Add required skills to use this."));
    let blind = true;
    const run = button({ label: "Search resumes", icon: "search", variant: "primary", onClick: async () => {
      if (dirty() && !(await persist())) return;
      run.disabled = true; st.searching = true; searchMsg();
      try {
        const body = { jd_id: jd.id, top_k: Number(topK.value), blind, filters: { min_years: fYears.value === "" ? null : Number(fYears.value), min_education: Number(fEdu.value) || null, required_skills: [...mustSel] } };
        const res = await ctx.api.search(body);
        ctx.cache.set(`search:${res.search_id}`, res);
        ctx.navigate("results", { id: res.search_id });
      } catch (err) { run.disabled = false; st.searching = false; searchMsg(); ctx.reportError(err, "The search failed."); }
    } });
    const sm = h("div", { "aria-live": "polite" });
    function searchMsg() { mount(sm, st.searching ? h("span", { class: "muted small" }, spinner(), " Ranking resumes…") : null); }

    const empty = !spec.required_skills.length;
    const editor = h("div", { class: "jd-edit" },
      h("div", { class: "sheet pad form-grid" },
        h("div", { class: "row", style: { justifyContent: "space-between" } },
          h("h2", null, "What we understood"),
          h("div", { class: "row", style: { gap: "6px" } },
            h("a", { class: "btn sm", href: buildHash("interviews", { id: jd.id }) }, icon("eye", { size: 14 }), "Interviews"),
            button({ label: "Delete", icon: "trash", size: "sm", variant: "danger", title: "Delete this job description", onClick: () => removeJd(jd) }))),
        empty ? banner("warn", "No required skills were found. Add the skills this role needs so ranking has something to check.") : null,
        field({ label: "Role title", id: "e-title", control: title }),
        field({ label: "Required skills", id: req.id, hint: "A resume that lacks these is marked down.", control: req.el }),
        field({ label: "Preferred skills", id: pref.id, hint: "Nice to have. Smaller effect on the score.", control: pref.el }),
        h("div", { class: "form-grid two" },
          field({ label: "Minimum years of experience", id: "e-years", control: years }),
          field({ label: "Minimum education", id: "e-edu", control: edu })),
        field({ label: "Responsibilities (one per line)", id: "e-resp", control: resp }),
        h("details", { class: "raw" }, h("summary", null, "Original text"), h("pre", null, spec.raw_text)),
        h("div", { class: "row" }, save, saveMsg)),
      h("aside", { class: "sheet search-panel", "aria-labelledby": "sp-h" },
        h("h2", { id: "sp-h" }, "Search your library"),
        field({ label: "How many candidates", id: "s-topk", control: topK }),
        h("fieldset", { style: { border: 0, padding: 0, margin: 0 } }, h("legend", { class: "small", style: { fontWeight: 600, marginBottom: "6px" } }, "Must have (hard filters)"), must),
        h("div", { class: "form-grid two" }, field({ label: "Min. years", id: "s-years", control: fYears }), field({ label: "Min. education", id: "s-edu", control: fEdu })),
        switchControl({ label: "Blind mode: hide names while ranking", checked: true, onChange: (v) => { blind = v; } }),
        run, sm));
    const onInput = () => { saveMsg.textContent = dirty() ? "Unsaved changes" : ""; };
    editor.addEventListener("input", onInput); editor.addEventListener("click", onInput); editor.addEventListener("change", onInput);
    mount(mainHost, editor);
  }

  async function loadJd() {
    mount(mainHost, skeletonRows(4, 60));
    try {
      st.jd = await ctx.api.getJD(id);
      if (alive) drawEditor();
    } catch (err) {
      if (alive) mount(mainHost, err.status === 404 ? emptyState({ title: "That job description was not found", body: "It may have been deleted.", action: button({ label: "Start a new one", variant: "primary", onClick: () => ctx.navigate("jd") }) }) : errorState(err, loadJd));
    }
  }

  drawList(); loadList();
  if (id) loadJd(); else drawComposer();
  return { el, title: id ? "Edit job description" : "Job description", destroy() { alive = false; } };
}
