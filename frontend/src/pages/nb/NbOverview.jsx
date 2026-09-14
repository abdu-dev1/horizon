import { Gauge, Percent, Target, TrendingUp } from "lucide-react";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { NB_BAND_COLORS, fmtMoney, fmtNum, fmtPct } from "../../format.js";
import { KpiCard, Legend, ProbCell, LikelihoodBadge, tooltipStyle } from "../../components/shared.jsx";

// Every segment table below is keyed by a real per-quote column (rsd, broker,
// product, industry), so each one can drill into the entity deep-dive page.
// `onDrill(kind, name)` is owned by App.jsx because that page REPLACES the
// main content area -- see NbEntityPage.
export default function NbOverview({ data, onDrill }) {
  const { summary, segments, groups } = data;

  const bandData = Object.entries(summary.band_counts).map(([name, value]) => ({ name, value }));
  const topPicks = [...groups]
    .filter((g) => g.likelihood_band === "High" || g.likelihood_band === "Moderate")
    .sort((a, b) => b.expected_value - a.expected_value)
    .slice(0, 8);

  return (
    <>
      <div className="grid grid-kpi">
        <KpiCard
          icon={Target}
          label="Open Quotes"
          value={fmtNum(summary.open_quotes)}
          foot={`${fmtMoney(summary.potential_premium)} potential premium`}
        />
        <KpiCard
          icon={TrendingUp}
          label="Expected Wins"
          value={summary.expected_wins.toFixed(1)}
          tone="accent"
          foot="sum of each quote's band's measured historical win rate"
        />
        <KpiCard
          icon={Gauge}
          label="High + Moderate Leads"
          value={fmtNum(summary.band_counts.High + summary.band_counts.Moderate)}
          tone="green"
          foot={`${summary.band_counts.High} high · ${summary.band_counts.Moderate} moderate`}
        />
        <KpiCard
          icon={Percent}
          label="All-Time Win Rate"
          value={fmtPct(summary.alltime_win_rate, 1)}
          foot={`${fmtNum(summary.alltime_wins)} won of ${fmtNum(summary.alltime_quotes)} decided quotes`}
        />
        <KpiCard
          icon={TrendingUp}
          label="Expected Value"
          value={fmtMoney(summary.expected_premium_won)}
          tone="green"
          foot="potential premium × band's historical win rate, summed"
        />
      </div>

      <div className="grid grid-2-3">
        <div className="card">
          <div className="card-title">Pipeline Likelihood Mix</div>
          <div className="card-sub">Every open, undecided quote right now</div>
          <ResponsiveContainer width="100%" height={240}>
            <PieChart>
              <Pie
                data={bandData}
                dataKey="value"
                nameKey="name"
                innerRadius={62}
                outerRadius={95}
                paddingAngle={3}
                strokeWidth={0}
              >
                {bandData.map((t) => (
                  <Cell key={t.name} fill={NB_BAND_COLORS[t.name]} />
                ))}
              </Pie>
              <Tooltip contentStyle={tooltipStyle} />
            </PieChart>
          </ResponsiveContainer>
          <Legend items={bandData.map((t) => [`${t.name} (${t.value})`, NB_BAND_COLORS[t.name]])} />
        </div>

        <div className="card">
          <div className="card-title">Top Picks by Expected Value</div>
          <div className="card-sub">
            High &amp; Moderate likelihood leads ranked by potential premium × the band's historical win rate —
            work these first
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Group</th>
                  <th>Expected Value</th>
                  <th>Historical Rate</th>
                  <th>Likelihood</th>
                </tr>
              </thead>
              <tbody>
                {topPicks.map((g) => (
                  <tr key={g.quote_id}>
                    <td>
                      <div className="cell-main">{g.group_name}</div>
                      <div className="cell-dim">{g.product} · {g.rsd || "—"}</div>
                    </td>
                    <td className="mono">{fmtMoney(g.expected_value)}</td>
                    <td>
                      <ProbCell p={g.band_win_rate} showTag={false} colorFn={() => NB_BAND_COLORS[g.likelihood_band]} />
                    </td>
                    <td><LikelihoodBadge band={g.likelihood_band} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div className="grid grid-2">
        <div className="card">
          <div className="card-title">Open Pipeline by Product</div>
          <div className="card-sub">
            Expected wins summed across each product line · <span className="drill-hint">click a row for its full detail</span>
          </div>
          <SegmentTable rows={segments.by_product_open} kind="product" onDrill={onDrill} />
        </div>
        <div className="card">
          <div className="card-title">Open Pipeline by RSD</div>
          <div className="card-sub">
            Expected wins summed per rep · <span className="drill-hint">click a rep for their full detail</span>
          </div>
          <SegmentTable rows={segments.by_rsd_open} kind="rsd" onDrill={onDrill} />
        </div>
      </div>

      <div className="grid grid-2">
        <div className="card">
          <div className="card-title">All-Time Win Rate by Broker</div>
          <div className="card-sub">
            Historical decided quotes only — the same track record the model reads ·{" "}
            <span className="drill-hint">click a broker for their full detail</span>
          </div>
          <HistorySegmentTable rows={segments.by_broker_history.slice(0, 12)} kind="broker" onDrill={onDrill} />
        </div>
        <div className="card">
          <div className="card-title">All-Time Win Rate by Industry</div>
          <div className="card-sub">
            Historical decided quotes only · <span className="drill-hint">click an industry for its full detail</span>
          </div>
          <HistorySegmentTable rows={segments.by_industry_history.slice(0, 12)} kind="industry" onDrill={onDrill} />
        </div>
      </div>

    </>
  );
}

// `kind` is the quote column this table segments by, and is what the deep-dive
// page filters on -- so the label in the first cell has to be the raw column
// value the backend grouped by, not a prettified version of it.
function SegmentTable({ rows, kind, onDrill }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Segment</th>
            <th>Open Quotes</th>
            <th>Expected Wins</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr
              key={s.label}
              className="row-drill"
              tabIndex={0}
              onClick={() => onDrill(kind, s.label)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onDrill(kind, s.label);
                }
              }}
            >
              <td className="cell-main">{s.label}</td>
              <td className="mono">{fmtNum(s.open_quotes)}</td>
              <td className="mono">{s.expected_wins.toFixed(1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HistorySegmentTable({ rows, kind, onDrill }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Segment</th>
            <th>Quotes</th>
            <th>Win Rate</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr
              key={s.label}
              className="row-drill"
              tabIndex={0}
              onClick={() => onDrill(kind, s.label)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onDrill(kind, s.label);
                }
              }}
            >
              <td className="cell-main">{s.label}</td>
              <td className="mono">{fmtNum(s.quotes)}</td>
              <td><ProbCell p={s.win_rate} showTag={false} colorFn={() => "#38bdf8"} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
