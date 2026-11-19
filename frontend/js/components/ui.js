// Small reusable UI pieces. All built with h() (no innerHTML).
import { h } from "../dom.js";
import { icon } from "../icons.js";
import { statusMeta, displaySkill, scoreTone, fmtScore, clamp } from "../format.js";

export function button({ label, icon: iconName, variant = "", size = "", onClick, disabled = false, title, type = "button", ariaLabel, pressed, className = "" } = {}) {
  const iconOnly = !label;
  const cls = ["btn", variant, size, iconOnly ? "icon" : "", className].filter(Boolean).join(" ");
  return h(
    "button",
    {
      class: cls, type, onClick, disabled, title, "aria-label": ariaLabel ?? (iconOnly ? title : undefined),
      "aria-pressed": pressed === undefined ? undefined : String(Boolean(pressed)),
    },
    iconName ? icon(iconName, { size: size === "sm" ? 14 : 16 }) : null,
    label,
  );
}

export function linkButton({ label, href, icon: iconName, variant = "", size = "", target, title }) {
  return h(
    "a",
    { class: ["btn", variant, size].filter(Boolean).join(" "), href, target, rel: target === "_blank" ? "noopener noreferrer" : undefined, title },
    iconName ? icon(iconName, { size: size === "sm" ? 14 : 16 }) : null,
    label,
  );
}

export function chip(text, tone = "", { iconName, title } = {}) {
  return h("span", { class: `chip ${tone}`.trim(), title }, iconName ? icon(iconName, { size: 12 }) : null, text);
}

export function statusChip(status) {
  const m = statusMeta(status);
  return h(
    "span",
    { class: `chip ${m.tone}` },
    m.live ? h("span", { class: "dot-live", "aria-hidden": "true" }) : status === "ready" ? icon("check", { size: 12 }) : status === "failed" ? icon("x", { size: 12 }) : icon("alert", { size: 12 }),
    m.label,
  );
}

/** state: "match" (has it), "miss" (does not), "plain". Icons carry the meaning, colour only reinforces it. */
export function skillChip(skill, state = "plain") {
  const label = displaySkill(skill);
  if (state === "match") return h("span", { class: "chip ok", title: `${label}: found in the resume` }, icon("check", { size: 12 }), label);
  if (state === "miss") return h("span", { class: "chip bad", title: `${label}: not found in the resume` }, icon("x", { size: 12 }), label);
  return h("span", { class: "chip outline" }, label);
}

export function scoreBlock(score, { showLabel = false } = {}) {
  const v = clamp(Number(score) || 0, 0, 100);
  const tone = scoreTone(v);
  return h(
    "div",
    { class: `score ${tone === "high" ? "" : tone}`.trim(), title: `Match score ${v.toFixed(1)} out of 100. A relative ranking score, not a probability.` },
    h("b", null, fmtScore(v), showLabel ? h("span", { class: "sr-only" }, " out of 100") : null),
    h("div", { class: "progress", role: "presentation" }, h("i", { style: { width: `${v}%` } })),
  );
}

export function progress(value, max, tone = "") {
  const pct = max > 0 ? clamp((value / max) * 100, 0, 100) : 0;
  return h("div", { class: `progress ${tone}`.trim(), role: "progressbar", "aria-valuemin": "0", "aria-valuemax": String(max), "aria-valuenow": String(value) }, h("i", { style: { width: `${pct}%` } }));
}

export function spinner(large = false) {
  return h("span", { class: `spinner${large ? " lg" : ""}`, role: "status", "aria-label": "Loading" });
}

export function banner(tone, ...content) {
  const iconName = tone === "bad" ? "alert" : tone === "warn" ? "alert" : "info";
  return h("div", { class: `banner ${tone}`, role: tone === "bad" ? "alert" : "status" }, icon(iconName, { size: 18 }), h("div", null, ...content));
}

export function emptyState({ title, body, action }) {
  return h("div", { class: "empty" }, h("h2", null, title), body ? h("p", null, body) : null, action ?? null);
}

export function errorState(err, onRetry) {
  return h(
    "div",
    { class: "empty", role: "alert" },
    h("h2", null, "Something went wrong"),
    h("p", null, err?.message ?? "The request failed."),
    onRetry ? button({ label: "Try again", icon: "refresh", onClick: onRetry }) : null,
  );
}

export function skeletonRows(n = 5, height = 44) {
  return h("div", { class: "stack", "aria-busy": "true", "aria-label": "Loading" }, Array.from({ length: n }, () => h("div", { class: "skel", style: { height: `${height}px` } })));
}

export function field({ label, hint, control, error, id }) {
  return h(
    "div",
    { class: "field" },
    label ? h("label", { for: id }, label) : null,
    control,
    hint ? h("div", { class: "hint" }, hint) : null,
    error ? h("div", { class: "error", role: "alert" }, error) : null,
  );
}

export function switchControl({ label, checked, onChange, id }) {
  return h(
    "label",
    { class: "switch" },
    h("input", { type: "checkbox", id, checked, onChange: (e) => onChange(e.target.checked), role: "switch" }),
    h("span", { class: "track", "aria-hidden": "true" }),
    label,
  );
}
