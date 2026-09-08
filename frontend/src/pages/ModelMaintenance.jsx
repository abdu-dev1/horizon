import { useState } from "react";
import {
  AlertTriangle,
  CalendarClock,
  CheckCircle2,
  FileSpreadsheet,
  ListChecks,
  RefreshCw,
  Sparkles,
  Wrench,
  X,
} from "lucide-react";
import { UploadPanel } from "../components/UploadPanel.jsx";

// Essentials shown on the cards (the rest live in the "all fields" popup).
const UPCOMING_KEY = [
  ["Group name (+ ID)", "Acme Manufacturing LLC", "Who is renewing. A stable ID is ideal for matching."],
  ["Renewal (effective) date", "2026-10-01", "Decides if it falls in the 2-quarter window."],
  ["Product", "HPS Level Funded", "Level Funded or Self Funded."],
  ["Enrolled lives / premium", "84 / 612,000", "Size and dollar exposure."],
];

// The COMPLETE field list the model uses, grouped. Same fields feed both flows;
// Feed 2 (past renewals) just adds the outcome at the end.
const ALL_FIELDS = [
  ["Identity — required to score", [
    ["Group name (+ ID)", "Acme Manufacturing LLC / CP1042", "Who it is; a stable ID is best for matching across files."],
    ["Renewal (effective) date", "2026-10-01", "When the renewal takes effect."],
    ["Product", "HPS Level Funded", "Level Funded or Self Funded."],
  ]],
  ["Size & premium", [
    ["Enrolled lives", "84", "Current employee count."],
    ["Annual premium", "612,000", "Annualized premium."],
  ]],
  ["Underwriting economics — the strongest predictors", [
    ["Net loss ratio (w/ rebates)", "0.62", "Claims experience — a core driver."],
    ["ISL loss ratio", "0.55", "Individual stop-loss loss ratio."],
    ["Mature claims to attachment", "1.08", "Claims maturity vs the funding level."],
    ["Total renewal increase %", "0.24", "The #1 predictor — final increase incl. lasers."],
    ["Initial UW increase %", "0.19", "Where underwriting opened, pre-negotiation."],
    ["Fixed-cost increase %", "0.12", "Fixed-cost portion of the increase."],
  ]],
  ["Lasers", [
    ["# lasers (current)", "1", "Lasers in the expiring year."],
    ["# lasers (renewal)", "2", "Lasers proposed at renewal."],
    ["Laser liability $", "45,000", "Dollar exposure above the ISL deductible."],
  ]],
  ["History & relationship", [
    ["Years with Crumdale (tenure)", "5", "How long the group has been a client."],
    ["BOR change? (Y/N)", "N", "Did the broker of record change this year?"],
    ["Captive offer (Y/N)", "N", "Was a captive arrangement offered?"],
  ]],
  ["Team & geography", [
    ["Broker", "USI – Cincinnati", "Broker of record."],
    ["RSD", "Chris", "Regional sales director."],
    ["AM", "Alyssa", "Account manager."],
    ["Carrier", "Zurich", "Stop-loss carrier."],
    ["State", "OH", "Group's state."],
  ]],
  ["Broker relationship (broker-level)", [
    ["Broker years w/ CS", "6", "Broker's tenure with Crumdale."],
    ["Broker groups w/ CS", "18", "Broker's book size with Crumdale."],
    ["Broker products sold", "3", "Products the broker sells with CS."],
    ["Preferred broker (Y/N)", "Y", "Preferred partner."],
  ]],
  ["Outcome — Feed 2 (past renewals) ONLY", [
    ["Renewed / Termed", "Renewed", "What the group actually did. The label the model learns from — never sent for upcoming renewals."],
  ]],
];

