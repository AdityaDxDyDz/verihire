import { h, mount, debounce } from "../dom.js";
import { icon } from "../icons.js";
import { button, linkButton, chip, statusChip, skillChip, progress, emptyState, errorState, skeletonRows, banner } from "../components/ui.js";
import { confirmDialog } from "../components/overlay.js";
import { openPdf } from "../components/pdf.js";
import { validateFiles, MAX_FILES, MAX_BYTES } from "../validate.js";
import { fmtDate, fmtBytes, fmtYears, eduLabel, isProcessing, plural } from "../format.js";
import { buildHash } from "../router.js";

const FILTERS = [["all", "All"], ["ready", "Ready"], ["processing", "Processing"], ["failed", "Failed"], ["needs_ocr", "Needs OCR"]];
const PAGE_SIZE = 20;
const POLL_MS = 2500;

export function libraryView(ctx) {
  const st = { filter: "all", q: "", page: 1, data: null, error: null, job: null, uploading: null, rejected: [], pollErrors: 0, selected: new Set(), bulkBusy: false };
  let alive = true;
  let timer = null;

  const fileInput = h("input", { type: "file", multiple: true, accept: ".pdf,application/pdf", class: "sr-only", tabindex: "-1", "aria-hidden": "true", onChange: () => { handleFiles(fileInput.files); fileInput.value = ""; } });
  const dropzone = h("div", { class: "dropzone" });
  const jobHost = h("div", { "aria-live": "polite" });
  const filterHost = h("div", { class: "seg", role: "group", "aria-label": "Filter by status" });
  const search = h("input", { class: "input", type: "search", placeholder: "Search by file name", "aria-label": "Search by file name", onInput: debounce(() => { st.q = search.value.trim(); st.page = 1; load(); }, 250) });
  const tableHost = h("div");
  const pagerHost = h("div");
  const headActions = h("div", { class: "page-actions" });

  const el = h(
    "div",
    null,
    h("div", { class: "page-head" },
      h("div", null, h("h1", null, "Resume library"), h("p", { class: "sub" }, "Upload PDF resumes. Each one is read, stripped of personal details, and indexed so it can be searched against a job description.")),
      headActions),
    h("div", { class: "library-grid" },
      h("div", { class: "stack" }, dropzone, jobHost),
      h("section", { "aria-labelledby": "docs-h" },
        h("h2", { id: "docs-h", class: "sr-only" }, "Uploaded resumes"),
        h("div", { class: "filters" }, filterHost, h("div", { class: "search" }, search)),
        tableHost, pagerHost)),
    fileInput,
  );

  // ---- dropzone --------------------------------------------------------------------------------------------
  function drawDropzone() {
    const busy = Boolean(st.uploading);
    mount(
      dropzone,
      icon("upload", { size: 28 }),
      h("h2", null, "Drop PDF resumes here"),
      h("p", null, `Up to ${MAX_FILES} files at a time, ${fmtBytes(MAX_BYTES).replace(".0", "")} each. Text-based PDFs only; scanned pages are flagged.`),
      button({ label: "Choose PDFs", icon: "plus", variant: "primary", disabled: busy, onClick: () => fileInput.click() }),
    );
  }
  for (const ev of ["dragenter", "dragover"]) dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.add("over"); });
  for (const ev of ["dragleave", "drop"]) dropzone.addEventListener(ev, (e) => { e.preventDefault(); dropzone.classList.remove("over"); });
  dropzone.addEventListener("drop", (e) => handleFiles(e.dataTransfer?.files ?? []));

  async function handleFiles(fileList) {
    const files = Array.from(fileList ?? []);
    if (!files.length || st.uploading) return;
    const { accepted, rejected } = validateFiles(files);
    st.rejected = rejected;
    if (!accepted.length) {
      ctx.toast.show("None of those files can be uploaded. Only PDF files up to 10 MB are accepted.", { tone: "bad" });
      drawJob();
      return;
    }
    st.uploading = { loaded: 0, total: 1, count: accepted.length };
    drawDropzone();
    drawJob();
    try {
      const res = await ctx.api.uploadDocuments(accepted, {
        onProgress: (loaded, total) => {
          if (!alive || !st.uploading) return;
          st.uploading.loaded = loaded;
          st.uploading.total = total || 1;
          drawJob();
        },
      });
      if (!alive) return;
      st.uploading = null;
      st.rejected = [...rejected, ...(res.rejected ?? []).map((r) => ({ name: r.filename, reason: r.reason }))];
      if (res.document_ids?.length) {
        st.job = { id: res.job_id, status: "running", total: res.document_ids.length, done: 0, failed: 0, documents: [] };
        ctx.toast.show(`Uploaded ${plural(res.document_ids.length, "file")}. Reading them now.`, { tone: "ok" });
        await refreshJob();
      }
      st.page = 1;
      await load();
    } catch (err) {
      if (!alive) return;
      st.uploading = null;
      ctx.reportError(err, "The upload failed.");
    }
    drawDropzone();
    drawJob();
    schedule();
  }

  // ---- job panel -------------------------------------------------------------------------------------------
  function drawJob() {
    const parts = [];
    if (st.uploading) {
      const u = st.uploading;
      parts.push(h("div", { class: "sheet job-panel" },
        h("div", { class: "row" }, h("strong", null, `Uploading ${plural(u.count, "file")}`), h("span", { class: "spacer" }), h("span", { class: "muted small" }, `${Math.round((u.loaded / u.total) * 100)}%`)),
        progress(u.loaded, u.total)));
    }
    if (st.job) {
      const j = st.job;
      const finished = j.status !== "running";
      const settled = j.done + j.failed;
      parts.push(h("div", { class: "sheet job-panel" },
        h("div", { class: "row" },
          h("strong", null, finished ? (j.failed ? `Finished: ${j.done} ready, ${j.failed} need attention` : `Finished: all ${plural(j.total, "resume")} are ready`) : `Reading resumes: ${settled} of ${j.total} done`),
          h("span", { class: "spacer" }),
          finished ? button({ label: "Dismiss", size: "sm", variant: "ghost", onClick: () => { st.job = null; drawJob(); } }) : null),
        progress(settled, j.total, finished && j.failed ? "bad" : finished ? "ok" : ""),
        j.documents?.length ? h("div", { class: "files" }, j.documents.map((d) => h("div", { class: "f" }, h("span", { class: "name" }, d.filename), d.error ? h("span", { class: "muted small" }, d.error) : null, statusChip(d.status)))) : null));
    }
    if (st.rejected.length) {
      parts.push(banner("warn",
        h("strong", null, `${plural(st.rejected.length, "file")} not uploaded`),
        h("ul", { style: { margin: "6px 0 0", paddingLeft: "18px" } }, st.rejected.map((r) => h("li", null, `${r.name}: ${r.reason}`))),
        h("div", { style: { marginTop: "8px" } }, button({ label: "Dismiss", size: "sm", onClick: () => { st.rejected = []; drawJob(); } }))));
    }
    mount(jobHost, parts);
  }

  async function refreshJob() {
    if (!st.job || st.job.status !== "running") return;
    st.job = await ctx.api.getJob(st.job.id);
  }

  // ---- table -----------------------------------------------------------------------------------------------
  function drawFilters() {
    const counts = st.data?.counts ?? {};
    mount(filterHost, FILTERS.map(([key, label]) =>
      h("button", { type: "button", "aria-pressed": String(st.filter === key), onClick: () => { st.filter = key; st.page = 1; load(); } },
        counts[key] !== undefined ? `${label} ${counts[key]}` : label)));
  }

  function drawTable() {
    drawFilters();
    if (st.error && !st.data) return mount(tableHost, errorState(st.error, () => load()));
    if (!st.data) return mount(tableHost, skeletonRows(6));
    const { items, total, counts } = st.data;
    mount(headActions, counts.ready > 0 ? linkButton({ label: "Add a job description", icon: "plus", variant: "primary", href: buildHash("jd") }) : null);
    if (!items.length) {
      const nothingYet = counts.all === 0;
      mount(tableHost, emptyState({
        title: nothingYet ? "No resumes yet" : "No resumes match",
        body: nothingYet ? "Upload a few PDF resumes to get started. Fictional sample resumes work well for a first try." : "Try a different status or clear the search.",
        action: nothingYet ? button({ label: "Choose PDFs", icon: "upload", variant: "primary", onClick: () => fileInput.click() }) : button({ label: "Clear filters", onClick: () => { st.filter = "all"; st.q = ""; search.value = ""; load(); } }),
      }));
      return mount(pagerHost);
    }
    for (const id of [...st.selected]) if (!items.some((d) => d.id === id)) st.selected.delete(id); // selection is per page
    const allPicked = items.length > 0 && items.every((d) => st.selected.has(d.id));
    const attention = (counts.failed ?? 0) + (counts.needs_ocr ?? 0);
    const bulkBar = h("div", { class: "bulk-bar" },
      h("label", { class: "row small", style: { gap: "6px" } },
        h("input", { type: "checkbox", checked: allPicked, indeterminate: !allPicked && st.selected.size > 0, "aria-label": "Select every resume on this page", onChange: (e) => { for (const d of items) e.target.checked ? st.selected.add(d.id) : st.selected.delete(d.id); drawTable(); } }),
        st.selected.size ? `${st.selected.size} selected` : "Select all on this page"),
      h("span", { class: "spacer" }),
      attention ? button({ label: `Retry failed (${attention})`, icon: "refresh", size: "sm", disabled: st.bulkBusy, onClick: retryAllFailed }) : null,
      button({ label: `Delete selected${st.selected.size ? ` (${st.selected.size})` : ""}`, icon: "trash", size: "sm", variant: "danger", disabled: !st.selected.size || st.bulkBusy, onClick: removeSelected }));
    const rows = items.map((d) => {
      const canRetry = d.status === "failed" || d.status === "needs_ocr";
      return h("tr", { class: st.selected.has(d.id) ? "selected" : "" },
        h("td", null, h("input", { type: "checkbox", checked: st.selected.has(d.id), "aria-label": `Select ${d.filename}`, onChange: (e) => { e.target.checked ? st.selected.add(d.id) : st.selected.delete(d.id); drawTable(); } })),
        h("td", null, h("div", { class: "doc-name" }, d.filename), d.error ? h("div", { class: "doc-err" }, d.error) : null, d.suspicious_hidden_text ? h("div", { style: { marginTop: "4px" } }, chip("Hidden text found", "warn", { iconName: "alert", title: "This PDF contains hidden text, such as white or tiny text." })) : null),
        h("td", null, statusChip(d.status)),
        h("td", { class: "num" }, d.page_count ?? "–"),
        h("td", { class: "nowrap" }, d.years_experience == null ? h("span", { class: "muted" }, "–") : fmtYears(d.years_experience)),
        h("td", { class: "nowrap" }, d.education_level == null ? h("span", { class: "muted" }, "–") : eduLabel(d.education_level)),
        h("td", null, h("div", { class: "chip-list" }, (d.skills ?? []).slice(0, 3).map((s) => skillChip(s)), (d.skills?.length ?? 0) > 3 ? chip(`+${d.skills.length - 3}`, "dim") : null)),
        h("td", { class: "nowrap muted small" }, fmtDate(d.created_at)),
        h("td", { class: "nowrap" }, h("div", { class: "row", style: { gap: "2px", flexWrap: "nowrap" } },
          button({ icon: "eye", size: "sm", variant: "ghost", title: `View original PDF of ${d.filename}`, onClick: () => openPdf(ctx, d.id, { title: d.filename }), disabled: isProcessing(d.status) }),
          canRetry ? button({ icon: "refresh", size: "sm", variant: "ghost", title: `Try ${d.filename} again`, onClick: () => retry(d) }) : null,
          button({ icon: "trash", size: "sm", variant: "ghost", title: `Delete ${d.filename}`, onClick: () => remove(d) }))));
    });
    mount(tableHost, bulkBar, h("div", { class: "table-wrap" }, h("table", { class: "dt" },
      h("thead", null, h("tr", null, h("th", { scope: "col" }, h("span", { class: "sr-only" }, "Select")), ["File", "Status", "Pages", "Experience", "Education", "Skills", "Added", ""].map((t, i) => h("th", { class: i === 2 ? "num" : "", scope: "col" }, t || h("span", { class: "sr-only" }, "Actions"))))),
      h("tbody", null, rows))));
    const from = (st.data.page - 1) * st.data.page_size + 1;
    const to = Math.min(total, from + items.length - 1);
    const pages = Math.max(1, Math.ceil(total / st.data.page_size));
    mount(pagerHost, h("div", { class: "pager" },
      h("span", null, `Showing ${from}–${to} of ${total}`),
      h("div", { class: "row" },
        button({ label: "Previous", icon: "chevronLeft", size: "sm", disabled: st.data.page <= 1, onClick: () => { st.page -= 1; load(); } }),
        h("span", null, `Page ${st.data.page} of ${pages}`),
        button({ label: "Next", icon: "chevronRight", size: "sm", disabled: st.data.page >= pages, onClick: () => { st.page += 1; load(); } }))));
  }

  async function load({ silent = false } = {}) {
    try {
      const data = await ctx.api.listDocuments({ status: st.filter === "all" ? undefined : st.filter, q: st.q, page: st.page, page_size: PAGE_SIZE });
      if (!alive) return;
      st.data = data;
      st.error = null;
      st.pollErrors = 0;
    } catch (err) {
      if (!alive) return;
      st.pollErrors += 1;
      if (!silent || st.pollErrors === 3) ctx.reportError(err, "Could not load your resumes.");
      if (!st.data) st.error = err;
    }
    drawTable();
  }

  async function retry(d) {
    try {
      const { job_id } = await ctx.api.retryDocuments([d.id]);
      st.job = { id: job_id, status: "running", total: 1, done: 0, failed: 0, documents: [] };
      await refreshJob();
      ctx.toast.show(`Trying ${d.filename} again.`);
      drawJob();
      await load();
      schedule();
    } catch (err) {
      ctx.reportError(err, "Could not retry that file.");
    }
  }

  async function remove(d) {
    const ok = await confirmDialog({ title: "Delete this resume?", message: `${d.filename} and everything extracted from it will be permanently removed.`, confirmLabel: "Delete resume", danger: true });
    if (!ok) return;
    try {
      await ctx.api.deleteDocument(d.id);
      ctx.toast.show("Resume deleted.", { tone: "ok" });
      await load();
    } catch (err) {
      ctx.reportError(err, "Could not delete that resume.");
    }
  }

  async function removeSelected() {
    const ids = [...st.selected];
    if (!ids.length) return;
    const ok = await confirmDialog({ title: `Delete ${ids.length} resume${ids.length === 1 ? "" : "s"}?`, message: "The selected resumes and everything extracted from them will be permanently removed.", confirmLabel: "Delete", danger: true });
    if (!ok) return;
    st.bulkBusy = true; drawTable();
    const results = await Promise.allSettled(ids.map((id) => ctx.api.deleteDocument(id)));
    const failed = results.filter((r) => r.status === "rejected").length;
    st.selected.clear();
    st.bulkBusy = false;
    ctx.toast.show(failed ? `Deleted ${ids.length - failed}; ${failed} could not be deleted.` : `Deleted ${ids.length} resume${ids.length === 1 ? "" : "s"}.`, { tone: failed ? "warn" : "ok" });
    await load();
  }

  async function retryAllFailed() {
    st.bulkBusy = true; drawTable();
    try {
      const lists = await Promise.all(["failed", "needs_ocr"].map((s) => ctx.api.listDocuments({ status: s, page: 1, page_size: 100 })));
      const ids = lists.flatMap((l) => l.items.map((d) => d.id));
      if (!ids.length) { ctx.toast.show("Nothing to retry."); return; }
      const { job_id } = await ctx.api.retryDocuments(ids);
      st.job = { id: job_id, status: "running", total: ids.length, done: 0, failed: 0, documents: [] };
      await refreshJob();
      ctx.toast.show(`Trying ${ids.length} file${ids.length === 1 ? "" : "s"} again.`);
      drawJob();
    } catch (err) {
      ctx.reportError(err, "Could not retry the failed files.");
    } finally {
      st.bulkBusy = false;
      await load();
      schedule();
    }
  }

  // ---- polling ---------------------------------------------------------------------------------------------
  function schedule() {
    clearTimeout(timer);
    if (!alive) return;
    const active = st.job?.status === "running" || st.data?.items.some((d) => isProcessing(d.status));
    if (!active || st.pollErrors >= 5) return;
    timer = setTimeout(async () => {
      try { await refreshJob(); } catch (err) { st.pollErrors += 1; }
      drawJob();
      await load({ silent: true });
      schedule();
    }, POLL_MS);
  }

  drawDropzone();
  drawJob();
  drawTable();
  load().then(schedule);
  return { el, title: "Library", destroy() { alive = false; clearTimeout(timer); } };
}
