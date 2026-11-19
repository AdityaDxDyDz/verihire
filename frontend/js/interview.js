// Pure helpers for the interview-scheduling feature (no DOM access, unit-tested with `node --test`).

function offsetMinutes(utcMs, timeZone) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
    }).formatToParts(new Date(utcMs)).map((p) => [p.type, p.value]),
  );
  const asUtc = Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour, +parts.minute, +parts.second);
  return Math.round((asUtc - Math.floor(utcMs / 1000) * 1000) / 60000); // minutes east of UTC
}

/** "2026-11-25", "10:00", "Asia/Kolkata" -> "2026-11-25T10:00:00+05:30". Throws RangeError for an unknown zone. */
export function toIsoWithOffset(date, time, timeZone) {
  const [y, mo, d] = date.split("-").map(Number);
  const [hh, mm] = time.split(":").map(Number);
  const wall = Date.UTC(y, mo - 1, d, hh, mm, 0);
  let off = offsetMinutes(wall, timeZone);
  off = offsetMinutes(wall - off * 60000, timeZone); // second pass settles times next to a DST change
  const sign = off >= 0 ? "+" : "-";
  const a = Math.abs(off);
  return `${date}T${time}:00${sign}${String(Math.floor(a / 60)).padStart(2, "0")}:${String(a % 60).padStart(2, "0")}`;
}

export function browserTimeZone() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch { return "UTC"; }
}

export function timeZoneList() {
  try {
    const list = Intl.supportedValuesOf?.("timeZone");
    if (list?.length) return list.includes("UTC") ? list : ["UTC", ...list];
  } catch { /* older browsers */ }
  return ["UTC", "Asia/Kolkata", "Europe/London", "Europe/Berlin", "America/New_York", "America/Los_Angeles", "Australia/Sydney"];
}

/** Validate the slot rows of the candidate form. Returns {slots, errors}; errors is an array of messages. */
export function buildSlots(rows, timeZone, now = Date.now()) {
  const slots = [];
  const errors = [];
  rows.forEach((r, i) => {
    const n = i + 1;
    if (!r.date && !r.start && !r.end) return; // blank row: ignored
    if (!r.date || !r.start) { errors.push(`Option ${n}: choose a date and a start time.`); return; }
    let start;
    let end = null;
    try {
      start = toIsoWithOffset(r.date, r.start, timeZone);
      if (r.end) {
        if (r.end <= r.start) { errors.push(`Option ${n}: the end time must be after the start time.`); return; }
        end = toIsoWithOffset(r.date, r.end, timeZone);
      }
    } catch { errors.push("Choose a valid time zone."); return; }
    if (Date.parse(start) < now) { errors.push(`Option ${n}: that time is in the past.`); return; }
    slots.push(end ? { start, end } : { start });
  });
  if (!slots.length && !errors.length) errors.push("Add at least one date and time.");
  if (slots.length > 5) errors.push("You can suggest at most 5 options.");
  return { slots, errors };
}

/** Split an ISO string with offset back into the form values (date, HH:MM) in the zone it was written in. */
export function slotToRow(slot) {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(slot?.start ?? "");
  const e = /T(\d{2}:\d{2})/.exec(slot?.end ?? "");
  return { date: m?.[1] ?? "", start: m?.[2] ?? "", end: e?.[1] ?? "" };
}

/** "Tue 25 Nov, 10:00-11:00 IST" in the viewer's zone (or `viewerZone`). */
export function fmtSlot(slot, viewerZone = undefined, locale = undefined) {
  const opts = { timeZone: viewerZone };
  const day = new Intl.DateTimeFormat(locale, { ...opts, weekday: "short", day: "numeric", month: "short" });
  const hm = new Intl.DateTimeFormat(locale, { ...opts, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const zone = new Intl.DateTimeFormat(locale, { ...opts, timeZoneName: "short" }).formatToParts(new Date(slot.start)).find((p) => p.type === "timeZoneName")?.value ?? "";
  const s = new Date(slot.start);
  const base = `${day.format(s)}, ${hm.format(s)}`;
  return `${slot.end ? `${base}\u2013${hm.format(new Date(slot.end))}` : base} ${zone}`.trim();
}

export const OUTREACH_STATUS = {
  queued: { label: "Queued", tone: "" },
  sending: { label: "Sending", tone: "" },
  sent: { label: "Sent", tone: "pen" },
  failed: { label: "Failed", tone: "bad" },
  replied: { label: "Replied", tone: "ok" },
};

/** The chip status for a list row: a candidate reply wins over "sent". */
export function rowStatus(item) {
  return item.reply ? "replied" : item.status;
}

export function batchSummary(res) {
  const sending = res.queued ?? 0;
  const bits = [`Sending to ${sending} candidate${sending === 1 ? "" : "s"}`];
  if (res.skipped_no_email) bits.push(`${res.skipped_no_email} skipped (no e-mail in resume)`);
  if (res.already_invited) bits.push(`${res.already_invited} already invited`);
  return bits.join(" \u00b7 ");
}

export function progressSummary(batch) {
  const c = batch.counts ?? {};
  const pending = (c.queued ?? 0) + (c.sending ?? 0);
  const parts = [];
  if (c.sent) parts.push(`Sent to ${c.sent}`);
  if (c.failed) parts.push(`${c.failed} failed`);
  if (pending) parts.push(`${pending} still sending`);
  return parts.join(" \u00b7 ") || "Nothing to send";
}
