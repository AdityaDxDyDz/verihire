// Toasts, modal dialogs and a confirm helper, with keyboard handling (Esc to close, Tab stays inside, focus returns).
import { h, nextId } from "../dom.js";
import { icon } from "../icons.js";
import { button } from "./ui.js";

export function createToaster(root) {
  return {
    show(message, { tone = "", timeout = 5000 } = {}) {
      const el = h(
        "div",
        { class: `toast ${tone}`.trim(), role: tone === "bad" ? "alert" : "status" },
        h("span", { class: "msg" }, message),
        h("button", { type: "button", "aria-label": "Dismiss", onClick: () => el.remove() }, icon("x", { size: 14 })),
      );
      root.append(el);
      if (timeout) setTimeout(() => el.remove(), timeout);
      while (root.children.length > 4) root.firstChild.remove();
      return el;
    },
  };
}

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Trap Tab inside `container`, close on Escape. Returns a cleanup function. */
export function trapFocus(container, onEscape) {
  const onKey = (e) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      onEscape?.();
      return;
    }
    if (e.key !== "Tab") return;
    const items = [...container.querySelectorAll(FOCUSABLE)].filter((n) => n.offsetParent !== null || n === document.activeElement);
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };
  container.addEventListener("keydown", onKey);
  return () => container.removeEventListener("keydown", onKey);
}

/**
 * @param {{title: string, body: Node|Node[], actions?: Node[], wide?: boolean, flush?: boolean, onClose?: () => void}} opts
 */
export function openModal({ title, body, actions, wide = false, flush = false, onClose }) {
  const root = document.getElementById("modal-root");
  const previous = document.activeElement;
  const titleId = nextId("modal-title");
  const bodyEl = h("div", { class: `modal-body${flush ? " flush" : ""}` }, body);
  const closeBtn = button({ icon: "x", variant: "ghost", title: "Close", onClick: () => close() });
  const dialog = h(
    "div",
    { class: `modal${wide ? " wide" : ""}`, role: "dialog", "aria-modal": "true", "aria-labelledby": titleId },
    h("div", { class: "modal-head" }, h("h2", { id: titleId }, title), closeBtn),
    bodyEl,
    actions?.length ? h("div", { class: "modal-foot" }, actions) : null,
  );
  const backdrop = h("div", { class: "backdrop" }, dialog);
  let closed = false;
  const untrap = trapFocus(dialog, () => close());
  backdrop.addEventListener("mousedown", (e) => {
    if (e.target === backdrop) close();
  });
  function close() {
    if (closed) return;
    closed = true;
    untrap();
    backdrop.remove();
    if (previous && document.contains(previous)) previous.focus();
    onClose?.();
  }
  root.append(backdrop);
  (dialog.querySelector("[data-autofocus]") ?? closeBtn).focus();
  return { close, el: dialog, body: bodyEl };
}

/** Resolves true when the user confirms, false when they cancel or press Escape. */
export function confirmDialog({ title, message, confirmLabel = "Confirm", danger = false }) {
  return new Promise((resolve) => {
    let modal;
    const done = (v) => {
      resolve(v);
      modal.close();
    };
    const confirm = button({ label: confirmLabel, variant: danger ? "danger solid" : "primary", onClick: () => done(true) });
    confirm.setAttribute("data-autofocus", "");
    modal = openModal({
      title,
      body: h("p", null, message),
      actions: [button({ label: "Cancel", onClick: () => done(false) }), confirm],
      onClose: () => resolve(false),
    });
  });
}