export default function ModelMaintenance({ onDataChange }) {
  const [showFields, setShowFields] = useState(false);

  return (
    <>
      <div className="card guide-hero">
        <div className="guide-hero-icon"><Wrench size={22} strokeWidth={2.2} /></div>
        <div>
          <div className="card-title" style={{ fontSize: 15 }}>
            The monthly cycle that keeps Horizon accurate
          </div>
          <div className="card-sub" style={{ marginBottom: 0 }}>
            Each month the model is fed two things — the <b>upcoming renewals</b> to forecast, and
            last month's <b>actual outcomes</b> to learn from — then it's retrained. Download the
            template below, fill in one row per renewal, and upload it right here — no files to
            touch, no script to run.
          </div>
        </div>
      </div>

      {/* Warning: no post-decision / "spoiler" data */}
      <div className="mm-warning">
        <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--red)" }}>
          <AlertTriangle size={16} /> Critical — send only what was known <span style={{ textDecoration: "underline" }}>before</span> the decision
        </div>
        <p className="guide-rule-text" style={{ marginBottom: 8 }}>
          Every value you provide must reflect what was known <b>at the time of the renewal quote</b> —
          for example, the loss ratio as of ~90 days out and the quoted increase. <b>Do not include
          final, settled, or after-the-fact figures</b>, and never include the outcome on an upcoming
          renewal.
        </p>
        <p className="guide-rule-text" style={{ marginBottom: 0, color: "var(--text-faint)" }}>
          Feeding the model "spoiler" data — information that only exists <i>after</i> the result is
          known — makes it look highly accurate in testing and then fail on real renewals. The test:
          <b> "Would we have known this the day we quoted it?"</b> If not, leave it blank.
        </p>
      </div>

      {/* The cycle at a glance */}
      <div className="card">
        <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <RefreshCw size={15} color="var(--accent)" /> The monthly cycle at a glance
        </div>
        <div className="steps">
          <Step n="1" title="Collect upcoming renewals">
            The groups due in the next two quarters → these get <b>scored</b> for the forecast.
          </Step>
          <Step n="2" title="Collect last month's outcomes">
            What actually happened to the renewals that were due → these become <b>training labels</b>.
          </Step>
          <Step n="3" title="Import the data">
            Load both feeds into the pipeline (column mapping + history linking).
          </Step>
          <Step n="4" title="Retrain">
            Click <b>Monthly Retrain</b> — the model relearns on the new outcomes and rescores the book.
          </Step>
          <Step n="5" title="Review &amp; act">
            Check the new metrics, then work the save list.
          </Step>
        </div>
      </div>

      {/* The two feeds */}
      <div className="grid grid-2">
        <div className="card">
          <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Sparkles size={15} color="var(--accent)" /> Feed 1 — Upcoming renewals
            <span className="badge badge-Stable" style={{ marginLeft: 6 }}>to forecast</span>
          </div>
          <div className="card-sub">
            Groups due in the window — they get <b>scored</b>. Send <b>every field below</b>; the more
            complete, the sharper the score. <b>No outcome</b> here (that's what we predict).
          </div>
          <FieldTable rows={UPCOMING_KEY} />
          <button className="mm-fieldbtn" onClick={() => setShowFields(true)}>
            <ListChecks size={14} /> View all fields the model uses
          </button>
        </div>

        <div className="card">
          <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <CheckCircle2 size={15} color="var(--green)" /> Feed 2 — Actual outcomes
            <span className="badge badge-Secure" style={{ marginLeft: 6 }}>to learn from</span>
          </div>
          <div className="card-sub">
            Last month's <b>decided</b> renewals. Send the <b>same full field set as Feed 1</b> (as known
            before the decision), <b>plus the outcome</b>:
          </div>
          <FieldTable rows={[["Renewed / Termed", "Renewed", "What the group actually did — the label the model learns from."]]} />
          <button className="mm-fieldbtn" onClick={() => setShowFields(true)}>
            <ListChecks size={14} /> View all fields the model uses
          </button>
        </div>
      </div>

      {/* Upload — one file, one upload, tries both feeds against it */}
      <div className="card">
        <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <RefreshCw size={15} color="var(--accent)" /> Upload your data
        </div>
        <div className="card-sub">
          One file, one upload. Fill in either sheet of the template — or both — and this picks up
          whichever are actually present; you don't need to upload twice.
        </div>
        <UploadPanel onImported={onDataChange} />
      </div>

      {/* Step by step */}
      <div className="card">
        <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <CalendarClock size={15} /> The monthly run — step by step
        </div>
        <div className="steps">
          <Step n="1" title="Fill in the template">
            Download the template above, add one row per renewal to the matching sheet
            (<b>Upcoming Renewals</b> or <b>Past Outcomes</b>).
          </Step>
          <Step n="2" title="Upload &amp; import">
            Use the <b>Upload</b> panel above — it previews exactly what will change (rows matched,
            anything unrecognized, any values it had to interpret) before you commit anything.
          </Step>
          <Step n="3" title="Retrain">
            Click <b>Monthly Retrain</b> (~10–12 min). New outcomes become training data; the upcoming
            renewals are rescored into Upcoming Renewals.
          </Step>
          <Step n="4" title="Verify">
            On <b>Model Performance</b>, confirm AUC / accuracy held and calibration still tracks. On
            <b> Data Quality</b>, confirm completeness held.
          </Step>
          <Step n="5" title="Act">
            Work the <b>Upcoming Renewals</b> save list, top-down by expected lost premium.
          </Step>
        </div>
      </div>

      {/* Format + coming soon */}
      <div className="card">
        <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <FileSpreadsheet size={15} /> Format &amp; what's coming
        </div>
        <ul className="guide-list">
          <li><b>File type:</b> the template (.xlsx) or a CSV/Excel with the same headers — common
            header variants are recognized, and anything unrecognized is called out before you import.</li>
          <li><b>One row per renewal</b> (per group, per year).</li>
          <li><b>Cadence:</b> monthly — the model retrains in minutes.</li>
          <li>
            <b>Re-uploading the same group</b> for the same renewal date updates its row instead of
            creating a duplicate, so sending a corrected file is always safe.
          </li>
        </ul>
      </div>

      {showFields && <FieldsModal onClose={() => setShowFields(false)} />}
    </>
  );
}

