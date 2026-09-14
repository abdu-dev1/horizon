import { memo, useMemo, useState } from "react";
import { Award, DollarSign, Percent, TrendingDown, TrendingUp, Trophy } from "lucide-react";
import {
  Bar, BarChart, CartesianGrid, Cell, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { fmtMoney, fmtNum, fmtPct } from "../../format.js";
import {
  CHART_INK as INK, CHART_INK_DIM as INK_DIM, KpiCard, RateBar, Spark, axisStyle,
} from "../../components/shared.jsx";

// One hue for every chart on this page, deliberately. The alternative -- a
// categorical palette across 7-8 products or RSDs on shared axes -- is
// spaghetti at that series count, and the dataviz series cap would force most
// of them into an "Other" bucket anyway. Small multiples (a sparkline per
// leaderboard row) carry the same per-entity trend with one hue and no legend,
// so color never has to encode identity here at all.
//
// The four color constants and the Spark/RateBar components themselves now
// live in components/shared.jsx -- the entity deep-dive page (NbEntityPage)
// renders the same comparison bar and sparkline, and a second copy is how two
// views of one number drift apart.

// A year with quotes still open can only go UP as they decide, so it is drawn
// recessive and flagged in the caption rather than plotted as if final.
const PROVISIONAL_OPACITY = 0.42;

const PAGE_SIZE = 25; // matches NbPipeline's table page size

export default function NbPerformance({ data, onDrill }) {
  const perf = data.performance;

  return (
    <>
      <HeadlineKpis perf={perf} />
      <YearCharts perf={perf} />
      <SizeAndSeasonCharts perf={perf} />
      <LeaderboardCard perf={perf} onDrill={onDrill} />
    </>
  );
}

// ---------------------------------------------------------------- headline

function HeadlineKpis({ perf }) {
  const h = perf.headline;
  const delta =
    h.latest_closed_win_rate != null && h.prior_closed_win_rate != null
      ? h.latest_closed_win_rate - h.prior_closed_win_rate
      : null;
  const best = useMemo(() => bestSegment(perf), [perf]);

  return (
    <div className="grid grid-kpi">
      <KpiCard
        icon={Percent}
        label="All-Time Win Rate"
        value={fmtPct(h.win_rate, 1)}
        foot={`${fmtNum(h.wins)} won of ${fmtNum(h.quotes)} decided quotes`}
      />
      <KpiCard
        icon={delta == null ? Award : delta >= 0 ? TrendingUp : TrendingDown}
        label={h.latest_closed_year ? `${h.latest_closed_year} Win Rate` : "Latest Win Rate"}
        value={fmtPct(h.latest_closed_win_rate, 1)}
        tone={delta == null ? undefined : delta >= 0 ? "green" : "red"}
        foot={
          delta == null
            ? "last fully-decided selling season"
            : `${delta >= 0 ? "+" : ""}${(delta * 100).toFixed(1)} pts vs ${h.prior_closed_year} (${fmtPct(h.prior_closed_win_rate, 1)})`
        }
      />
      <KpiCard
        icon={DollarSign}
        label="Premium Won"
        value={fmtMoney(h.premium_won)}
        tone="green"
        foot="money already closed, across the whole decided book"
      />
      <KpiCard
        icon={Trophy}
        label="Best Segment"
        value={best.rate}
        foot={best.label}
      />
      <KpiCard
        icon={Award}
        label="Network Size"
        value={fmtNum(h.rsd_count)}
        foot={`RSDs · ${fmtNum(h.broker_count)} brokers · ${fmtNum(h.open_quotes)} still open`}
      />
    </div>
  );
}

function bestSegment(perf) {
  const rows = perf.leaderboards.product.filter((r) => r.enough_volume);
  if (!rows.length) return { rate: "—", label: "not enough volume in any product" };
  const top = rows.reduce((a, b) => (b.win_rate > a.win_rate ? b : a));
  return {
    rate: fmtPct(top.win_rate, 1),
    label: `${top.label} — ${fmtNum(top.wins)} of ${fmtNum(top.quotes)} quotes`,
  };
}

// ---------------------------------------------------------------- year charts
// Isolated behind React.memo, keyed only on `perf`: the year-basis toggle is
// local state here, not in the page component, so switching it (or anything
// happening in the Leaderboard below) never forces the Size/Seasonality
// charts -- or this component's own Recharts trees -- to redo layout work
// they have no reason to repeat.

const YearCharts = memo(function YearCharts({ perf }) {
  const [basis, setBasis] = useState("effective");
  const h = perf.headline;
  const avgPct = h.win_rate * 100;

  const yearRows = useMemo(() => {
    const years = basis === "effective" ? perf.by_effective_year : perf.by_created_year;
    return years.map((r) => ({
      ...r,
      label: String(r.year) + (r.in_progress ? "*" : ""),
      ratePct: r.win_rate == null ? null : r.win_rate * 100,
    }));
  }, [perf, basis]);

  return (
    <div className="grid grid-2">
      <div className="card">
        <div className="card-head-row">
          <div>
            <div className="card-title">Win Rate by Year</div>
            <div className="card-sub">
              Dashed line is the all-time average ({fmtPct(h.win_rate, 1)}).
              {yearRows.some((r) => r.in_progress) && " Faded bars marked * still have open quotes — those rates can only rise."}
            </div>
          </div>
          <select className="filter" value={basis} onChange={(e) => setBasis(e.target.value)}>
            <option value="effective">By effective year</option>
            <option value="created">By created year</option>
          </select>
        </div>
        <ResponsiveContainer width="100%" height={250}>
          <BarChart data={yearRows} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
            <CartesianGrid stroke="#16223a" vertical={false} />
            <XAxis dataKey="label" tick={axisStyle} axisLine={false} tickLine={false} />
            <YAxis unit="%" tick={axisStyle} axisLine={false} tickLine={false} />
            <Tooltip content={<YearTip />} isAnimationActive={false} />
            <ReferenceLine y={avgPct} stroke="#5b6c8c" strokeDasharray="5 5" />
            <Bar dataKey="ratePct" radius={[4, 4, 0, 0]} maxBarSize={54} isAnimationActive={false}>
              {yearRows.map((r) => (
                <Cell key={r.year} fill={INK} fillOpacity={r.in_progress ? PROVISIONAL_OPACITY : 1} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
        <div className="card-note">
          {basis === "effective"
            ? "Effective year = which selling season the business belongs to. This is what “how did 2025 go” means."
            : "Created year = when the opportunity entered the funnel. Recent years read low here: a quote created late that will be won often hasn’t decided yet."}
        </div>
      </div>

      <div className="card">
        <div className="card-title">Decided Quote Volume by Year</div>
        <div className="card-sub">
          Deliberately its own chart, not a second axis on the win-rate chart —
          two scales on one plot invites reading a correlation that isn’t there
        </div>
        <ResponsiveContainer width="100%" height={250}>
          <BarChart data={yearRows} margin={{ top: 8, right: 8, left: -6, bottom: 0 }}>
            <CartesianGrid stroke="#16223a" vertical={false} />
            <XAxis dataKey="label" tick={axisStyle} axisLine={false} tickLine={false} />
            <YAxis tick={axisStyle} axisLine={false} tickLine={false} />
            <Tooltip content={<YearTip volume />} isAnimationActive={false} />
            <Bar dataKey="quotes" radius={[4, 4, 0, 0]} maxBarSize={54} isAnimationActive={false}>
              {yearRows.map((r) => (
                <Cell key={r.year} fill={INK_DIM} fillOpacity={r.in_progress ? PROVISIONAL_OPACITY : 1} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
        <div className="card-note">
          Wins per year: {yearRows.map((r) => `${r.label} ${r.wins}`).join(" · ")}
        </div>
      </div>
    </div>
  );
});

// ---------------------------------------------------------------- size + seasonality

const SizeAndSeasonCharts = memo(function SizeAndSeasonCharts({ perf }) {
  const h = perf.headline;
  const avgPct = h.win_rate * 100;

  const sizeRows = useMemo(
    () => perf.leaderboards.size.map((r) => ({ ...r, ratePct: r.win_rate * 100 })),
    [perf]
  );
  const monthRows = useMemo(
    () => perf.seasonality.map((r) => ({ ...r, ratePct: r.win_rate * 100 })),
    [perf]
  );
  const bigCaseQuotes = useMemo(
    () => sizeRows.filter((r) => r.label.startsWith("500") || r.label.startsWith("1,500"))
      .reduce((a, r) => a + r.quotes, 0),
    [sizeRows]
  );
  const busiestMonth = useMemo(
    () => monthRows.slice().sort((a, b) => b.quotes - a.quotes)[0],
    [monthRows]
  );

  return (
    <div className="grid grid-2">
      <div className="card">
        <div className="card-title">Win Rate by Deal Size</div>
        <div className="card-sub">Enrolled employees on the quote — the sharpest split in the whole book</div>
        <ResponsiveContainer width="100%" height={230}>
          <BarChart data={sizeRows} layout="vertical" margin={{ top: 4, right: 30, left: 44, bottom: 0 }}>
            <CartesianGrid stroke="#16223a" horizontal={false} />
            <XAxis type="number" unit="%" tick={axisStyle} axisLine={false} tickLine={false} />
            <YAxis type="category" dataKey="label" width={78}
                   tick={axisStyle} axisLine={false} tickLine={false} />
            <Tooltip content={<SegTip />} isAnimationActive={false} />
            <ReferenceLine x={avgPct} stroke="#5b6c8c" strokeDasharray="5 5" />
            <Bar dataKey="ratePct" radius={[0, 4, 4, 0]} fill={INK} maxBarSize={20} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
        <div className="card-note">
          Cases over 500 lives win at roughly double the rate of everything below —
          on {fmtNum(bigCaseQuotes)} quotes.
        </div>
      </div>

      <div className="card">
        <div className="card-title">Seasonality by Effective Month</div>
        <div className="card-sub">Where the book concentrates, and where it converts</div>
        <ResponsiveContainer width="100%" height={230}>
          <BarChart data={monthRows} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
            <CartesianGrid stroke="#16223a" vertical={false} />
            <XAxis dataKey="month" tick={axisStyle} axisLine={false} tickLine={false} />
            <YAxis unit="%" tick={axisStyle} axisLine={false} tickLine={false} />
            <Tooltip content={<SegTip />} isAnimationActive={false} />
            <ReferenceLine y={avgPct} stroke="#5b6c8c" strokeDasharray="5 5" />
            <Bar dataKey="ratePct" radius={[4, 4, 0, 0]} fill={INK} maxBarSize={26} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
        <div className="card-note">
          {busiestMonth.month} carries the most volume ({fmtNum(busiestMonth.quotes)} decided quotes).
        </div>
      </div>
    </div>
  );
});

// ---------------------------------------------------------------- leaderboard
// Its own component with its own state (board/volumeOnly/page), isolated from
// the three chart sections above. Filtering, sorting, or paging this table
// used to re-render all four Recharts trees on every click because everything
// lived in one component -- that round-trip through recalculating and
// repainting charts that hadn't actually changed was the page's real
// sluggishness, not the charts existing per se. Paginating at PAGE_SIZE also
// caps the sparkline count per render at 25 rather than up to 741 for Broker.

const LeaderboardCard = memo(function LeaderboardCard({ perf, onDrill }) {
  const [board, setBoard] = useState("rsd");
  const [volumeOnly, setVolumeOnly] = useState(true);
  const [page, setPage] = useState(0);
  const h = perf.headline;

  const rows = useMemo(() => {
    const all = perf.leaderboards[board] ?? [];
    return volumeOnly ? all.filter((r) => r.enough_volume) : all;
  }, [perf, board, volumeOnly]);

  const hiddenCount = (perf.leaderboards[board] ?? []).length - rows.length;
  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const clampedPage = Math.min(page, pageCount - 1);
  const pageRows = rows.slice(clampedPage * PAGE_SIZE, (clampedPage + 1) * PAGE_SIZE);

  const changeBoard = (v) => { setBoard(v); setPage(0); };
  const changeVolumeOnly = (v) => { setVolumeOnly(v); setPage(0); };

  return (
    <div className="card">
      <div className="card-head-row">
        <div>
          <div className="card-title">Leaderboard</div>
          <div className="card-sub">
            All-time decided quotes. Bars compare to the {fmtPct(h.win_rate, 1)} book average;
            the spark column is that row’s own win rate by year. ·{" "}
            <span className="drill-hint">click any row for its full detail page</span>
          </div>
        </div>
        <div className="filter-row">
          <select className="filter" value={board} onChange={(e) => changeBoard(e.target.value)}>
            <option value="rsd">RSD</option>
            <option value="product">Product</option>
            <option value="broker">Broker</option>
            <option value="industry">Industry</option>
            <option value="state">State</option>
          </select>
          <label className="filter-check">
            <input type="checkbox" checked={volumeOnly} onChange={(e) => changeVolumeOnly(e.target.checked)} />
            {`${perf.min_volume}+ quotes only`}
          </label>
        </div>
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{board === "rsd" ? "RSD" : board === "state" ? "State" : board[0].toUpperCase() + board.slice(1)}</th>
              <th>Quotes</th>
              <th>Wins</th>
              <th>Win Rate</th>
              <th>vs Avg</th>
              <th>Premium Won</th>
              <th>By Year</th>
            </tr>
          </thead>
          <tbody>
            {pageRows.map((r) => (
              <LeaderboardRow
                key={r.label} r={r} avg={h.win_rate}
                onDrill={() => onDrill(board, r.label)}
              />
            ))}
          </tbody>
        </table>
      </div>

      <div className="pager">
        <span>{fmtNum(rows.length)} {board}{rows.length === 1 ? "" : "s"} · page {clampedPage + 1} of {pageCount}</span>
        <div className="pager-btns">
          <button disabled={clampedPage === 0} onClick={() => setPage(0)}>« First</button>
          <button disabled={clampedPage === 0} onClick={() => setPage(clampedPage - 1)}>‹ Prev</button>
          <button disabled={clampedPage >= pageCount - 1} onClick={() => setPage(clampedPage + 1)}>Next ›</button>
          <button disabled={clampedPage >= pageCount - 1} onClick={() => setPage(pageCount - 1)}>Last »</button>
        </div>
      </div>

      {hiddenCount > 0 && (
        <div className="card-note">
          {hiddenCount} more {board} value{hiddenCount === 1 ? "" : "s"} with under {perf.min_volume} decided
          quotes — untick the filter to include them. A win rate on 6 quotes is noise, not performance.
        </div>
      )}
    </div>
  );
});

// One row, memoized on its own row object + the book average -- neither
// changes as the parent's unrelated state (e.g. a hover elsewhere) updates,
// so a re-render of the table shell doesn't re-run every row's sparkline math.
const LeaderboardRow = memo(function LeaderboardRow({ r, avg, onDrill }) {
  return (
    <tr
      className="row-drill"
      tabIndex={0}
      onClick={onDrill}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onDrill(); }
      }}
    >
      <td className="cell-main">
        {r.label}
        {!r.enough_volume && <span className="cell-dim"> · low volume</span>}
      </td>
      <td className="mono">{fmtNum(r.quotes)}</td>
      <td className="mono">{fmtNum(r.wins)}</td>
      <td className="mono">{fmtPct(r.win_rate, 1)}</td>
      <td><RateBar rate={r.win_rate} avg={avg} /></td>
      <td className="mono">{r.premium_won == null ? <span className="muted">—</span> : fmtMoney(r.premium_won)}</td>
      <td><Spark points={r.trend} avg={avg} /></td>
    </tr>
  );
});


function YearTip({ active, payload, volume }) {
  if (!active || !payload?.length) return null;
  const r = payload[0].payload;
  return (
    <div className="chart-tip">
      <div className="chart-tip-head">{r.year}{r.in_progress ? " · in progress" : ""}</div>
      {volume ? (
        <div>{fmtNum(r.quotes)} decided · {fmtNum(r.wins)} won</div>
      ) : (
        <div>{fmtPct(r.win_rate, 1)} win rate · {fmtNum(r.wins)} of {fmtNum(r.quotes)}</div>
      )}
      {r.premium_won != null && <div className="chart-tip-dim">{fmtMoney(r.premium_won)} won premium</div>}
      {r.in_progress && <div className="chart-tip-dim">Quotes still open — this rate can only rise.</div>}
    </div>
  );
}

function SegTip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const r = payload[0].payload;
  return (
    <div className="chart-tip">
      <div className="chart-tip-head">{r.label ?? r.month}</div>
      <div>{fmtPct(r.win_rate, 1)} win rate · {fmtNum(r.wins)} of {fmtNum(r.quotes)}</div>
      {r.premium_won != null && (
        <div className="chart-tip-dim">{fmtMoney(r.premium_won)} won premium</div>
      )}
    </div>
  );
}
