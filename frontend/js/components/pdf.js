// Original-PDF viewer modal. The URL comes from the backend (short-lived, audited) and is checked before use.
import { h, mount } from "../dom.js";
import { openModal } from "./overlay.js";
import { linkButton, spinner } from "./ui.js";
import { pdfPageUrl } from "../format.js";

export async function openPdf(ctx, documentId, { title = "Original resume", page } = {}) {
  const body = h("div", { style: { display: "grid", placeItems: "center", width: "100%", minHeight: "320px" } }, spinner(true));
  const openLink = linkButton({ label: "Open in new tab", icon: "external", href: "#", target: "_blank" });
  openLink.setAttribute("aria-disabled", "true");
  const modal = openModal({ title, body, wide: true, flush: true, actions: [openLink] });
  try {
    const { url } = await ctx.api.pdfUrl(documentId);
    const href = pdfPageUrl(url, page);
    if (href === "#") throw new Error("The PDF link returned by the server is not valid.");
    openLink.setAttribute("href", href);
    openLink.removeAttribute("aria-disabled");
    mount(modal.body, h("iframe", { class: "pdf-frame", src: href, title: `${title} (PDF)`, referrerpolicy: "no-referrer" }));
  } catch (err) {
    modal.close();
    ctx.reportError(err, "Could not open the PDF.");
  }
}