function FieldsModal({ onClose }) {
  return (
    <div className="mm-modal-overlay" onClick={onClose}>
      <div className="mm-modal" onClick={(e) => e.stopPropagation()}>
        <button className="mm-modal-close" onClick={onClose} aria-label="Close"><X size={18} /></button>
        <div className="card-title" style={{ fontSize: 15 }}>All fields Horizon uses</div>
        <div className="card-sub">
          The complete set per renewal. <b>Group name, renewal date and product are the minimum</b> to
          score a group; everything else sharpens the forecast. Send the same fields for upcoming
          renewals and past ones — past renewals just add the <b>outcome</b> at the end.
        </div>
        <div className="mm-modal-warn">
          <AlertTriangle size={13} /> Reminder: only values known <b>before</b> the decision — no settled
          or after-the-fact figures.
        </div>
        {ALL_FIELDS.map(([cat, rows]) => (
          <div key={cat}>
            <div className="mm-cat">{cat}</div>
            <FieldTable rows={rows} />
          </div>
        ))}
      </div>
    </div>
  );
}

function FieldTable({ rows }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>Field</th><th>Example</th><th>What it is</th></tr>
        </thead>
        <tbody>
          {rows.map(([field, example, note]) => (
            <tr key={field} style={{ cursor: "default" }}>
              <td className="cell-main">{field}</td>
              <td className="mono cell-dim">{example}</td>
              <td className="cell-dim" style={{ whiteSpace: "normal" }}>{note}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Step({ n, title, children }) {
  return (
    <div className="step">
      <div className="step-num">{n}</div>
      <div>
        <div className="step-title">{title}</div>
        <div className="step-body">{children}</div>
      </div>
    </div>
  );
}
