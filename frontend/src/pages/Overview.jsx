import {
  Banknote,
  CalendarClock,
  ShieldAlert,
  ShieldCheck,
  Target,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useEffect, useState } from "react";
import { ArrowRight, Lightbulb } from "lucide-react";
import { api } from "../api.js";
import { TIER_COLORS, fmtMoney, fmtNum, fmtPct } from "../format.js";
import { KpiCard, Legend, ProbCell, RiskBadge, axisStyle, tooltipStyle } from "../components/shared.jsx";

export default function Overview({ data }) {
  const { summary, segments, groups } = data;

  const [recommendations, setRecommendations] = useState([]);
  const [recLoading, setRecLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setRecLoading(true);
    api
      .recommendations()
      .then((r) => alive && setRecommendations(r.recommendations || []))
      .catch(() => alive && setRecommendations([]))
      .finally(() => alive && setRecLoading(false));
    return () => { alive = false; };
  }, []);

  const tierData = Object.entries(summary.tier_counts).map(([name, value]) => ({
    name,
    value,
  }));

  const atRisk = groups.filter((g) => ["Critical", "Concern", "Watch"].includes(g.risk_tier) && !g.actual_outcome);
  const topAtRisk = [...atRisk]
    .sort((a, b) => b.premium_at_risk - a.premium_at_risk)
    .slice(0, 8);

  const tenureData = segments.by_tenure.map((s) => ({
    segment: s.segment,
    retention: +(s.retention_rate * 100).toFixed(1),
  }));
  const order = ["1st renewal", "2nd year", "3rd year", "4-5 years", "6+ years"];
  tenureData.sort((a, b) => order.indexOf(a.segment) - order.indexOf(b.segment));

  return (
    <>
      <div className="grid grid-kpi">
        <KpiCard
          icon={CalendarClock}
          label="Renewals Due (2 Qtrs)"
          value={fmtNum(summary.renewals_due)}
          foot={`${fmtMoney(summary.premium_due)} premium up for renewal`}
        />
        <KpiCard
          icon={Target}
          label="Expected Retention"
          value={fmtPct(summary.expected_retention_rate)}
          tone="accent"
          foot={`${fmtPct(summary.premium_weighted_retention)} premium-weighted`}
        />
        <KpiCard
          icon={ShieldCheck}
          label="Expected Renewals"
          value={fmtNum(Math.round(summary.expected_renewals))}
          tone="green"
          foot={`of ${fmtNum(summary.renewals_due)} due in window`}
        />
        <KpiCard
          icon={ShieldAlert}
          label="Groups At Risk"
          value={fmtNum(summary.tier_counts.Watch + summary.tier_counts.Concern + summary.tier_counts.Critical)}
          tone="amber"
          foot={`${summary.tier_counts.Critical} critical · ${summary.tier_counts.Concern} concern · ${summary.tier_counts.Watch} watch`}
        />
        <KpiCard
          icon={Banknote}
          label="Expected Lost Premium"
          value={fmtMoney(summary.premium_at_risk)}
          tone="red"
          foot="premium × probability of lapse"
        />
      </div>

      <div className="grid grid-2-3">
        <div className="card">
          <div className="card-title">Risk Tier Mix</div>
          <div className="card-sub">Groups renewing in the next two quarters</div>
          <ResponsiveContainer width="100%" height={240}>
            <PieChart>
              <Pie
                data={tierData}
                dataKey="value"
                nameKey="name"
                innerRadius={62}
                outerRadius={95}
                paddingAngle={3}
                strokeWidth={0}
              >
                {tierData.map((t) => (
                  <Cell key={t.name} fill={TIER_COLORS[t.name]} />
                ))}
              </Pie>
              <Tooltip contentStyle={tooltipStyle} />
            </PieChart>
          </ResponsiveContainer>
          <Legend items={tierData.map((t) => [`${t.name} (${t.value})`, TIER_COLORS[t.name]])} />
        </div>

        <div className="card">
          <div className="card-title">Retention by Tenure</div>
          <div className="card-sub">
            The first renewal is the cliff — loyalty compounds after year three
          </div>
          <ResponsiveContainer width="100%" height={265}>
            <BarChart data={tenureData} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
              <CartesianGrid stroke="#16223a" vertical={false} />
              <XAxis dataKey="segment" tick={axisStyle} axisLine={false} tickLine={false} />
              <YAxis
                tick={axisStyle}
                axisLine={false}
                tickLine={false}
                domain={[0, 100]}
                tickFormatter={(v) => `${v}%`}
              />
              <Tooltip contentStyle={tooltipStyle} formatter={(v) => [`${v}%`, "Expected retention"]} />
              <Bar dataKey="retention" radius={[6, 6, 0, 0]} fill="#38bdf8" maxBarSize={56} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="grid grid-2">
        <div className="card">
          <div className="card-title">Retention by Line of Business</div>
          <div className="card-sub">Expected retention and premium at risk per segment</div>
          <SegmentTable rows={segments.by_lob} />
          <div className="drawer-section-title" style={{ marginTop: 18 }}>By RSD</div>
          <SegmentTable rows={segments.by_rsd} />
        </div>

        <div className="card">
          <div className="card-title">Top Save Opportunities</div>
          <div className="card-sub">Watch &amp; critical groups ranked by expected lost premium — start the save conversations here</div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Group</th>
                  <th>Renewal Likelihood</th>
                  <th>Tier</th>
                  <th>Exp. Lost Premium</th>
                </tr>
              </thead>
              <tbody>
                {topAtRisk.map((g) => (
                  <tr key={g.group_id}>
                    <td>
                      <div className="cell-main">{g.group_name}</div>
                      <div className="cell-dim">
                        {g.line_of_business} · renews {g.renewal_date}
                      </div>
                    </td>
                    <td><ProbCell p={g.renewal_probability} /></td>
                    <td><RiskBadge tier={g.risk_tier} /></td>
                    <td className="mono">{fmtMoney(g.premium_at_risk)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {(recLoading || recommendations.length > 0) && (
        <div className="card">
          <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Lightbulb size={15} color="var(--accent)" /> Recommended Actions by ML Model
          </div>
          <div className="card-sub">
            The actions the model predicts would save the most premium. Each is the model's own
            counterfactual — work this list top-down in the renewal huddles.
          </div>
          {recLoading && (
            <div className="cell-dim" style={{ padding: "8px 2px" }}>
              Computing model counterfactuals across the at-risk book…
            </div>
          )}
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Group</th>
                  <th>Recommended action</th>
                  <th>Likelihood lift</th>
                  <th>Premium saved</th>
                </tr>
              </thead>
              <tbody>
                {recommendations.slice(0, 8).map((r) => (
                  <tr key={r.group_id}>
                    <td>
                      <div className="cell-main">{r.group_name}</div>
                      <div className="cell-dim">{fmtMoney(r.annual_premium)} premium</div>
                    </td>
                    <td className="cell-dim" style={{ whiteSpace: "normal", maxWidth: 260 }}>{r.best.label}</td>
                    <td className="mono">
                      {fmtPct(r.baseline_probability, 0)}
                      <ArrowRight size={12} style={{ verticalAlign: "middle", margin: "0 3px", color: "var(--text-faint)" }} />
                      <b style={{ color: "var(--green)" }}>{fmtPct(r.best.new_probability, 0)}</b>
                    </td>
                    <td className="mono" style={{ color: "var(--green)" }}>{fmtMoney(r.best.premium_saved)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}

function SegmentTable({ rows }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Segment</th>
            <th>Groups</th>
            <th>Retention</th>
            <th>Exp. Lost</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.segment} style={{ cursor: "default" }}>
              <td className="cell-main">{s.segment}</td>
              <td className="mono">{fmtNum(s.groups)}</td>
              <td><ProbCell p={s.retention_rate} showTag={false} /></td>
              <td className="mono">{fmtMoney(s.premium_at_risk)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
