// Recruiter panel: every interview invitation sent for one job description, with delivery status and replies.
import { h, mount } from "../dom.js";
import { button, linkButton, chip, emptyState, errorState, skeletonRows } from "../components/ui.js";
import { buildHash } from "../router.js";
import { fmtDate, plural } from "../format.js";
import { OUTREACH_STATUS, rowStatus, fmtSlot } from "../interview.js";

const FILTERS = [["", "All"], ["queued", "Queued"], ["sending", "Sending"], ["sent", "Sent"], ["failed", "Failed"], ["replied", "Replied"]];

export function interviewsView(ctx, route) {
  const jdId = route.params.id;
  let alive = true;
  let timer = null;
  const st = { items: null, filter: "", busy: new Set() };
  const body = h("div", null, skeletonRows(5, 52));
  const el = h("div", null, body);

  async function load({ quiet = false } = {}) {
    if (!quiet) mount(body, skeletonRows(5, 52));
    try {
      const res = await ctx.api.listInterviewRequests({ jd_id: jdId, status: st.filter });
      if (!alive) return;
      st.items = res.items;
      draw();
      clearTimeout(timer);
      if (res.items.some((i) => i.status === "queued" || i.status === "sending")) timer = setTimeout(() => load({ quiet: true }), 4000);
    } catch (err) {
      if (!alive) return;
      if (err.status === 401) { ctx.reportError(err); return; }
      if (quiet) return;
      mount(body, err.code === "email_disabled" ? emptyState({ title: "E-mail is switched off", body: "This server runs with e-mail disabled." }) : errorState(err, () => load()));
    }
  }

  async function resend(item) {
    st.busy.add(item.candidate_id); draw();
    try {
      const res = await ctx.api.requestInterviews({ jd_id: jdId, candidate_ids: [item.candidate_id], resend: true });
      ctx.toast.show(res.queued ? `Sending again to ${item.label}.` : `${item.label} has no e-mail address in their resume.`, { tone: res.queued ? "ok" : "warn", timeout: 4000 });
      st.busy.delete(item.candidate_id);
      await load({ quiet: true });
    } catch (err) { st.busy.delete(item.candidate_id); draw(); ctx.reportError(err, "Could not resend."); }
  }

  function replyCell(item) {
    const r = item.reply;
    if (!r) return h("span", { class: "muted" }, "No reply yet");
    return h("div", null,
      h("ul", { class: "slot-list" }, r.slots.map((s) => h("li", null, fmtSlot(s)))),
      h("div", { class: "sub muted small" }, `Candidate's time zone: ${r.timezone}`),
      r.note ? h("div", { class: "small", style: { marginTop: "4px" } }, `\u201c${r.note}\u201d`) : null);
  }

  function row(item) {
    const key = rowStatus(item);
    const meta = OUTREACH_STATUS[key] ?? { label: key, tone: "" };
    const last = ctx.store.get().lastResults;
    return h("tr", null,
      h("td", { class: "cand-cell" }, last ? h("a", { class: "cand-link", href: buildHash("candidate", { id: last, cid: item.candidate_id }) }, item.label) : item.label),
      h("td", { class: "nowrap" }, item.email_masked ?? "–"),
      h("td", null, chip(meta.label, meta.tone, { title: item.error ?? undefined }), item.error ? h("div", { class: "sub small", style: { color: "var(--bad)" } }, item.error) : null),
      h("td", { class: "nowrap" }, item.sent_at ? fmtDate(item.sent_at) : "–"),
      h("td", null, replyCell(item)),
      h("td", null, button({ label: "Resend", icon: "refresh", size: "sm", disabled: st.busy.has(item.candidate_id) || item.status === "queued" || item.status === "sending", onClick: () => resend(item) })));
  }

  function draw() {
    const items = st.items ?? [];
    const replied = items.filter((i) => i.reply).length;
    mount(body,
      h("div", { class: "page-head" },
        h("div", null, h("h1", null, "Interviews"),
          h("div", { class: "run-meta" }, h("span", null, h("b", null, plural(items.length, "invitation")), st.filter ? ` (${st.filter})` : ""), h("span", null, `${replied} replied`))),
        h("div", { class: "page-actions" },
          button({ label: "Refresh", icon: "refresh", onClick: () => load() }),
          ctx.store.get().lastResults ? linkButton({ label: "Back to results", href: buildHash("results", { id: ctx.store.get().lastResults }) }) : null)),
      h("div", { class: "results-bar" },
        h("label", { class: "small", for: "iv-filter" }, "Show"),
        h("select", { class: "select", id: "iv-filter", style: { width: "auto" }, onChange: (e) => { st.filter = e.target.value; load(); } },
          FILTERS.map(([v, l]) => h("option", { value: v, selected: st.filter === v }, l)))),
      items.length
        ? h("div", { class: "table-wrap" }, h("table", { class: "dt" },
          h("thead", null, h("tr", null, ["Candidate", "E-mail", "Status", "Sent", "Preferred times", ""].map((t) => h("th", { scope: "col" }, t)))),
          h("tbody", null, items.map(row))))
        : emptyState({ title: st.filter ? "Nothing with that status" : "No invitations yet", body: st.filter ? "Try another filter." : "Tick candidates on the results page and choose Request interview times." }));
  }

  load();
  return { el, title: "Interviews", destroy() { alive = false; clearTimeout(timer); } };
}

