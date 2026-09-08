import { useState } from "react";
import { Award, Crosshair, Gauge, RefreshCw } from "lucide-react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { apiNb } from "../../apiNb.js";
import { NB_BAND_COLORS, fmtNum, fmtPct } from "../../format.js";
import { KpiCard, ProbCell, axisStyle, tooltipStyle } from "../../components/shared.jsx";

// Mirrors insights_nb.BAND_ORDER.
const BAND_ORDER = ["High", "Moderate", "Low", "Very Low"];

export default function NbModel({ data, onDataChange }) {
  const { metrics } = data;
  const m = metrics.metrics;
  const reliability = metrics.band_reliability;
  const [toast, setToast] = useState(null);

  const historyData = (metrics.history ?? []).map((h) => ({
    version: h.version.slice(-6),
    "ROC AUC": h.metrics?.roc_auc,
    "PR AUC": h.metrics?.pr_auc,
  }));

  // No retrain handler here any more: training moved off the server entirely
  // (see api.js / DEPLOYMENT_PLAN.md). Installing a reviewed model is the
  // PublishPanel above this card.

  return (
    <>
      <div className="grid grid-kpi">
        <KpiCard
          icon={Crosshair}
          label="ROC AUC (5-fold CV)"
          value={m.roc_auc.toFixed(3)}
          foot="ranks a real win above a real loss this often, on features an open quote actually has"
        />
        <KpiCard
          icon={Gauge}
          label="PR AUC (5-fold CV)"
          value={m.pr_auc.toFixed(3)}
          tone="accent"
          foot={`vs. a ${fmtPct(m.base_rate, 1)} base win rate`}
        />
        <KpiCard
          icon={Award}
          label="Training Set"
          value={fmtNum(m.n)}
          foot={`${fmtNum(m.n_won)} won / ${fmtNum(m.n - m.n_won)} lost, decided quotes only`}
        />
      </div>

      <div className="card">
        <div className="card-title" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span>Model Version History</span>
        </div>
        <div className="card-sub">
          Ensemble of Random Forest + Extra Trees + HistGradientBoosting (soft-voting, calibrated) —
          honest metrics from stratified 5-fold cross-validation, never scored on data used to fit
          that fold.
        </div>
        <div className="card-note" style={{ marginTop: 0, marginBottom: 16, borderTop: "none", paddingTop: 0 }}>
          <b>Versions before 20260826 are not comparable to later ones.</b> They scored ROC ≈ 0.94
          using the illustrative/firm quote costs, ISL deductible, UW turnaround days and assigned
          underwriter — fields that are well populated on decided history and essentially absent
          (0–4%) on an open quote. Measured under real scoring conditions that model ran at
          ROC 0.736 / PR 0.203. The current model trains only on fields an open quote actually
          carries, so its lower headline number is the one that holds in production — and it beats
          the old model's real behaviour by +0.07 ROC / +0.05 PR. See
          <span className="mono"> NewBusiness/backend/experiment_nb.py</span>.
        </div>
        {historyData.length > 1 && (
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={historyData} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
              <CartesianGrid stroke="#16223a" vertical={false} />
              <XAxis dataKey="version" tick={axisStyle} axisLine={false} tickLine={false} />
              <YAxis tick={axisStyle} axisLine={false} tickLine={false} domain={[0, 1]} />
              <Tooltip contentStyle={tooltipStyle} />
              <Bar dataKey="ROC AUC" radius={[6, 6, 0, 0]} fill="#38bdf8" maxBarSize={40} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>

      {reliability && (
        <div className="card">
          <div className="card-title">Band Reliability — is the number honest?</div>
          <div className="card-sub">
            The dashboard headlines each band's <b>measured historical win rate</b>, not a raw
            per-quote model score — with a win rate this low there aren't enough wins to support a
            fine-grained per-quote number (checked directly: quotes the model scored 70%+
            individually had closed only about 1 in 3 times). This table is that check, and it's
            why the per-quote score is a secondary field rather than the headline. Every rate below
            is out-of-fold: the model never saw those rows when it scored them.
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Likelihood</th>
                  <th>Quotes (n)</th>
                  <th>Actual Wins</th>
                  <th>Measured Win Rate</th>
                </tr>
              </thead>
              <tbody>
                {BAND_ORDER.map((t) => {
                  const r = reliability[t];
                  if (!r) return null;
                  return (
                    <tr key={t} style={{ cursor: "default" }}>
                      <td><span className={`badge badge-${t.replace(/\s+/g, "")}`}>{t}</span></td>
                      <td className="mono">{fmtNum(r.n)}</td>
                      <td className="mono">{fmtNum(r.wins)}</td>
                      <td>
                        <ProbCell p={r.rate} showTag={false} colorFn={() => NB_BAND_COLORS[t]} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {toast && (
        <div className="toast">{toast}</div>
      )}
    </>
  );
}
