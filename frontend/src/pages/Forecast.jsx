import { useState } from "react";
import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fmtMoney, fmtMonth, fmtNum, fmtPct } from "../format.js";
import { ChartTooltip, Legend, axisStyle } from "../components/shared.jsx";

const RETENTION = "#059669"; // rich emerald retention line (semantic: retention = kept/good);
// deeper than the light-emerald "Confirmed renewed" bar (#34d399) so the line stays distinct.

// color the quarter retention headline by health so a glance reads good/ok/at-risk
const retColor = (r) =>
  r == null ? "var(--text)" : r >= 0.7 ? "var(--green)" : r >= 0.55 ? "var(--amber)" : "var(--red)";

export default function Forecast({ data }) {
  const { months, quarters } = data.forecast;

  // DEFAULT = Combined (confirmed real outcomes + projected), the fullest view.
  // Toggling off drops to projected-only (the pure forward forecast) across the
  // whole page: quarter cards, outlook chart, and premium chart.
  const [showConfirmed, setShowConfirmed] = useState(true);

  // per-quarter values switch between projected-only and projected+confirmed totals
  const qv = (q) =>
    showConfirmed
      ? {
          retention: q.retention_rate,
          due: q.renewals_due,
          toRenew: q.expected_renewals,
          premDue: q.premium_due,
          premRet: q.premium_retained,
          premRisk: q.premium_at_risk,
        }
      : {
          retention: q.projected_retention_rate,
          due: q.open_count,
          toRenew: q.projected_renewals,
          premDue: q.projected_premium_retained + q.projected_premium_at_risk,
          premRet: q.projected_premium_retained,
          premRisk: q.projected_premium_at_risk,
        };

  const monthData = months.map((m) => {
    const projRetention =
      m.projected_retention_rate != null ? +(m.projected_retention_rate * 100).toFixed(1) : null;
    return {
      month: fmtMonth(m.month),
      "Confirmed renewed": m.confirmed_renewed ?? 0,
      "Projected renewals": +(m.projected_renewals ?? 0).toFixed(0),
      "Projected lapses": +(m.projected_lapses ?? 0).toFixed(0),
      "Confirmed lost": m.confirmed_lost ?? 0,
      retention: showConfirmed ? +(m.retention_rate * 100).toFixed(1) : projRetention,
    };
  });

  const premiumData = months.map((m) => ({
    month: fmtMonth(m.month),
    "Premium retained": +(
      (showConfirmed ? m.premium_retained : m.projected_premium_retained) / 1e6
    ).toFixed(2),
    "Expected lost premium": +(
      (showConfirmed ? m.premium_at_risk : m.projected_premium_at_risk) / 1e6
    ).toFixed(2),
  }));

  return (
    <>
      <div className="forecast-toolbar" style={{ display: "flex", justifyContent: "flex-end", marginBottom: 16 }}>
        <div className="seg-toggle" title="Projected only = still-open renewals (the forecast). Combined = also include settled outcomes from the renewal database.">
          <button className={!showConfirmed ? "on" : ""} onClick={() => setShowConfirmed(false)}>
            Projected only
          </button>
          <button className={showConfirmed ? "on" : ""} onClick={() => setShowConfirmed(true)}>
            Combined
          </button>
        </div>
      </div>

      <div className="metric-pill-row">
        {quarters.map((q) => {
          const v = qv(q);
          return (
            <div className="qcard" key={q.quarter}>
              <div className="q-name">{q.quarter.replace("Q", " · Q")}</div>
              <div className="q-big" style={{ color: retColor(v.retention) }}>{fmtPct(v.retention)}</div>
              <div className="kpi-foot" style={{ marginBottom: 12 }}>
                {showConfirmed ? "overall retention" : "projected retention"}
              </div>
              <div className="q-row"><span>Renewals due</span><b>{fmtNum(v.due)}</b></div>
              <div className="q-row"><span>{showConfirmed ? "Renewed/Expected to renew" : "Expected to renew"}</span><b>{fmtNum(Math.round(v.toRenew))}</b></div>
              <div className="q-row"><span>Premium due</span><b>{fmtMoney(v.premDue)}</b></div>
              <div className="q-row"><span>Premium retained</span><b style={{ color: "var(--green)" }}>{fmtMoney(v.premRet)}</b></div>
              <div className="q-row"><span>{showConfirmed ? "Lost premium" : "Expected lost premium"}</span><b style={{ color: "var(--red)" }}>{fmtMoney(v.premRisk)}</b></div>
              <div className="q-row"><span>Watch / concern / critical groups</span><b style={{ color: "var(--amber)" }}>{q.watch_groups} / {q.concern_groups} / {q.critical_groups}</b></div>
            </div>
          );
        })}
      </div>

      <div className="card">
        <div className="card-title">Monthly Renewal Outlook</div>
        <div className="card-sub">
          {showConfirmed
            ? "Confirmed outcomes from the renewal database (solid) plus probability-weighted projections for still-open renewals (lighter)"
            : "Probability-weighted renewals and lapses for still-open renewals, by renewal month"}
        </div>
        <ResponsiveContainer width="100%" height={320}>
          <ComposedChart data={monthData} margin={{ top: 10, right: 14, left: -8, bottom: 0 }}>
            <defs>
              {[["barRenew", "#38bdf8"], ["barLapse", "#fbbf24"],
                ["barConfRenew", "#34d399"], ["barConfLost", "#f87171"]].map(([id, c]) => (
                <linearGradient key={id} id={id} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={c} stopOpacity={0.95} />
                  <stop offset="100%" stopColor={c} stopOpacity={0.62} />
                </linearGradient>
              ))}
            </defs>
            <CartesianGrid stroke="var(--grid, #16223a)" vertical={false} strokeDasharray="3 3" />
            <XAxis dataKey="month" tick={axisStyle} axisLine={false} tickLine={false} />
            <YAxis yAxisId="count" tick={axisStyle} axisLine={false} tickLine={false} />
            <YAxis
              yAxisId="pct"
              orientation="right"
              tick={axisStyle}
              axisLine={false}
              tickLine={false}
              domain={[0, 100]}
              tickFormatter={(v) => `${v}%`}
            />
            <Tooltip content={<ChartTooltip />} cursor={{ fill: "rgba(148,163,184,0.08)" }} />
            {showConfirmed && (
              <Bar yAxisId="count" dataKey="Confirmed renewed" stackId="a" fill="url(#barConfRenew)" maxBarSize={44} />
            )}
            <Bar yAxisId="count" dataKey="Projected renewals" stackId="a" fill="url(#barRenew)" maxBarSize={44} />
            <Bar yAxisId="count" dataKey="Projected lapses" stackId="a" fill="url(#barLapse)" radius={showConfirmed ? [0, 0, 0, 0] : [7, 7, 0, 0]} maxBarSize={44} />
            {showConfirmed && (
              <Bar yAxisId="count" dataKey="Confirmed lost" stackId="a" fill="url(#barConfLost)" radius={[7, 7, 0, 0]} maxBarSize={44} />
            )}
            <Line
              yAxisId="pct"
              type="monotone"
              dataKey="retention"
              name="Retention %"
              stroke={RETENTION}
              strokeWidth={2.5}
              dot={{ r: 3.5, fill: RETENTION, strokeWidth: 2, stroke: "var(--surface, #fff)" }}
              activeDot={{ r: 6, fill: RETENTION, strokeWidth: 2, stroke: "var(--surface, #fff)" }}
              connectNulls
            />
          </ComposedChart>
        </ResponsiveContainer>
        <Legend
          items={
            showConfirmed
              ? [
                  ["Confirmed renewed", "#34d399"],
                  ["Projected renewals", "#38bdf8"],
                  ["Projected lapses", "#fbbf24"],
                  ["Confirmed lost", "#f87171"],
                  ["Retention %", RETENTION],
                ]
              : [
                  ["Projected renewals", "#38bdf8"],
                  ["Projected lapses", "#fbbf24"],
                  ["Retention %", RETENTION],
                ]
          }
        />
      </div>

      <div className="card">
        <div className="card-title">Premium Retention Forecast ($M)</div>
        <div className="card-sub">
          {showConfirmed
            ? "Annualized premium retained vs lost (confirmed + projected), by renewal month"
            : "Annualized premium projected to be retained vs lost for still-open renewals, by renewal month"}
        </div>
        <ResponsiveContainer width="100%" height={300}>
          <ComposedChart data={premiumData} margin={{ top: 10, right: 14, left: -8, bottom: 0 }}>
            <defs>
              <linearGradient id="gradRetained" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#34d399" stopOpacity={0.35} />
                <stop offset="100%" stopColor="#34d399" stopOpacity={0.02} />
              </linearGradient>
              <linearGradient id="gradRisk" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#f87171" stopOpacity={0.4} />
                <stop offset="100%" stopColor="#f87171" stopOpacity={0.03} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="#16223a" vertical={false} />
            <XAxis dataKey="month" tick={axisStyle} axisLine={false} tickLine={false} />
            <YAxis tick={axisStyle} axisLine={false} tickLine={false} tickFormatter={(v) => `$${v}M`} />
            <Tooltip content={<ChartTooltip fmt={(v) => `$${v}M`} />} />
            <Area dataKey="Premium retained" stroke="#34d399" strokeWidth={2.5} fill="url(#gradRetained)" />
            <Area dataKey="Expected lost premium" stroke="#f87171" strokeWidth={2.5} fill="url(#gradRisk)" />
          </ComposedChart>
        </ResponsiveContainer>
        <Legend
          items={[
            ["Premium retained", "#34d399"],
            ["Expected lost premium", "#f87171"],
          ]}
        />
      </div>
    </>
  );
}
