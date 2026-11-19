// "Request interview times" dialog: sends the invitation e-mails for the ticked candidates and shows the progress.
import { h, mount } from "../dom.js";
import { button, banner, field } from "./ui.js";
import { batchSummary, progressSummary } from "../interview.js";
import { buildHash } from "../router.js";

const COMPANY_KEY = "verihire.company";
const store = {
  get() { try { return window.localStorage.getItem(COMPANY_KEY) ?? ""; } catch { return ""; } },
  set(v) { try { window.localStorage.setItem(COMPANY_KEY, v); } catch { /* private mode */ } },
};
const todayIso = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);

/** @param {{jdId: string, candidates: {candidate_id: string, label: string}[], onSent?: () => void}} opts */
export function openOutreachDialog(ctx, { jdId, candidates, onSent }) {
  const company = h("input", { class: "input", id: "o-company", maxlength: "120", value: store.get(), placeholder: "e.g. Acme Labs" });
  const message = h("textarea", { class: "textarea", id: "o-message", maxlength: "2000", style: { minHeight: "110px" }, placeholder: "e.g. The interview is a 45-minute video call." });
  const replyBy = h("input", { class: "input", id: "o-reply-by", type: "date", min: todayIso() });
  const resend = h("input", { type: "checkbox", id: "o-resend" });
  const status = h("div", { "aria-live": "polite" });
  let alive = true;
  let timer = null;

  const sendBtn = button({ label: `Send to ${candidates.length}`, icon: "sparkle", variant: "primary", onClick: send });
  const closeBtn = button({ label: "Cancel", onClick: () => modal.close() });
  const form = h("form", { class: "stack", style: { gap: "14px" }, onSubmit: (e) => { e.preventDefault(); send(); } },
    h("p", { class: "muted small", style: { margin: 0 } },
      `Each of the ${candidates.length} selected candidate${candidates.length === 1 ? "" : "s"} who has an e-mail address in their resume gets a private link to choose interview times. `,
      "Candidates without an address are skipped. You only ever see a masked address."),
    field({ label: "Company name (optional)", id: "o-company", control: company, hint: "Shown in the e-mail. Remembered for next time." }),
    field({ label: "Message to candidates (optional)", id: "o-message", control: message }),
    field({ label: "Reply by (optional)", id: "o-reply-by", control: replyBy }),
    h("label", { class: "switch" }, resend, h("span", { class: "track", "aria-hidden": "true" }), "Send again to candidates already invited for this job"),
    status);

  const modal = ctx.openModal({ title: "Request interview times", body: form, actions: [closeBtn, sendBtn], onClose: () => { alive = false; clearTimeout(timer); } });
  company.setAttribute("data-autofocus", "");
  company.focus();

  function lock(on) { sendBtn.disabled = on; for (const c of [company, message, replyBy, resend]) c.disabled = on; }

  async function send() {
    if (sendBtn.disabled) return;
    lock(true);
    mount(status);
    try {
      store.set(company.value.trim());
      const res = await ctx.api.requestInterviews({
        jd_id: jdId,
        candidate_ids: candidates.map((c) => c.candidate_id),
        company_name: company.value.trim() || null,
        message: message.value.trim() || null,
        reply_by: replyBy.value || null,
        resend: resend.checked,
      });
      if (!alive) return;
      onSent?.();
      sendBtn.hidden = true;
      closeBtn.replaceChildren("Close");
      showResult(res);
    } catch (err) {
      if (!alive) return;
      lock(false);
      if (err.status === 401) { modal.close(); ctx.reportError(err); return; }
      const text = err.code === "email_disabled" ? "E-mail sending is switched off on this server."
        : err.code === "rate_limited" ? `Too many sends. Try again${err.retryAfter ? ` in ${err.retryAfter}s` : " in a minute"}.`
        : err.message || "Could not send the requests.";
      mount(status, banner("bad", text));
    }
  }

  function showResult(res) {
    const skipped = res.items.filter((i) => i.status === "skipped_no_email");
    const already = res.items.filter((i) => i.status === "already_invited");
    const line = h("p", { class: "small", style: { margin: 0 } }, "");
    const progressLine = h("p", { style: { margin: 0, fontWeight: "600" } }, batchSummary(res));
    const list = (title, items) => items.length ? h("details", null, h("summary", null, `${title} (${items.length})`), h("ul", { class: "small" }, items.map((i) => h("li", null, i.label)))) : null;
    mount(form,
      banner(res.queued ? "info" : "warn", res.queued ? "Invitations are being sent." : "No e-mails were sent: nobody selected can be invited right now."),
      progressLine, line, list("Skipped: no e-mail in resume", skipped), list("Already invited", already),
      res.queued ? h("p", { class: "small" }, h("a", { href: buildHash("interviews", { id: jdId }), onClick: () => modal.close() }, "See replies in Interviews")) : null);
    if (res.queued) poll(res, progressLine, line);
  }

  function poll(res, progressLine, line, started = Date.now()) {
    const tick = async () => {
      if (!alive) return;
      try {
        const b = await ctx.api.getOutreachBatch(res.batch_id);
        if (!alive) return;
        progressLine.textContent = progressSummary(b);
        if (b.done) { line.textContent = b.counts?.failed ? "Some e-mails failed. Open Interviews to resend them." : "All e-mails were handed to the mail server."; return; }
      } catch { /* keep trying until the time limit */ }
      if (Date.now() - started > 30000) { line.textContent = "Still sending in the background. Check Interviews for progress."; return; }
      timer = setTimeout(tick, 2000);
    };
    timer = setTimeout(tick, 1200);
  }
  return modal;
}

