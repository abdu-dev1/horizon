import { useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Upload } from "lucide-react";
import { apiNb } from "../apiNb.js";

/**
 * Admin-only, New Business: drop in a raw RSD Scorecard Export (or External
 * Market Pricing workbook) instead of copying it into NewBusiness/ by hand
 * and running etl_nb.py yourself.
 *
 * Same two-step shape as PublishPanel above it, and for the same reason:
 * choosing a file only PREVIEWS what it would change (new decided outcomes,
 * new open quotes, any row-count red flag); a second, explicit click commits
 * it. This project has already been bitten once by a data drop that arrived
 * silently pre-filtered (see DEPLOYMENT_PLAN.md) -- a preview an admin
 * actually looks at is what catches the next one before it goes live.
 *
 * Detection is by SHEET NAME, not filename -- upload whatever the export is
 * actually called, renamed or not.
 *
 * Does NOT retrain. New rows become training data on the next explicit
 * retrain, same as every other import path in this app.
 */
export default function ScorecardUploadPanel({ onApplied }) {
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const [applied, setApplied] = useState(null);
  const inputRef = useRef(null);

  const pick = async (e) => {
    const chosen = e.target.files?.[0];
    if (!chosen) return;
    setError(null);
    setApplied(null);
    setPreview(null);
    setBusy("checking");
    try {
      const res = await apiNb.scorecardPreview(chosen);
      setPreview(res);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(null);
      // Allow re-picking the same filename after a failure.
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  const confirm = async () => {
    if (!preview) return;
    setBusy("applying");
    setError(null);
    try {
      const res = await apiNb.scorecardApply(preview.token);
      setApplied(res);
      setPreview(null);
      onApplied?.(res);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="card" style={{ display: "grid", gap: 14 }}>
      <div>
        <div style={{ fontWeight: 700 }}>Upload a scorecard export</div>
        <div style={{ fontSize: 12.5, color: "var(--text-faint)", marginTop: 2 }}>
          Upload the RSD Scorecard Export (or External Market Pricing) workbook
          here instead of dropping it into the NewBusiness folder by hand. It's
          recognized by its sheet name, not its filename.
        </div>
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <input
          ref={inputRef}
          type="file"
          accept=".xlsx,.xls"
          onChange={pick}
          style={{ display: "none" }}
          id="scorecard-upload"
        />
        <label htmlFor="scorecard-upload" className="btn" style={{ cursor: "pointer" }}>
          <Upload size={14} /> {busy === "checking" ? "Checking…" : "Choose file…"}
        </label>
      </div>

      {error && (
        <div style={{ fontSize: 12.5, color: "var(--red)", whiteSpace: "pre-wrap" }}>
          {error}
        </div>
      )}

      {applied && !preview && (
        <div style={{ fontSize: 12.5, color: "var(--text-faint)" }}>
          Applied — saved as <span className="mono">{applied.saved_as}</span>.
          {applied.history_rows != null && (
            <> Win/Loss Database: {applied.history_rows} rows. Open Pipeline: {applied.pipeline_rows} rows.</>
          )}
        </div>
      )}

      {preview && (
        <div
          style={{
            border: "1px solid var(--border)",
            borderRadius: 8,
            padding: 12,
            display: "grid",
            gap: 8,
          }}
        >
          <div style={{ fontWeight: 650 }}>
            {preview.kind === "pricing" ? "External Market Pricing file" : "RSD Scorecard Export"}
            {" — "}
            <span className="mono" style={{ fontWeight: 500 }}>{preview.filename}</span>
          </div>

          {preview.kind === "pricing" ? (
            <div style={{ fontSize: 13 }}>
              Covers pricing for {preview.opportunities_priced} opportunit
              {preview.opportunities_priced === 1 ? "y" : "ies"}. Fills gaps in cost
              fields only — it doesn't add rows to the Win/Loss Database or Open
              Pipeline.
            </div>
          ) : (
            <div style={{ display: "grid", gap: 5, fontSize: 13 }}>
              <Row label="New decided outcomes (Win/Loss Database)" value={`+${preview.new_decided_outcomes}`} />
              <Row label="New open quotes (Open Pipeline)" value={`+${preview.new_open_quotes}`} />
              <Row label="Win/Loss Database" value={`${preview.total_history_before} → ${preview.total_history_after}`} />
              <Row label="Open Pipeline" value={`${preview.total_pipeline_before} → ${preview.total_pipeline_after}`} />
            </div>
          )}

          {preview.warning && (
            <div style={{ fontSize: 12.5, color: "var(--red)", display: "flex", gap: 6, alignItems: "flex-start" }}>
              <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: 1 }} />
              <span>{preview.warning}</span>
            </div>
          )}

          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn-primary" onClick={confirm} disabled={busy === "applying"}>
              <CheckCircle2 size={14} />
              {busy === "applying" ? "Applying…" : "Add to the app"}
            </button>
            <button className="btn" onClick={() => setPreview(null)}>
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
