// Public candidate page (#/interview/:token). No login, no app header. The token in the URL is the credential,
// so it is only ever sent to the invitation endpoints and never logged or stored.
import { h, mount } from "../dom.js";
import { button, banner, field, emptyState, spinner } from "../components/ui.js";
import { buildSlots, browserTimeZone, timeZoneList, slotToRow, fmtSlot } from "../interview.js";
import { fmtDate } from "../format.js";

const MAX_ROWS = 5;

export function interviewView(ctx, route) {
  const token = route.params.token;
  let alive = true;
  const tzDefault = browserTimeZone();
  const st = { inv: null, rows: [{ date: "", start: "", end: "" }], tz: tzDefault, note: "", errors: [], busy: false, done: null };
  const root = h("div", { class: "invite-wrap" });
  const el = h("div", { class: "invite" }, root);

  async function load() {
    mount(root, h("div", { class: "empty" }, spinner(true)));
    try {
      st.inv = await ctx.api.getInvitation(token);
      if (!alive) return;
      const last = st.inv.last_reply;
      if (last) {
        st.rows = last.slots.map(slotToRow);
        st.tz = last.timezone || tzDefault;
        st.note = last.note ?? "";
      }
      draw();
    } catch (err) {
      if (!alive) return;
      if (err.status === 404) mount(root, emptyState({ title: "This link is no longer valid", body: "Please use the latest e-mail you received, or reply to it." }));
      else if (err.status === 429) mount(root, emptyState({ title: "Too many attempts", body: `Please wait ${err.retryAfter ?? "a minute"}${err.retryAfter ? " seconds" : ""} and reload this page.` }));
      else mount(root, emptyState({ title: "Could not load this invitation", body: err.message, action: button({ label: "Try again", onClick: load }) }));
    }
  }

  async function submit() {
    if (st.busy) return;
    const { slots, errors } = buildSlots(st.rows, st.tz);
    st.errors = errors;
    if (errors.length) { draw(); root.querySelector('[role="alert"]')?.focus?.(); return; }
    st.busy = true; draw();
    try {
      await ctx.api.submitInterviewReply(token, { slots, timezone: st.tz, note: st.note.trim() || null });
      if (!alive) return;
      st.done = slots;
    } catch (err) {
      if (!alive) return;
      if (err.status === 410) st.inv = { ...st.inv, expired: true };
      else if (err.status === 404) { mount(root, emptyState({ title: "This link is no longer valid", body: "Please use the latest e-mail you received, or reply to it." })); return; }
      else st.errors = [err.status === 429 ? "Too many attempts. Please wait a minute and try again." : err.message || "Could not send your times. Try again."];
    }
    st.busy = false; draw();
  }

  function rowEl(r, i) {
    const set = (k) => (e) => { r[k] = e.target.value; };
    return h("div", { class: "slot-row" },
      h("div", { class: "field" }, h("label", { for: `d${i}` }, i === 0 ? "Date" : h("span", { class: "sr-only" }, `Date ${i + 1}`)),
        h("input", { class: "input", id: `d${i}`, type: "date", value: r.date, onChange: set("date"), "aria-label": `Option ${i + 1} date` })),
      h("div", { class: "field" }, h("label", { for: `s${i}` }, i === 0 ? "Start" : h("span", { class: "sr-only" }, `Start ${i + 1}`)),
        h("input", { class: "input", id: `s${i}`, type: "time", value: r.start, onChange: set("start"), "aria-label": `Option ${i + 1} start time` })),
      h("div", { class: "field" }, h("label", { for: `e${i}` }, i === 0 ? "End (optional)" : h("span", { class: "sr-only" }, `End ${i + 1}`)),
        h("input", { class: "input", id: `e${i}`, type: "time", value: r.end, onChange: set("end"), "aria-label": `Option ${i + 1} end time` })),
      st.rows.length > 1 ? button({ icon: "x", variant: "ghost", title: `Remove option ${i + 1}`, onClick: () => { st.rows.splice(i, 1); draw(); } }) : h("span"));
  }

  function draw() {
    const inv = st.inv;
    const head = h("div", { class: "stack", style: { gap: "6px" } },
      h("h1", null, `Interview for ${inv.role_title}`),
      inv.company_name ? h("p", { class: "muted", style: { margin: 0 } }, inv.company_name) : null);
    if (st.done) {
      mount(root, head, banner("info", "Thanks. The recruiter has your times."),
        h("ul", { class: "slot-list" }, st.done.map((s) => h("li", null, fmtSlot(s, st.tz)))),
        h("p", { class: "muted small" }, "You can open this link again to change your answer until it expires."),
        button({ label: "Change my answer", onClick: () => { st.done = null; draw(); } }));
      return;
    }
    if (inv.expired) { mount(root, head, banner("warn", "This invitation has expired. Please contact the recruiter.")); return; }
    const tzs = timeZoneList();
    if (!tzs.includes(st.tz)) tzs.unshift(st.tz);
    mount(root, head,
      inv.message ? h("p", { style: { whiteSpace: "pre-wrap" } }, inv.message) : null,
      inv.reply_by ? h("p", { class: "small muted" }, `Please respond by ${fmtDate(`${inv.reply_by}T00:00:00`)}.`) : null,
      inv.last_reply ? banner("info", "You already replied. You can update your answer below.") : null,
      h("form", { class: "stack", style: { gap: "14px" }, novalidate: true, onSubmit: (e) => { e.preventDefault(); submit(); } },
        h("p", { style: { margin: 0, fontWeight: "600" } }, "Which dates and times suit you? Suggest up to five."),
        st.rows.map(rowEl),
        st.rows.length < MAX_ROWS ? button({ label: "Add another option", icon: "plus", onClick: () => { st.rows.push({ date: "", start: "", end: "" }); draw(); } }) : null,
        field({ label: "Your time zone", id: "tz", control: h("select", { class: "select", id: "tz", onChange: (e) => { st.tz = e.target.value; } }, tzs.map((z) => h("option", { value: z, selected: z === st.tz }, z))) }),
        field({ label: "Note (optional)", id: "note", control: h("textarea", { class: "textarea", id: "note", maxlength: "1000", style: { minHeight: "80px" }, onInput: (e) => { st.note = e.target.value; } }, st.note) }),
        st.errors.length ? h("div", { role: "alert", tabindex: "-1" }, st.errors.map((m) => banner("bad", m))) : null,
        button({ label: st.busy ? "Sending" : inv.last_reply ? "Update my times" : "Send my times", type: "submit", variant: "primary", disabled: st.busy })));
  }

  load();
  return { el, title: "Interview times", destroy() { alive = false; } };
}
