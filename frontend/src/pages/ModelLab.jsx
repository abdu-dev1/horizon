import { Award, Crosshair, Gauge, Layers } from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fmtNum, fmtPct } from "../format.js";
import { KpiCard, Legend, axisStyle, tooltipStyle } from "../components/shared.jsx";

export default function ModelLab({ data }) {
  const { metrics, importance, diagnostics } = data;
  const m = metrics.metrics;

  // Leak-free pooled walk-forward is the number to trust: every prediction was made by
  // a model that never saw that renewal. The single 75/25 holdout (m.*) is noisier and
  // understates the model, so headline the walk-forward whenever it's available.
  const wf = metrics.wf_metrics;
  const head = wf
    ? {
        auc: wf.auc,
        accuracy: wf.accuracy,
        brier: wf.brier,
        baseline: Math.max(wf.renewal_rate, 1 - wf.renewal_rate),
        aucFoot: `pooled leak-free — ${wf.n} renewals across ${wf.n_months} months`,
        testFoot: `${wf.n} predictions scored leak-free (walk-forward)`,
        aucLabel: "ROC AUC (walk-forward)",
        rocSub: `Lapse detection on the leak-free walk-forward (AUC ${wf.auc.toFixed(3)})`,
        calSub:
          "Pooled over every leak-free monthly prediction. Points on the diagonal = trustworthy probabilities you can roll into a premium forecast.",
      }
    : {
        auc: m.auc,
        accuracy: m.accuracy,
        brier: m.brier,
        baseline: m.naive_baseline ?? m.base_renewal_rate,
        aucFoot: `5-fold CV ${m.cv_auc_mean.toFixed(3)} ± ${m.cv_auc_std.toFixed(3)}`,
        testFoot: `tested on ${fmtNum(m.n_test)} most recent decisions`,
        aucLabel: "ROC AUC (out-of-time)",
        rocSub: `Lapse detection power on the out-of-time holdout (AUC ${m.auc.toFixed(3)})`,
        calSub:
          "When the model says 80%, do 80% actually renew? Points on the diagonal = trustworthy probabilities you can roll into a premium forecast.",
      };

  const monthly = metrics.history.filter((h) => h.kind === "monthly_backfill");
  const historyData = monthly.map((h) => ({
    version: h.version.replace("v", ""),
    AUC: h.auc,
    Accuracy: h.accuracy,
    trained_on: h.n_train,
  }));

  const impData = importance.importances.slice(0, 14).map((r) => ({
    label: r.label,
    importance: +(r.importance * 100).toFixed(2),
  }));

  // Prefer the pooled walk-forward calibration/ROC (more data, honest); fall back to
  // the holdout diagnostics for older models trained before wf_* keys existed.
  const calSource =
    diagnostics.wf_calibration?.length ? diagnostics.wf_calibration : diagnostics.calibration;
  const calData = calSource.map((c) => ({
    predicted: +(c.predicted * 100).toFixed(1),
    actual: +(c.actual * 100).toFixed(1),
    count: c.count,
  }));

  const rocSource =
    diagnostics.wf_roc?.fpr?.length ? diagnostics.wf_roc : diagnostics.roc;
  const rocData = (rocSource.fpr ?? []).map((f, i) => ({
    fpr: +(f * 100).toFixed(1),
    tpr: +(rocSource.tpr[i] * 100).toFixed(1),
  }));

  return (
    <>
      <div className="grid grid-kpi">
        <KpiCard
          icon={Award}
          label={head.aucLabel}
          value={head.auc.toFixed(3)}
          tone="accent"
          foot={head.aucFoot}
        />
        <KpiCard
          icon={Gauge}
          label="Accuracy"
          value={fmtPct(head.accuracy)}
          tone="green"
          foot={`+${Math.round((head.accuracy - head.baseline) * 100)} pts better than no model`}
        />
        <KpiCard
          icon={Crosshair}
          label="Brier Score"
          value={head.brier.toFixed(3)}
          foot="lower is better — probability quality"
        />
        <KpiCard
          icon={Layers}
          label="Training Data"
          value={fmtNum(m.n_train)}
          foot={head.testFoot}
        />
      </div>

      <div className="card">
        <div className="card-title">Predictive Quality, Month Over Month</div>
        <div className="card-sub">
          Walk-forward validation: each point is a model trained on all history available that
          month, scored on the renewals actually decided in that month — the dynamic retraining
          cadence in action
        </div>
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={historyData} margin={{ top: 10, right: 14, left: -14, bottom: 0 }}>
            <CartesianGrid stroke="#16223a" vertical={false} />
            <XAxis dataKey="version" tick={axisStyle} axisLine={false} tickLine={false} />
            <YAxis tick={axisStyle} axisLine={false} tickLine={false} domain={[0.5, 1]} />
            <Tooltip
              contentStyle={tooltipStyle}
              formatter={(v, name) => [v == null ? "n/a" : v.toFixed(3), name]}
              labelFormatter={(l, payload) =>
                `${l} — trained on ${fmtNum(payload?.[0]?.payload?.trained_on ?? 0)} decisions`
              }
            />
            <Line dataKey="AUC" stroke="#38bdf8" strokeWidth={2.5} connectNulls dot={{ r: 4, fill: "#38bdf8", strokeWidth: 0 }} />
            <Line dataKey="Accuracy" stroke="#818cf8" strokeWidth={2.5} connectNulls dot={{ r: 4, fill: "#818cf8", strokeWidth: 0 }} />
          </LineChart>
        </ResponsiveContainer>
        <Legend items={[["AUC", "#38bdf8"], ["Accuracy", "#818cf8"]]} />
      </div>

      <div className="grid grid-3-2">
        <div className="card">
          <div className="card-title">What Drives Renewal — Permutation Importance</div>
          <div className="card-sub">
            AUC lost when each factor is shuffled, on held-out data. These are the factors from
            the kickoff email, ranked by measured predictive power.
          </div>
          <ResponsiveContainer width="100%" height={Math.max(300, impData.length * 28)}>
            <BarChart data={impData} layout="vertical" margin={{ top: 0, right: 18, left: 64, bottom: 0 }}>
              <CartesianGrid stroke="#16223a" horizontal={false} />
              <XAxis type="number" tick={axisStyle} axisLine={false} tickLine={false} tickFormatter={(v) => `${v}`} />
              <YAxis
                type="category"
                dataKey="label"
                tick={{ ...axisStyle, fontSize: 11.5, fill: "#8a9bb8" }}
                width={150}
                axisLine={false}
                tickLine={false}
              />
              <Tooltip contentStyle={tooltipStyle} formatter={(v) => [`${v} pts AUC impact`, "Importance"]} />
              <Bar dataKey="importance" radius={[0, 6, 6, 0]} fill="#38bdf8" maxBarSize={16} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="card">
            <div className="card-title">Calibration</div>
            <div className="card-sub">{head.calSub}</div>
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={calData} margin={{ top: 8, right: 14, left: -16, bottom: 0 }}>
                <CartesianGrid stroke="#16223a" />
                <XAxis
                  dataKey="predicted"
                  type="number"
                  domain={[0, 100]}
                  tick={axisStyle}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => `${v}%`}
                />
                <YAxis
                  domain={[0, 100]}
                  tick={axisStyle}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => `${v}%`}
                />
                <Tooltip
                  contentStyle={tooltipStyle}
                  formatter={(v) => [`${v}%`, "Actual renewal rate"]}
                  labelFormatter={(l) => `Predicted ${l}%`}
                />
                <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 100, y: 100 }]} stroke="#5b6c8c" strokeDasharray="5 5" />
                <Line dataKey="actual" stroke="#34d399" strokeWidth={2.5} dot={{ r: 4, fill: "#34d399", strokeWidth: 0 }} />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="card">
            <div className="card-title">ROC Curve</div>
            <div className="card-sub">{head.rocSub}</div>
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={rocData} margin={{ top: 8, right: 14, left: -16, bottom: 0 }}>
                <CartesianGrid stroke="#16223a" />
                <XAxis
                  dataKey="fpr"
                  type="number"
                  domain={[0, 100]}
                  tick={axisStyle}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => `${v}%`}
                />
                <YAxis
                  domain={[0, 100]}
                  tick={axisStyle}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => `${v}%`}
                />
                <Tooltip
                  contentStyle={tooltipStyle}
                  formatter={(v) => [`${v}%`, "True positive rate"]}
                  labelFormatter={(l) => `False positive rate ${l}%`}
                />
                <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 100, y: 100 }]} stroke="#5b6c8c" strokeDasharray="5 5" />
                <Line dataKey="tpr" stroke="#818cf8" strokeWidth={2.5} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-title">Model Architecture</div>
        <div className="card-sub">How the engine works under the hood</div>
        <div className="fact-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))" }}>
          <Fact k="Ensemble" v="Random Forest + Extra Trees + Gradient Boosting (soft voting)" />
          <Fact k="Probability calibration" v="Isotonic regression, 3-fold" />
          <Fact k="Validation" v="Leak-free monthly walk-forward (pooled) + out-of-time holdout" />
          <Fact k="Features" v="18 raw factors + 7 engineered interactions" />
          <Fact k="Retraining cadence" v="Monthly — every decision enriches the next model" />
          <Fact k="Forecast horizon" v="2 quarters forward, scored per group" />
        </div>
      </div>
    </>
  );
}

function Fact({ k, v }) {
  return (
    <div className="fact">
      <div className="k">{k}</div>
      <div className="v" style={{ lineHeight: 1.45 }}>{v}</div>
    </div>
  );
}
