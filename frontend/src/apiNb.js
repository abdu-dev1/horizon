// New Business API client. Always calls "/nb-app/api/..." — dev-proxied to
// the standalone NewBusiness backend (see vite.config.js) or, in the combined
// product, resolved by the top-level gateway mounting that same backend
// unmodified at "/nb-app". Deliberately a separate client from api.js, not a
// shared one with a "product" param — the two backends are fully isolated
// projects (see NewBusiness/README.md) and this file is the one place that
// isolation could accidentally get blurred, so it stays its own module.
const BASE = "/nb-app";

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
  retrain: async () => {
    const res = await fetch(`${BASE}/api/retrain`, { method: "POST" });
    if (!res.ok) throw new Error(`retrain -> ${res.status}`);
    return res.json();
  },
};

export async function loadAllNb() {
  const [health, summary, segments, groups, metrics, performance] = await Promise.all([
    apiNb.health(),
    apiNb.summary(),
    apiNb.segments(),
    apiNb.groups(),
    apiNb.modelMetrics(),
    apiNb.performance(),
  ]);
  return { health, summary, segments, groups: groups.groups, metrics, performance };
}
