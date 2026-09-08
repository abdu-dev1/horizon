import { useEffect, useRef, useState } from "react";
import { CheckCircle2, RefreshCw, Upload } from "lucide-react";

/**
 * Admin-only: install a model bundle built on an admin's laptop.
 *
 * Two steps on purpose. Choosing a file only INSPECTS it (server-side
 * validation + manifest read); a second, explicit click promotes it. A model
 * going live should be a decision made while looking at its AUC and row count,
 * not a side effect of opening a file dialog -- this project has already been
 * bitten by a model whose reported and served numbers disagreed, and by a data
 * drop that silently cut training history from 449 wins to 81.
 *
 * `client` is api (renewals) or apiNb (new business) -- the two products are
 * separate projects with separate bundles, so this panel is told which one it
 * is acting on rather than deciding for itself.
 */
export default function PublishPanel({ client, product, onApplied }) {
  const [status, setStatus] = useState(null);
  const [candidate, setCandidate] = useState(null);
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const inputRef = useRef(null);

  const loadStatus = () =>
    client.adminStatus().then(setStatus).catch((e) => setError(e.message));

  useEffect(() => {
    setCandidate(null);
    setFile(null);
    setError(null);
    loadStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [product]);

  const pick = async (e) => {
    const chosen = e.target.files?.[0];
    if (!chosen) return;
    setError(null);
    setCandidate(null);
    setBusy("inspecting");
    try {
      const res = await client.bundleInspect(chosen);
      setCandidate(res.manifest);
      setFile(chosen);
    } catch (err) {
      setError(err.message);
      setFile(null);
    } finally {
      setBusy(null);
      // Allow re-picking the same filename after a failure.
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  const promote = async () => {
    if (!file) return;
    setBusy("applying");
    setError(null);
    try {
      const res = await client.bundleApply(file);
      setCandidate(null);
      setFile(null);
      await loadStatus();
      onApplied?.(res);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(null);
    }
  };

  const m = (o) => o?.metrics ?? {};
  const auc = (o) => {
    const v = m(o).roc_auc ?? m(o).auc;
    return typeof v === "number" ? v.toFixed(3) : "—";
  };

  return (
    <div className="card" style={{ display: "grid", gap: 14 }}>
      <div>
        <div style={{ fontWeight: 700 }}>Published model</div>
        <div style={{ fontSize: 12.5, color: "var(--text-faint)", marginTop: 2 }}>
          Models are trained and reviewed on an admin&apos;s laptop, then installed
          here. Nothing retrains inside this app.
        </div>
      </div>

      <div style={{ display: "grid", gap: 6, fontSize: 13 }}>
        <Row label="Live version" value={status?.model_version ?? "—"} />
        <Row label="Trained" value={status?.trained_at?.slice(0, 19)?.replace("T", " ") ?? "—"} />
        <Row label="AUC" value={auc(status)} />
        <Row
          label={product === "renewals" ? "Groups in window" : "Open quotes"}
          value={status?.groups_in_window ?? status?.open_quotes ?? "—"}
        />
        <Row label="Training rows" value={status?.history_rows ?? "—"} />
        {status?.local_files?.length > 0 && (
          <Row label="Local files kept" value={status.local_files.join(", ")} />
        )}
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <input
          ref={inputRef}
          type="file"
          accept=".zip"
          onChange={pick}
          style={{ display: "none" }}
          id={`bundle-${product}`}
        />
        <label htmlFor={`bundle-${product}`} className="btn" style={{ cursor: "pointer" }}>
          <Upload size={14} /> {busy === "inspecting" ? "Checking…" : "Choose bundle…"}
        </label>
        <button className="btn" onClick={() => client.adminReload().then(loadStatus)}>
          <RefreshCw size={14} /> Reload from disk
        </button>
      </div>

      {error && (
        <div style={{ fontSize: 12.5, color: "var(--red)", whiteSpace: "pre-wrap" }}>
          {error}
        </div>
      )}

      {candidate && (
        <div
          style={{
            border: "1px solid var(--border)",
            borderRadius: 8,
            padding: 12,
            display: "grid",
            gap: 8,
          }}
        >
          <div style={{ fontWeight: 650 }}>Ready to publish</div>
          <div style={{ display: "grid", gap: 5, fontSize: 13 }}>
            <Row label="Version" value={candidate.version ?? "—"} />
            <Row label="Trained" value={candidate.trained_at?.slice(0, 19)?.replace("T", " ") ?? "—"} />
            <Row label="AUC" value={auc(candidate)} />
            <Row label="Training rows" value={candidate.history_rows ?? "—"} />
          </div>
          {/* Deliberately not hidden behind an "advanced" toggle: a large drop
              in training rows is the exact symptom of a filtered data export,
              and it should be visible next to the button that promotes it. */}
          {status?.history_rows != null && candidate.history_rows != null && (
            <div
              style={{
                fontSize: 12.5,
                color:
                  candidate.history_rows < status.history_rows * 0.9
                    ? "var(--red)"
                    : "var(--text-faint)",
              }}
            >
              {candidate.history_rows < status.history_rows * 0.9
                ? `Warning: ${status.history_rows - candidate.history_rows} fewer training rows than what is live. ` +
                  `A filtered or partial data export looks exactly like this — check before publishing.`
                : `${candidate.history_rows - status.history_rows >= 0 ? "+" : ""}${
                    candidate.history_rows - status.history_rows
                  } training rows vs. live.`}
            </div>
          )}
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn-primary" onClick={promote} disabled={busy === "applying"}>
              <CheckCircle2 size={14} />
              {busy === "applying" ? "Publishing…" : "Publish this model"}
            </button>
            <button
              className="btn"
              onClick={() => {
                setCandidate(null);
                setFile(null);
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function Row({ label, value }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
      <span style={{ color: "var(--text-faint)" }}>{label}</span>
      <span className="mono" style={{ textAlign: "right", wordBreak: "break-all" }}>
        {String(value)}
      </span>
    </div>
  );
}
