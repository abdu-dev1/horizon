import { Scale } from "lucide-react";

// Static reference page -- nothing here is fetched from the API. It documents
// a RULE (etl_nb.py's _load_market_pricing), not live data, so there's
// nothing to load or refresh. Keep this in lockstep with that function's
// docstring and with nbColumns.jsx's two "New Logic: ..." column labels if
// either changes.
export default function NbPricingLogic() {
  return (
    <>
      <div className="card guide-hero">
        <div className="guide-hero-icon"><Scale size={22} strokeWidth={2.2} /></div>
        <div>
          <div className="card-title" style={{ fontSize: 15 }}>
            A pricing-comparison rule layered on top of the data below — not a new dataset
          </div>
          <div className="card-sub" style={{ marginBottom: 0 }}>
            It answers one question: "how does what we quoted compare to what the client pays
            today?" — computed the same way for every quote, decided or still open.
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-title">The rule</div>
        <div className="card-sub" style={{ marginBottom: 0 }}>
          For a given quote, up to three cost numbers can exist:
        </div>
        <ul className="guide-list">
          <li><b>P — Illustrative quote.</b> An early, rough estimate.</li>
          <li><b>Q — Firm quote.</b> A finalized number — better than P whenever it exists.</li>
          <li><b>W — Written (bound) number.</b> The actual final deal — beats everything if it's cheaper.</li>
        </ul>
        <div className="card-sub" style={{ marginTop: 14, marginBottom: 0 }}>Then, in order:</div>
        <ul className="guide-list">
          <li><b>1.</b> Pick Q if it exists; otherwise pick P.</li>
          <li><b>2.</b> If W exists and is cheaper than whichever of P/Q was picked, use W instead.</li>
          <li><b>3.</b> Compare that one winning number to <b>L</b> — what the client currently pays,
            either their Current Cost or their Current Renewal — to get a real
            "% more/less than today" figure.</li>
        </ul>
      </div>

      <div className="card">
        <div className="card-title">Where it's applied</div>
        <div className="card-sub" style={{ marginBottom: 0 }}>
          Run inside <span className="mono">etl_nb.py</span>'s <span className="mono">_load_market_pricing</span>,
          against the <span className="mono">External Market Pricing_All Time</span> workbook —
          New Business quotes only. That workbook lists one row per stop-loss market shopped on a
          quote, each with its own P/Q/W, so the rule runs per market row first, then the cheapest
          result across a client's markets is kept — the number a broker would actually bring back
          to them. Applied to every quote alike, whether it's already decided (Win/Loss Database) or
          still open (Open Pipeline).
        </div>
      </div>

      <div className="card">
        <div className="card-title">Output fields</div>
        <div className="card-sub">
          Exactly two fields come out of this — nothing else is added or changed elsewhere.
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Field</th>
                <th>Shown as</th>
                <th>Where</th>
              </tr>
            </thead>
            <tbody>
              <tr style={{ cursor: "default" }}>
                <td className="mono">pct_vs_current_full</td>
                <td>New Logic: vs. Current Cost</td>
                <td>Open Pipeline, Win/Loss Database, Data Quality</td>
              </tr>
              <tr style={{ cursor: "default" }}>
                <td className="mono">pct_vs_renewal</td>
                <td>New Logic: vs. Current Renewal</td>
                <td>Open Pipeline, Win/Loss Database, Data Quality</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div className="card-note">
          Kept deliberately separate from the existing "% vs. current cost" column, which is a
          different, simpler calculation (always Illustrative ÷ Current Cost, matched to Salesforce's
          own report formula) — the two are shown side by side, never merged into one number.
        </div>
      </div>

      <div className="card">
        <div className="card-title">Coverage &amp; limits</div>
        <div className="card-sub" style={{ marginBottom: 0 }}>
          The market-pricing workbook is almost entirely decided deals, so coverage is real but
          partial: populated on a meaningful share of history, sparse on the open pipeline. A blank
          cell means no market quote was on file for that client — not a computation error, and never
          filled with a guess. For the same reason, neither field is fed to the model as a training
          feature — an open quote needs a value the model can see at scoring time, and this one isn't
          reliably there yet.
        </div>
      </div>
    </>
  );
}
