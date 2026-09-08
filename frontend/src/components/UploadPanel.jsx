import { useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Download, Loader2, Upload, X } from "lucide-react";
import { api } from "../api.js";

const FEED_LABEL = { upcoming: "Upcoming Renewals", outcomes: "Past Outcomes" };

// Upload -> preview -> commit. One file, ONE picker — no "which feed" choice for
// the user to get wrong. The template ships an "Upcoming Renewals" sheet and a
// "Past Outcomes" sheet together, so a single upload tries both
// (upload_feed.parse_workbook) and comes back with a report per sheet that's
// actually present; each is reviewed and committed independently. Nothing is
// written to the pipeline until the user explicitly imports a given report — an
// upload is never applied silently. Shared by Model Maintenance and the
// "Upload" button on Upcoming Renewals.
export function UploadPanel({ onImported }) {
  const inputRef = useRef(null);
  const [filename, setFilename] = useState(null);
  const [busy, setBusy] = useState(false);
  const [reports, setReports] = useState([]);   // pending previews, one per feed found
  const [imported, setImported] = useState([]); // committed results, for the success line
  const [error, setError] = useState(null);
  const [prefill, setPrefill] = useState(false); // pre-fill Past Outcomes with current Upcoming Renewals

  const reset = () => {
    setFilename(null);
    setReports([]);
    setImported([]);
    setError(null);
    if (inputRef.current) inputRef.current.value = "";
  };

  const pickFile = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setFilename(file.name);
    setImported([]);
    setError(null);
    setBusy(true);
    try {
      const found = await api.uploadPreview(file);
      setReports(found);
    } catch (err) {
      setError(err.message);
      setReports([]);
    } finally {
      setBusy(false);
    }
  };

  const commitOne = async (report) => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.uploadCommit(report.token);
      setImported((prev) => [...prev, { ...r, feed: report.feed }]);
      setReports((prev) => prev.filter((x) => x.token !== report.token));
      await onImported?.();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="upload-panel">
      <div className="upload-row">
        <a className="mm-fieldbtn" href={api.uploadTemplateUrl(prefill)}>
          <Download size={14} /> Download template
        </a>
        <label className="upload-prefill-check" title="Adds every group currently in Upcoming Renewals / Needs Data to the Past Outcomes sheet — id, name, and renewal date filled in, so you only need to add the outcome for the ones that decided.">
          <input type="checkbox" checked={prefill} onChange={(e) => setPrefill(e.target.checked)} />
          Pre-fill with current Upcoming Renewals
        </label>
        <label className="mm-fieldbtn upload-pick">
          <Upload size={14} /> {filename ? "Choose a different file" : "Choose file to upload"}
          <input ref={inputRef} type="file" accept=".xlsx,.xls,.csv" onChange={pickFile} hidden />
        </label>
        {filename && <span className="upload-filename">{filename}</span>}
      </div>

      {busy && (
        <div className="upload-status upload-busy">
          <Loader2 size={14} className="uw-spin" /> Working…
        </div>
      )}

      {error && (
        <div className="upload-status upload-error">
          <AlertTriangle size={14} /> {error}
        </div>
      )}

      {reports.map((report) => (
        <UploadPreview key={report.token} report={report} onCommit={() => commitOne(report)} />
      ))}

      {imported.map((r, i) => (
        <div className="upload-status upload-success" key={i}>
          <CheckCircle2 size={14} color="var(--green)" />
          <b>{FEED_LABEL[r.feed] || r.feed}:</b> imported — {r.rows_added} added, {r.rows_updated} updated
          {r.feed === "outcomes" ? " into the training data (retrain to learn from them)"
                                  : " into Upcoming Renewals"}.
        </div>
      ))}

      {(imported.length > 0 && reports.length === 0) && (
        <button className="upload-again" onClick={reset}>Upload another file</button>
      )}
    </div>
  );
}

function UploadPreview({ report, onCommit }) {
  const hasErrors = report.errors?.length > 0;
  return (
    <div className={`upload-preview ${hasErrors ? "upload-preview-bad" : ""}`}>
      <div className="upload-preview-head">
        <b>{FEED_LABEL[report.feed] || report.sheet}</b> ({report.sheet}) · {report.rows_ready} of{" "}
        {report.rows_in_file} row(s) ready
        {report.rows_skipped > 0 && `, ${report.rows_skipped} skipped`}
      </div>

      {hasErrors && (
        <ul className="upload-issue-list upload-issue-errors">
          {report.errors.map((e, i) => <li key={i}><X size={11} /> {e}</li>)}
        </ul>
      )}

      {report.warnings?.length > 0 && (
        <ul className="upload-issue-list upload-issue-warnings">
          {report.warnings.map((w, i) => <li key={i}><AlertTriangle size={11} /> {w}</li>)}
        </ul>
      )}

      {report.unrecognized_columns?.length > 0 && (
        <div className="upload-unrecognized">
          Unrecognized column(s), ignored: {report.unrecognized_columns.join(", ")}
        </div>
      )}

      {report.sample?.length > 0 && (
        <div className="table-wrap upload-sample">
          <table>
            <thead>
              <tr>
                {Object.keys(report.matched_columns).map((k) => <th key={k}>{k}</th>)}
              </tr>
            </thead>
            <tbody>
              {report.sample.map((row, i) => (
                <tr key={i}>
                  {Object.keys(report.matched_columns).map((k) => (
                    <td key={k} className="mono cell-dim">{row[k] == null ? "—" : String(row[k])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="upload-preview-actions">
        <button className="btn btn-primary" onClick={onCommit} disabled={!report.ok}>
          Import {report.rows_ready} row{report.rows_ready === 1 ? "" : "s"} into {FEED_LABEL[report.feed]}
        </button>
      </div>
    </div>
  );
}

// Quick-access upload as a modal — same UploadPanel, reachable without leaving
// whatever page you're on (Upcoming Renewals' "Upload" button uses this). Model
// Maintenance shows it inline instead, with the full field guide and monthly-cycle
// context around it; this is the shortcut version.
export function UploadModal({ onClose, onImported }) {
  return (
    <div className="mm-modal-overlay" onClick={onClose}>
      <div className="mm-modal upload-modal" onClick={(e) => e.stopPropagation()}>
        <button className="mm-modal-close" onClick={onClose} aria-label="Close"><X size={18} /></button>
        <div className="card-title" style={{ fontSize: 15 }}>Upload renewal data</div>
        <div className="card-sub">
          One file, one upload — fill in either or both sheets of the template (upcoming renewals,
          past outcomes) and this picks up whichever are present. Full field guide and the monthly
          workflow live on <b>Model Maintenance</b>.
        </div>
        <UploadPanel onImported={onImported} />
      </div>
    </div>
  );
}
