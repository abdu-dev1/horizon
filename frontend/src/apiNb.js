// New Business API client. Always calls "/nb-app/api/..." — dev-proxied to
// the standalone NewBusiness backend (see vite.config.js) or, in the combined
// product, resolved by the top-level gateway mounting that same backend
// unmodified at "/nb-app". Deliberately a separate client from api.js, not a
// shared one with a "product" param — the two backends are fully isolated
// projects (see NewBusiness/README.md) and this file is the one place that
// isolation could accidentally get blurred, so it stays its own module.
const BASE = "/nb-app";

// Streams a bundle upload; surfaces the server's validation detail verbatim
// (see the twin in api.js).
async function postFile(path, file) {
  const body = new FormData();
  body.append("file", file);
  const res = await fetch(`${BASE}${path}`, { method: "POST", body });
  const json = await res.json().catch(() => null);
  if (!res.ok) throw new Error(json?.detail ?? `${path} -> ${res.status}`);
  return json;
}

async function get(path) {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

export const apiNb = {
  health: () => get("/api/health"),
  summary: () => get("/api/summary"),
  segments: () => get("/api/segments"),
  groups: () => get("/api/groups"),
  group: (quote_id) => get(`/api/groups/${quote_id}`),
  history: () => get("/api/history"),
  dataQuality: () => get("/api/data-quality"),
  performance: () => get("/api/performance"),
  modelMetrics: () => get("/api/model/metrics"),
  stageOptions: () => get("/api/stage-options"),
  updateStage: async (quote_id, stage) => {
    const res = await fetch(`${BASE}/api/groups/${quote_id}/stage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stage }),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `update stage -> ${res.status}`);
    return res.json();
  },
  // See the matching note in api.js: in-app retraining is gone, replaced by
  // installing a bundle built and reviewed on an admin's laptop.
  adminStatus: () => get("/api/admin/status"),
  bundleInspect: (file) => postFile("/api/admin/bundle/inspect", file),
  bundleApply: (file) => postFile("/api/admin/bundle/apply", file),
  adminReload: async () => {
    const res = await fetch(`${BASE}/api/admin/reload`, { method: "POST" });
    if (!res.ok) throw new Error(`reload -> ${res.status}`);
    return res.json();
  },
  // Drop in a raw RSD Scorecard Export (or External Market Pricing workbook)
  // instead of copying it into NewBusiness/ by hand and running etl_nb.py
  // yourself. Preview reports what would change; apply commits it and
  // rescores the pipeline. Neither retrains -- see the matching note on
  // /api/retrain in the backend.
  scorecardPreview: (file) => postFile("/api/upload/scorecard/preview", file),
  scorecardApply: async (token) => {
    const res = await fetch(`${BASE}/api/upload/scorecard/apply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    const json = await res.json().catch(() => null);
    if (!res.ok) throw new Error(json?.detail ?? `apply -> ${res.status}`);
    return json;
  },
};

export async function loadAllNb(isAdmin = false) {
  const [health, summary, segments, groups, performance] = await Promise.all([
    apiNb.health(),
    apiNb.summary(),
    apiNb.segments(),
    apiNb.groups(),
    apiNb.performance(),
  ]);

  // modelMetrics is admin-only at the gateway (403). Kept out of the batch
  // above for the same reason as api.js's loadAll: a single 403 would reject
  // the whole Promise.all and blank the New Business dashboard for every
  // non-admin. Only NbModel (an admin page) reads it.
  const metrics = isAdmin ? await apiNb.modelMetrics() : null;

  return { health, summary, segments, groups: groups.groups, metrics, performance };
}
