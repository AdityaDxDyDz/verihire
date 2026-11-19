// Pure formatting helpers (no DOM access, unit-tested with `node --test`).

export const EDU_LABELS = ["Not stated", "Diploma", "Bachelor", "Master", "PhD"];

export function eduLabel(level) {
  const n = Number(level);
  return Number.isInteger(n) && n >= 0 && n < EDU_LABELS.length ? EDU_LABELS[n] : EDU_LABELS[0];
}

function trimZero(n) {
  return Number.isInteger(n) ? String(n) : String(Math.round(n * 10) / 10);
}

export function fmtYears(y) {
  if (y === null || y === undefined || Number.isNaN(Number(y))) return "Not stated";
  const n = Number(y);
  return n === 1 ? "1 year" : `${trimZero(n)} years`;
}

export function fmtScore(n) {
  const v = Number(n);
  return Number.isFinite(v) ? String(Math.round(v)) : "0";
}

export function scoreTone(n) {
  const v = Number(n);
  if (v >= 75) return "high";
  if (v >= 50) return "mid";
  return "low";
}

export function clamp(n, lo, hi) {
  return Math.min(hi, Math.max(lo, n));
}

export function fmtBytes(bytes) {
  const b = Number(bytes);
  if (!Number.isFinite(b) || b < 0) return "";
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${Math.round(b / 1024)} KB`;
  return `${(b / (1024 * 1024)).toFixed(1)} MB`;
}

export function fmtDuration(ms) {
  const v = Number(ms);
  if (!Number.isFinite(v)) return "";
  return v < 1000 ? `${Math.round(v)} ms` : `${(v / 1000).toFixed(1)} s`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Relative time for recent dates, "7 Nov" otherwise. `now` is injectable for tests. */
export function fmtDate(iso, now = Date.now()) {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const diff = Math.max(0, now - t);
  const min = Math.floor(diff / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.floor(h / 24);
  if (d === 1) return "yesterday";
  const date = new Date(t);
  return `${date.getDate()} ${MONTHS[date.getMonth()]}`;
}

export function plural(n, one, many = `${one}s`) {
  return `${n} ${n === 1 ? one : many}`;
}

const SKILL_DISPLAY = {
  aws: "AWS", gcp: "GCP", sql: "SQL", css: "CSS", html: "HTML", "ci/cd": "CI/CD", nlp: "NLP", rest: "REST APIs",
  fastapi: "FastAPI", postgresql: "PostgreSQL", mysql: "MySQL", mongodb: "MongoDB", graphql: "GraphQL",
  javascript: "JavaScript", typescript: "TypeScript", "node.js": "Node.js", pytorch: "PyTorch", tensorflow: "TensorFlow",
  xgboost: "XGBoost", "scikit-learn": "scikit-learn", "spring boot": "Spring Boot", junit: "JUnit", rabbitmq: "RabbitMQ",
  "github actions": "GitHub Actions", "power bi": "Power BI", rag: "RAG", "c++": "C++", "c#": "C#", pgvector: "pgvector",
  "machine learning": "machine learning", "deep learning": "deep learning", kubernetes: "Kubernetes", docker: "Docker",
  python: "Python", java: "Java", react: "React", django: "Django", flask: "Flask", redis: "Redis", kafka: "Kafka",
  terraform: "Terraform", linux: "Linux", git: "Git", pandas: "pandas", numpy: "NumPy", spark: "Spark", airflow: "Airflow",
  tableau: "Tableau", excel: "Excel", selenium: "Selenium", pytest: "pytest", jira: "Jira", agile: "Agile", ansible: "Ansible",
  prometheus: "Prometheus", grafana: "Grafana", figma: "Figma", cypress: "Cypress", angular: "Angular", vue: "Vue",
  microservices: "microservices", golang: "Go", rust: "Rust", express: "Express", nginx: "NGINX", jenkins: "Jenkins",
};

/** Canonical lowercase skill -> display name ("postgresql" -> "PostgreSQL"). Unknown skills pass through. */
export function displaySkill(skill) {
  const s = String(skill ?? "").trim();
  return SKILL_DISPLAY[s.toLowerCase()] ?? s;
}

const FEATURE_LABELS = {
  rrf_sum_top3: "Search relevance (top 3 passages)",
  rrf_max: "Best passage match",
  n_chunks_hit: "Passages matched",
  best_vec_sim: "Closest meaning match",
  mean_vec_sim: "Average meaning match",
  required_skill_coverage: "Required skills covered",
  preferred_skill_coverage: "Preferred skills covered",
  n_required_missing: "Required skills missing",
  skills_in_experience_ratio: "Skills shown in work history",
  years_exp: "Years of experience",
  years_gap: "Experience vs. minimum",
  edu_level: "Education level",
  edu_gap: "Education vs. requirement",
  title_match: "Job title similarity",
  experience_section_hit: "Evidence in work experience",
  cross_encoder_max: "Passage re-read score",
  cross_encoder_mean: "Average passage re-read score",
  retrieval_strength: "Search relevance",
  years_fit: "Experience fit",
  education_fit: "Education fit",
};

/** Feature key -> plain-language label; unknown keys are prettified so new model features still render. */
export function featureLabel(key) {
  if (FEATURE_LABELS[key]) return FEATURE_LABELS[key];
  const s = String(key ?? "").replace(/[_-]+/g, " ").trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : "";
}

const STATUS = {
  uploaded: { label: "Queued", tone: "pen", live: true },
  extracting: { label: "Reading PDF", tone: "pen", live: true },
  scrubbing: { label: "Removing personal details", tone: "pen", live: true },
  chunking: { label: "Splitting into sections", tone: "pen", live: true },
  parsing: { label: "Reading profile", tone: "pen", live: true },
  embedding: { label: "Indexing", tone: "pen", live: true },
  ready: { label: "Ready", tone: "ok", live: false },
  failed: { label: "Failed", tone: "bad", live: false },
  needs_ocr: { label: "Needs OCR", tone: "warn", live: false },
};

export function statusMeta(status) {
  return STATUS[status] ?? { label: String(status ?? "Unknown"), tone: "outline", live: false };
}

export function isProcessing(status) {
  return Boolean(STATUS[status]?.live);
}

const SECTIONS = {
  summary: "Summary", experience: "Experience", skills: "Skills", education: "Education", projects: "Projects",
  certifications: "Certifications", other: "Header",
};

export function sectionLabel(section) {
  return SECTIONS[section] ?? (section ? String(section) : "Other");
}

/** Only relative URLs and http(s) URLs may become href/src values (API data is not trusted). */
export function safeHref(url) {
  const u = String(url ?? "").trim();
  if (/^\/(?!\/)/.test(u) || /^https?:\/\//i.test(u)) return u;
  if (/^[\w.-]+(\/[\w.%~+-]+)*(\?.*)?$/.test(u) && !/^[a-z][a-z0-9+.-]*:/i.test(u)) return u;
  return "#";
}

export function pdfPageUrl(url, page) {
  const base = safeHref(url);
  if (base === "#") return base;
  const n = Number(page);
  return Number.isInteger(n) && n > 0 ? `${base.split("#")[0]}#page=${n}` : base;
}

/** Build a CSV (with formula-injection guard) from rows of values. Used for client-side fallback exports. */
export function toCsv(rows) {
  const esc = (v) => {
    let s = v === null || v === undefined ? "" : String(v);
    if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`;
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return rows.map((r) => r.map(esc).join(",")).join("\r\n");
}
