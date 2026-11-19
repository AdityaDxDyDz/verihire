// Inline SVG icons (stroke style, 24x24 grid). Built with createElementNS, so no markup strings are parsed.
const NS = "http://www.w3.org/2000/svg";

const PATHS = {
  check: ["M20 6 9 17l-5-5"],
  x: ["M18 6 6 18M6 6l12 12"],
  plus: ["M12 5v14M5 12h14"],
  upload: ["M12 16V4m0 0L7 9m5-5 5 5M4 20h16"],
  download: ["M12 4v12m0 0-5-5m5 5 5-5M4 20h16"],
  file: ["M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5ZM14 3v5h5"],
  trash: ["M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"],
  refresh: ["M20 11a8 8 0 0 0-14-4L4 9M4 4v5h5M4 13a8 8 0 0 0 14 4l2-2M20 20v-5h-5"],
  eye: ["M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z", "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z"],
  thumbUp: ["M7 11v9H4v-9h3Zm0 0 4-8c1.5 0 2.5 1 2.2 2.5L12.7 9H19a2 2 0 0 1 2 2.3l-1.3 7A2 2 0 0 1 17.7 20H7"],
  thumbDown: ["M17 13V4h3v9h-3Zm0 0-4 8c-1.5 0-2.5-1-2.2-2.5L11.3 15H5a2 2 0 0 1-2-2.3l1.3-7A2 2 0 0 1 6.3 4H17"],
  external: ["M14 4h6v6M20 4 10 14M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"],
  search: ["M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16ZM21 21l-4.3-4.3"],
  chevronDown: ["m6 9 6 6 6-6"],
  chevronUp: ["m6 15 6-6 6 6"],
  chevronLeft: ["m15 6-6 6 6 6"],
  chevronRight: ["m9 6 6 6-6 6"],
  alert: ["M12 9v4M12 17h.01M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"],
  info: ["M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20ZM12 16v-4M12 8h.01"],
  shield: ["M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6l-8-3Z", "m9 12 2 2 4-4"],
  compare: ["M4 5h6v14H4zM14 5h6v14h-6z"],
  moon: ["M21 13A9 9 0 1 1 11 3a7 7 0 0 0 10 10Z"],
  sun: ["M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10ZM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"],
  logout: ["M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"],
  sparkle: ["M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Z"],
  lock: ["M7 11V8a5 5 0 0 1 10 0v3M5 11h14v10H5z"],
  arrowUpDown: ["M8 4v16M8 4 4 8M8 4l4 4M16 20V4M16 20l-4-4M16 20l4-4"],
};

export function icon(name, { size = 16, className = "" } = {}) {
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  if (className) svg.setAttribute("class", className);
  for (const d of PATHS[name] ?? []) {
    const p = document.createElementNS(NS, "path");
    p.setAttribute("d", d);
    svg.append(p);
  }
  return svg;
}

export function iconNames() {
  return Object.keys(PATHS);
}

/** Brand mark: a resume sheet with a highlighter stroke and a check. */
export function logoMark(size = 26) {
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 28 28");
  svg.setAttribute("width", String(size));
  svg.setAttribute("height", String(size));
  svg.setAttribute("aria-hidden", "true");
  const el = (tag, attrs) => {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    svg.append(n);
  };
  el("rect", { x: "4", y: "3", width: "20", height: "22", rx: "2.5", fill: "var(--sheet)", stroke: "currentColor", "stroke-width": "1.9" });
  el("rect", { x: "6.6", y: "11.2", width: "14.8", height: "5.4", rx: "1.2", fill: "var(--mark)" });
  el("path", { d: "M8 7.6h8M8 20.4h9", stroke: "currentColor", "stroke-width": "1.9", "stroke-linecap": "round", fill: "none" });
  el("path", { d: "m15.6 14 1.7 1.7 3.3-3.6", stroke: "currentColor", "stroke-width": "1.9", "stroke-linecap": "round", "stroke-linejoin": "round", fill: "none" });
  return svg;
}
