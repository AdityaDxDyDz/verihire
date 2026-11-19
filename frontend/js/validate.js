// Client-side validation. The backend re-validates everything; these checks only give fast, specific feedback.

export const MAX_BYTES = 10 * 1024 * 1024;
export const MAX_FILES = 100;
export const JD_MIN_CHARS = 40;
export const JD_MAX_CHARS = 20000;

/**
 * @param {{name: string, size: number, type?: string}[]} files
 * @returns {{accepted: object[], rejected: {name: string, reason: string}[]}}
 */
export function validateFiles(files, { maxBytes = MAX_BYTES, maxFiles = MAX_FILES, existingCount = 0 } = {}) {
  const accepted = [];
  const rejected = [];
  const seen = new Set();
  for (const f of files) {
    const name = String(f?.name ?? "unnamed");
    const isPdf = /\.pdf$/i.test(name) || f?.type === "application/pdf";
    const key = `${name.toLowerCase()}:${f?.size}`;
    if (!isPdf) rejected.push({ name, reason: "Only PDF files are accepted." });
    else if (!f.size) rejected.push({ name, reason: "The file is empty." });
    else if (f.size > maxBytes) rejected.push({ name, reason: `Larger than ${Math.round(maxBytes / (1024 * 1024))} MB.` });
    else if (seen.has(key)) rejected.push({ name, reason: "Selected twice." });
    else if (existingCount + accepted.length >= maxFiles) rejected.push({ name, reason: `Over the limit of ${maxFiles} files per upload.` });
    else {
      seen.add(key);
      accepted.push(f);
    }
  }
  return { accepted, rejected };
}

export function jdTextProblem(text) {
  const t = String(text ?? "").trim();
  if (t.length < JD_MIN_CHARS) return `Add at least ${JD_MIN_CHARS} characters (${t.length} so far).`;
  if (t.length > JD_MAX_CHARS) return `That is too long. The limit is ${JD_MAX_CHARS.toLocaleString("en-US")} characters.`;
  return null;
}

export function normalizeSkill(raw) {
  return String(raw ?? "")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .replace(/^[\s,;]+|[\s,;]+$/g, "")
    .slice(0, 60);
}

export function parseSkillInput(raw) {
  return String(raw ?? "").split(/[,\n;]/).map(normalizeSkill).filter(Boolean);
}

export function addSkills(list, raw, limit = 40) {
  const next = [...list];
  for (const s of parseSkillInput(raw)) {
    if (!next.includes(s) && next.length < limit) next.push(s);
  }
  return next;
}

export function removeSkill(list, skill) {
  return list.filter((s) => s !== skill);
}

export function sameList(a, b) {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

/** Stable comparison of two plain JSON-like objects (used for "unsaved changes" detection). */
export function sameJson(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

export function isEmail(s) {
  return /^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$/.test(String(s ?? "").trim());
}
