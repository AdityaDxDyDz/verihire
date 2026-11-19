// API client. Every backend call goes through `call()`, which:
//   * adds the bearer token,
//   * turns the error envelope {"error": {"code", "message"}} into an ApiError,
//   * on 401 asks `onUnauthorized` to refresh the session once and retries,
//   * never throws raw network errors at the UI.
// The transport is injectable, so the same client runs against the real server (fetchTransport) or the in-browser
// mock (mock.js) with `?mock=1`.

export const API_PREFIX = "/api/v1";

export class ApiError extends Error {
  constructor(status, code, message, { retryAfter = null } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryAfter = retryAfter;
  }
  get isNetwork() {
    return this.status === 0;
  }
}

function queryString(query) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v === undefined || v === null || v === "") continue;
    p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

/** Real transport: fetch for JSON requests, XMLHttpRequest for uploads (fetch cannot report upload progress). */
export function fetchTransport({ baseUrl = "", fetchImpl, XHR } = {}) {
  const doFetch = fetchImpl ?? ((...a) => globalThis.fetch(...a));
  return {
    request({ method, path, query, body, headers, signal }) {
      return doFetch(`${baseUrl}${API_PREFIX}${path}${queryString(query)}`, { method, headers, body, signal });
    },
    upload({ method = "POST", path, form, headers, onProgress }) {
      const Xhr = XHR ?? globalThis.XMLHttpRequest;
      return new Promise((resolve, reject) => {
        const xhr = new Xhr();
        xhr.open(method, `${baseUrl}${API_PREFIX}${path}`);
        for (const [k, v] of Object.entries(headers ?? {})) xhr.setRequestHeader(k, v);
        if (xhr.upload) {
          xhr.upload.onprogress = (e) => {
            if (e.lengthComputable && onProgress) onProgress(e.loaded, e.total);
          };
        }
        xhr.onload = () =>
          resolve(
            new Response(xhr.status === 204 ? null : xhr.responseText, {
              status: xhr.status,
              headers: { "content-type": xhr.getResponseHeader("content-type") || "application/json", "retry-after": xhr.getResponseHeader("retry-after") || "" },
            }),
          );
        xhr.onerror = () => reject(new TypeError("network error"));
        xhr.ontimeout = () => reject(new TypeError("timeout"));
        xhr.send(form);
      });
    },
  };
}

async function toApiError(res) {
  let code = "http_error";
  let message = `Request failed (${res.status}).`;
  try {
    const text = await res.text();
    const data = text ? JSON.parse(text) : null;
    if (data?.error?.message) {
      code = data.error.code ?? code;
      message = data.error.message;
    } else if (typeof data?.detail === "string") {
      message = data.detail;
    }
  } catch { /* non-JSON error body */ }
  const ra = Number(res.headers?.get?.("retry-after"));
  return new ApiError(res.status, code, message, { retryAfter: Number.isFinite(ra) && ra > 0 ? ra : null });
}

export function createApi({ transport, getToken, onUnauthorized }) {
  async function call(method, path, { query, json, form, responseType = "json", onProgress, signal } = {}) {
    const attempt = async () => {
      const token = await getToken();
      const headers = { Accept: responseType === "blob" ? "*/*" : "application/json" };
      if (token) headers.Authorization = `Bearer ${token}`;
      let body;
      if (json !== undefined) {
        headers["Content-Type"] = "application/json";
        body = JSON.stringify(json);
      }
      try {
        return form
          ? await transport.upload({ method, path, form, headers, onProgress })
          : await transport.request({ method, path, query, body, headers, signal });
      } catch (err) {
        if (err?.name === "AbortError") throw err;
        throw new ApiError(0, "network", "Cannot reach the server. Check your connection and try again.");
      }
    };
    let res = await attempt();
    if (res.status === 401 && onUnauthorized && (await onUnauthorized())) res = await attempt();
    if (!res.ok) throw await toApiError(res);
    if (res.status === 204) return null;
    if (responseType === "blob") return res.blob();
    const text = await res.text();
    return text ? JSON.parse(text) : null;
  }

  const enc = encodeURIComponent;
  return {
    call,
    health: () => call("GET", "/health"),
    me: () => call("GET", "/me"),

    uploadDocuments(files, { docType = "resume", onProgress } = {}) {
      const form = new FormData();
      form.append("doc_type", docType);
      for (const f of files) form.append("files", f, f.name);
      return call("POST", "/documents/upload", { form, onProgress });
    },
    retryDocuments: (ids) => call("POST", "/documents/retry", { json: { document_ids: ids } }),
    getJob: (id) => call("GET", `/jobs/${enc(id)}`),
    listDocuments: (params = {}) => call("GET", "/documents", { query: params }),
    pdfUrl: (docId) => call("GET", `/documents/${enc(docId)}/pdf-url`),
    deleteDocument: (docId) => call("DELETE", `/documents/${enc(docId)}`),

    createJD: (text, title) => call("POST", "/job-descriptions", { json: { title: title || null, text } }),
    uploadJD: (file, title) => {
      const form = new FormData();
      form.append("file", file, file.name);
      if (title) form.append("title", title);
      return call("POST", "/job-descriptions/upload", { form });
    },
    // backend returns {items: [...]} (older fixtures were a bare array): the views always get an array
    listJDs: async () => { const r = await call("GET", "/job-descriptions"); return Array.isArray(r) ? r : (r?.items ?? []); },
    getJD: (id) => call("GET", `/job-descriptions/${enc(id)}`),
    updateJD: (id, title, spec) => call("PUT", `/job-descriptions/${enc(id)}`, { json: { title: title || null, spec } }),

    search: (body) => call("POST", "/search", { json: body }),
    getSearch: (id) => call("GET", `/search/${enc(id)}`),
    exportCsv: (id) => call("GET", `/search/${enc(id)}/export.csv`, { responseType: "blob" }),

    analyze: (body) => call("POST", "/analysis", { json: body }),
    getCandidate: (id, reveal = false) => call("GET", `/candidates/${enc(id)}`, { query: { reveal: reveal ? "true" : undefined } }),
    feedback: (body) => call("POST", "/feedback", { json: body }),
  };
}
