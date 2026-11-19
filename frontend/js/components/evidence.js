// The "marked paper": resume text as a white sheet, with verified quotes highlighted like a marker stroke.
import { h } from "../dom.js";
import { buildSegments, excerptAround, splitRedactions } from "../highlight.js";
import { sectionLabel } from "../format.js";
import { button } from "./ui.js";

/** Text -> nodes. Marks come only from [start, end) ranges; [NAME]-style tokens become redaction bars. */
export function renderText(text, ranges, { sweep = false } = {}) {
  const nodes = [];
  let markIndex = 0;
  for (const seg of buildSegments(text, ranges)) {
    const inner = splitRedactions(seg.text).map((p) => (p.redacted ? h("span", { class: "redacted", title: "Personal detail removed" }, p.text) : p.text));
    if (seg.marked) {
      nodes.push(h("mark", { class: `ev${sweep ? " sweep" : ""}`, style: sweep ? { "--sweep-delay": `${markIndex * 0.14}s` } : undefined }, inner));
      markIndex += 1;
    } else nodes.push(...inner);
  }
  return nodes;
}

/**
 * @param {{text: string, ranges: {start:number,end:number}[], page: number, section?: string|null, onOpenPdf?: (page:number)=>void, sweep?: boolean}} p
 */
export function paper({ text, ranges, page, section, onOpenPdf, sweep = false }) {
  const ex = excerptAround(text, ranges);
  return h(
    "figure",
    { class: "paper", style: { margin: "0" } },
    h(
      "figcaption",
      { class: "paper-meta" },
      h("span", null, sectionLabel(section)),
      h("span", null, `Page ${page}`),
      h("span", { class: "spacer" }),
      onOpenPdf ? button({ label: "Open page in PDF", icon: "external", size: "sm", variant: "ghost", onClick: () => onOpenPdf(page) }) : null,
    ),
    h("div", { class: "paper-text" }, ex.cutStart ? h("span", { class: "cut" }, "… ") : null, renderText(ex.text, ex.ranges, { sweep }), ex.cutEnd ? h("span", { class: "cut" }, " …") : null),
  );
}
