const BASE = import.meta.env.VITE_API_BASE ?? "";

async function get(path) {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

export const api = {
  health: () => get("/api/health"),
  summary: () => get("/api/summary"),
  forecast: () => get("/api/forecast"),
  segments: () => get("/api/segments"),
  groups: () => get("/api/groups"),
  exportBookUrl: () => `${BASE}/api/export/book`,
  modelMetrics: () => get("/api/model/metrics"),
  modelImportance: () => get("/api/model/importance"),
  modelDiagnostics: () => get("/api/model/diagnostics"),
  dataQuality: () => get("/api/data-quality"),
  renewalDatabase: () => get("/api/renewal-database"),
  recommendations: () => get("/api/recommendations"),
  recommendation: (id) => get(`/api/recommendations/${id}`),
  retrain: async () => {
    const res = await fetch(`${BASE}/api/retrain`, { method: "POST" });
    if (!res.ok) throw new Error(`retrain -> ${res.status}`);
    return res.json();
  },
  moveDecidedToDatabase: async () => {
    const res = await fetch(`${BASE}/api/groups/move-decided-to-database`, { method: "POST" });
    if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail || `move-decided-to-database -> ${res.status}`);
    return res.json();
  },
  deleteGroup: async (group_id) => {
    const res = await fetch(`${BASE}/api/groups/${group_id}`, { method: "DELETE" });
    const json = await res.json().catch(() => null);
    if (!res.ok) throw new Error(json?.detail || `delete group -> ${res.status}`);
    return json;
  },
  updateUnderwriting: async (group_id, fields) => {
    const res = await fetch(`${BASE}/api/groups/${group_id}/underwriting`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fields }),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail || `underwriting -> ${res.status}`);
    return res.json();
  },
  uploadTemplateUrl: (includeUpcoming) =>
    `${BASE}/api/upload/template${includeUpcoming ? "?include_upcoming=true" : ""}`,
  // Tries BOTH feeds against the one file — a template with both sheets filled in
  // comes back as two reports. Returns { reports: [{ token, feed, ok, ... }, ...] }.
  uploadPreview: async (file) => {
    const body = new FormData();
    body.append("file", file);
    const res = await fetch(`${BASE}/api/upload/preview`, { method: "POST", body });
    const json = await res.json().catch(() => null);
    if (!res.ok) throw new Error(json?.detail || `upload/preview -> ${res.status}`);
    return json.reports;
  },
  uploadCommit: async (token) => {
    const res = await fetch(`${BASE}/api/upload/commit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    const json = await res.json().catch(() => null);
    if (!res.ok) throw new Error(json?.detail || `upload/commit -> ${res.status}`);
    return json;
  },
};

export async function loadAll() {
  // recommendations are computed lazily (they re-score every at-risk group and
  // can take a while) — fetched separately so the dashboard paints instantly.
  const [health, summary, forecast, segments, groups, metrics, importance, diagnostics] =
    await Promise.all([
      api.health(),
      api.summary(),
      api.forecast(),
      api.segments(),
      api.groups(),
      api.modelMetrics(),
      api.modelImportance(),
      api.modelDiagnostics(),
    ]);
  return {
    health, summary, forecast, segments, groups: groups.groups,
    metrics, importance, diagnostics,
  };
}
