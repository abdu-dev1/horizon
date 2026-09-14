/**
 * One-entity deep dive for the New Business book: a full page for a single
 * RSD, broker, product, industry or state, reached by clicking that name in
 * any segment table or leaderboard row.
 *
 * Why a page and not a drawer
 * ---------------------------
 * This started as a right-side drawer, which was wrong for the amount of data
 * involved: one RSD can carry ~900 decided quotes and ~45 open ones, and the
 * panel also holds a KPI row, a band mix, loss reasons, seasonality and two
 * partner breakdowns. A 500-1080px overlay turns that into a kilometre of
 * scroll on top of a page you can no longer see. A page gets the full content
 * width for the tables, keeps the sidebar available, and is a destination an
 * exec can sit and study rather than a popup they hold open.
 *
 * Consequently the two long tables are PAGINATED and searchable rather than
 * truncated. The drawer version capped the decided list at the 150 most
 * recent, which quietly meant "not actually every deal" for anyone with real
 * volume -- the whole point of the click-through is that it IS the complete
 * record for that entity, both halves of it:
 *   - every open quote from the current pipeline   (data.groups)
 *   - every decided quote from the Win/Loss record (/api/history)
 *
 * Where each number comes from, and why it matters
 * ------------------------------------------------
 * The headline rate/quotes/wins/premium/trend are read STRAIGHT OUT of
 * performance.leaderboards[kind] -- the same server-computed rows the Sales
 * Performance page prints -- rather than recomputed in the browser. That is
 * deliberate: an exec who lands here from that leaderboard must see the
 * identical number they just clicked, and a second client-side
 * implementation of "win rate" is exactly how those two drift apart.
 *
 * Only what the server does NOT already aggregate per entity is computed
 * here: the band mix and expected-value totals of the open pipeline, and the
 * loss-reason / seasonality / partner breakdowns of the decided history.
 *
 * /api/history is fetched lazily on first visit and cached for the session
 * (module-level `historyCache`): it is every decided quote ever, too much to
 * add to the app's initial load for a page most sessions never open, but
 * small enough to keep once fetched.
 *
 * The volume caveat is shown, not hidden: the backend's own MIN_VOLUME gate
 * (performance.min_volume) decides whether a win rate is a track record or
 * noise, and a rate computed on fewer quotes than that gets an explicit
 * warning rather than a confident number -- the same rule the leaderboard's
 * "low volume" tag already applies.
 */

import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, DollarSign, Percent, Search, Target, TrendingUp } from "lucide-react";
import { apiNb } from "../../apiNb.js";
import { NB_BAND_COLORS, fmtDate, fmtMoney, fmtNum, fmtPct } from "../../format.js";
import {
  BandMixBar, DOWN, HoverTip, KpiCard, LikelihoodBadge, RateBar, Spark, UP,
} from "../../components/shared.jsx";

const PAGE_SIZE = 25; // matches NbPipeline / NbPerformance

// The leaderboard's board NAME is not always the quote COLUMN name: the
// "state" board is built server-side from `billing_state`
// (insights_nb.performance(), line ~516) but keyed as "state". Filtering rows
// on `row["state"]` therefore matched nothing and fell into the "Unknown"
// bucket for every single quote -- a state page that silently showed the
// whole book. Caught by cross-checking each leaderboard row's own `quotes`
// count against the rows this page can actually list; that check now passes
// for all five dimensions.
const ROW_COL = {
  rsd: "rsd", broker: "broker", product: "product",
  industry: "industry", state: "billing_state",
};

// Matches insights_nb._agg(), which fills a null grouping key with "Unknown"
// BEFORE grouping -- so a leaderboard row labelled "Unknown" has to match the
// null-RSD/null-broker quotes here too, or its page would come up empty.
const UNKNOWN = "Unknown";
const keyOf = (row, kind) => {
  const v = row[ROW_COL[kind] ?? kind];
  return v == null || String(v).trim() === "" ? UNKNOWN : String(v);
};

const KIND_LABEL = {
  rsd: "RSD", broker: "Broker", product: "Product",
  industry: "Industry", state: "State",
};

// Which column names a *counterparty* for this entity kind. An RSD's
// interesting partner list is the brokers they quote through; a broker's is
// the reps working them. For a product/industry/state both sides are
// informative, so both get shown.
//
// Every key here is a real per-quote column, which is what makes the
// filtering work. performance.leaderboards also exposes a "size" board whose
// labels are bucket names ("100-249 lives"), NOT a column value -- it is
// deliberately absent from the leaderboard's own <select>, so no row can open
// this page with kind="size". The `??` fallbacks keep that a graceful empty
// page rather than a white screen if that ever changes.
const PARTNERS = {
  rsd: ["broker"],
  broker: ["rsd"],
  product: ["rsd", "broker"],
  industry: ["rsd", "broker"],
  state: ["rsd", "broker"],
};

// Fetched once per session, shared across every visit. Not React state on
// purpose -- it has to survive this page unmounting when you navigate away.
let historyCache = null;

export default function NbEntityPage({ kind, name, data, onBack, backLabel }) {
  const [history, setHistory] = useState(historyCache);
  const [histErr, setHistErr] = useState(null);

  useEffect(() => {
    if (historyCache) return;
    let alive = true;
    apiNb.history()
      .then((res) => {
        historyCache = res.records ?? [];
        if (alive) setHistory(historyCache);
      })
      .catch((e) => { if (alive) setHistErr(e.message); });
    return () => { alive = false; };
  }, []);

  const perf = data.performance;
  const bookAvg = perf.headline.win_rate;
  const partners = PARTNERS[kind] ?? ["rsd", "broker"];

  // The server's own row for this entity -- the numbers the leaderboard shows.
  const board = useMemo(
    () => (perf.leaderboards[kind] ?? []).find((r) => r.label === name) ?? null,
    [perf, kind, name]);

  const openQuotes = useMemo(
    () => data.groups
      .filter((g) => keyOf(g, kind) === name)
      .sort((a, b) => (b.expected_value ?? 0) - (a.expected_value ?? 0)),
    [data.groups, kind, name]);

  const decided = useMemo(
    () => (history ?? [])
      .filter((r) => keyOf(r, kind) === name)
      .sort((a, b) => String(b.eff_date ?? "").localeCompare(String(a.eff_date ?? ""))),
    [history, kind, name]);

  const openStats = useMemo(() => {
    const bands = {};
    let expWins = 0, expValue = 0, potential = 0;
    for (const g of openQuotes) {
      bands[g.likelihood_band] = (bands[g.likelihood_band] || 0) + 1;
      expWins += g.band_win_rate ?? 0;
      expValue += g.expected_value ?? 0;
      potential += g.potential_premium ?? 0;
    }
    return { bands, expWins, expValue, potential };
  }, [openQuotes]);

  const lossReasons = useMemo(() => {
    const tally = {};
    for (const r of decided) {
      if (r.won) continue;
      const reason = (r.loss_reason && String(r.loss_reason).trim())
        || (r.loss_reason_other && String(r.loss_reason_other).trim())
        || "Not recorded";
      tally[reason] = (tally[reason] || 0) + 1;
    }
    const lost = decided.filter((r) => !r.won).length;
    return { rows: Object.entries(tally).sort((a, b) => b[1] - a[1]), lost };
  }, [decided]);

  const better = board?.win_rate != null && board.win_rate >= bookAvg;
  const thin = board && !board.enough_volume;

  return (
    <>
      <div className="entity-head">
        <button className="btn-back" onClick={onBack}>
          <ArrowLeft size={14} /> Back to {backLabel}
        </button>
        <div className="entity-chips">
          <span className="chip">{KIND_LABEL[kind] ?? kind}</span>
          <span className="chip">{fmtNum(openQuotes.length)} open</span>
          {board && <span className="chip">{fmtNum(board.quotes)} decided</span>}
          {thin && <span className="chip">under {perf.min_volume} decided quotes</span>}
        </div>
      </div>

      {/* ------------------------------------------------------- headline */}
      <div className="grid grid-kpi">
        <KpiCard
          icon={Percent}
          label="All-time win rate"
          value={board?.win_rate == null ? "—" : fmtPct(board.win_rate, 1)}
          tone={board?.win_rate == null ? undefined : better ? "green" : undefined}
          foot={board
            ? `${fmtNum(board.wins)} won of ${fmtNum(board.quotes)} decided`
            : "no decided quotes on record"}
        />
        <KpiCard
          icon={TrendingUp}
          label={`vs. book average (${fmtPct(bookAvg, 1)})`}
          value={board?.win_rate == null ? "—"
            : `${(board.win_rate / bookAvg).toFixed(2)}×`}
          foot={board?.win_rate == null ? "—"
            : better ? "ahead of the book" : "behind the book"}
        />
        <KpiCard
          icon={DollarSign}
          label="Premium won (all time)"
          value={board?.premium_won == null ? "—" : fmtMoney(board.premium_won)}
          /* The coverage caveat hangs off the dollar figure, so it only shows
             when there IS one: an entity whose wins all lack a cost figure has
             premium_won=null but coverage=0, which read as "— across 0% of
             wins" when these were rendered independently. */
          foot={board?.premium_won == null ? "no cost figure on its wins"
            : board.premium_coverage != null
              ? `across ${fmtPct(board.premium_coverage, 0)} of wins`
              : undefined}
        />
        <KpiCard
          icon={Target}
          label="Open quotes"
          value={fmtNum(openQuotes.length)}
          foot={`${openStats.expWins.toFixed(1)} expected wins`}
        />
        <KpiCard
          icon={DollarSign}
          label="Expected value (open)"
          value={fmtMoney(openStats.expValue)}
          tone="green"
          foot={`${fmtMoney(openStats.potential)} potential premium`}
        />
      </div>

      {thin && (
        <div className="data-note">
          <Percent size={15} />
          <div>
            <b>Small sample — read the win rate with care.</b> {fmtNum(board.quotes)} decided
            quote{board.quotes === 1 ? "" : "s"} is under the {perf.min_volume}-quote bar the
            Sales Performance page uses before treating a win rate as a track record. The quote
            lists below are still complete; it's the percentage that isn't yet meaningful.
          </div>
        </div>
      )}

      {/* -------------------------------------------------- open pipeline */}
      <div className="grid grid-2-3">
        <div className="card">
          <div className="card-title">Open pipeline — likelihood mix</div>
          <div className="card-sub">Every open, undecided quote currently attributed here</div>
          <BandMixBar counts={openStats.bands} total={openQuotes.length} />
        </div>
        <div className="card">
          <div className="card-title">Win rate by year</div>
          <div className="card-sub">
            Settled years only — a year with quotes still open can only go up, so it stays out
            of the line and in the hover readout
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 14, padding: "10px 0" }}>
            <Spark points={board?.trend} avg={bookAvg} w={260} hgt={56} />
            <div className="cell-dim">
              dashed line = {fmtPct(bookAvg, 1)} book average
            </div>
          </div>
        </div>
      </div>

      <OpenQuotesCard rows={openQuotes} kind={kind} />

      {/* ----------------------------------------------- history sections */}
      {histErr ? (
        <div className="card">
          <div className="card-title">Decided history</div>
          <div className="driver-empty">Couldn&apos;t load decided quotes: {histErr}</div>
        </div>
      ) : history == null ? (
        <div className="card">
          <div className="card-title">Decided history</div>
          <div className="driver-empty">Loading every decided quote…</div>
        </div>
      ) : (
        <>
          <div className="grid grid-2">
            <div className="card">
              <div className="card-title">Why quotes were lost</div>
              <div className="card-sub">
                {fmtNum(lossReasons.lost)} lost quote{lossReasons.lost === 1 ? "" : "s"}, by
                recorded reason
              </div>
              {lossReasons.rows.length === 0 ? (
                <div className="driver-empty">No lost quotes on record.</div>
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr><th>Loss reason</th><th>Quotes</th><th>Share of losses</th></tr>
                    </thead>
                    <tbody>
                      {lossReasons.rows.map(([reason, n]) => (
                        <tr key={reason}>
                          <td className="cell-main">{reason}</td>
                          <td className="mono">{fmtNum(n)}</td>
                          <td className="mono">{fmtPct(n / lossReasons.lost, 0)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
            <div className="card">
              <div className="card-title">Effective-month seasonality</div>
              <div className="card-sub">
                Which months this {(KIND_LABEL[kind] ?? kind).toLowerCase()}&apos;s business
                actually lands in — year-over-year trend is the spark above
              </div>
              <MixTable rows={monthMix(decided)} label="Month" avg={bookAvg} />
            </div>
          </div>

          <div className="grid grid-2">
            {partners.map((p) => (
              <div className="card" key={p}>
                <div className="card-title">Track record by {KIND_LABEL[p].toLowerCase()}</div>
                <div className="card-sub">Decided quotes only, ranked by volume not by rate</div>
                <MixTable rows={mixBy(decided, p)} label={KIND_LABEL[p]} avg={bookAvg} />
              </div>
            ))}
            {partners.length === 1 && (
              <div className="card">
                <div className="card-title">
                  Track record by {kind === "product" ? "industry" : "product"}
                </div>
                <div className="card-sub">Decided quotes only, ranked by volume not by rate</div>
                <MixTable
                  rows={mixBy(decided, kind === "product" ? "industry" : "product")}
                  label={kind === "product" ? "Industry" : "Product"}
                  avg={bookAvg}
                />
              </div>
            )}
          </div>

          <DecidedQuotesCard rows={decided} kind={kind} />
        </>
      )}
    </>
  );
}

// ------------------------------------------------------------------ tables

// Both long tables get their own component with their own search/page state,
// so typing in one doesn't re-render the other (or the charts above).
function OpenQuotesCard({ rows, kind }) {
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((g) => [g.group_name, g.broker, g.rsd, g.product, g.industry,
                               g.stage, g.likelihood_band]
      .some((v) => v && String(v).toLowerCase().includes(q)));
  }, [rows, query]);

  const { pageRows, pageCount, clamped } = paginate(filtered, page);

  return (
    <div className="card">
      <div className="card-head-row">
        <div>
          <div className="card-title">Open quotes — {fmtNum(rows.length)}</div>
          <div className="card-sub">
            Every undecided quote currently attributed here, ranked by expected value —
            work the top of this list first
          </div>
        </div>
        <TableSearch value={query} onChange={(v) => { setQuery(v); setPage(0); }} />
      </div>
      {rows.length === 0 ? (
        <div className="driver-empty">Nothing open right now.</div>
      ) : (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Group</th>
                  <th>Likelihood</th>
                  <th>Historical Rate</th>
                  <th>Expected Value</th>
                  <th>Potential Premium</th>
                  <th>Lives</th>
                  <th>Effective</th>
                  <th>Stage</th>
                </tr>
              </thead>
              <tbody>
                {pageRows.map((g) => (
                  <tr key={g.quote_id}>
                    <td>
                      <div className="cell-main">{g.group_name}</div>
                      <div className="cell-dim">
                        {[g.product, kind === "broker" ? g.rsd : g.broker, g.industry]
                          .filter(Boolean).join(" · ") || "—"}
                      </div>
                    </td>
                    <td><LikelihoodBadge band={g.likelihood_band} /></td>
                    <td className="mono" style={{ color: NB_BAND_COLORS[g.likelihood_band] }}>
                      {fmtPct(g.band_win_rate, 1)}
                    </td>
                    <td className="mono">{fmtMoney(g.expected_value)}</td>
                    <td className="mono">{fmtMoney(g.potential_premium)}</td>
                    <td className="mono">{fmtNum(g.lives)}</td>
                    <td className="mono">{fmtDate(g.eff_date)}</td>
                    <td className="cell-dim">{g.stage || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager count={filtered.length} noun="open quote" page={clamped}
                 pageCount={pageCount} setPage={setPage} />
        </>
      )}
    </div>
  );
}

function DecidedQuotesCard({ rows, kind }) {
  const [query, setQuery] = useState("");
  const [outcome, setOutcome] = useState("all");
  const [page, setPage] = useState(0);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rows.filter((r) => {
      if (outcome === "won" && !r.won) return false;
      if (outcome === "lost" && r.won) return false;
      if (!q) return true;
      return [r.group_name, r.broker, r.rsd, r.product, r.industry,
              r.loss_reason, r.loss_reason_other]
        .some((v) => v && String(v).toLowerCase().includes(q));
    });
  }, [rows, query, outcome]);

  const { pageRows, pageCount, clamped } = paginate(filtered, page);
  const wins = rows.filter((r) => r.won).length;

  return (
    <div className="card">
      <div className="card-head-row">
        <div>
          <div className="card-title">Decided quotes — {fmtNum(rows.length)}</div>
          <div className="card-sub">
            The complete Win/Loss record attributed here: {fmtNum(wins)} won,{" "}
            {fmtNum(rows.length - wins)} lost. Most recent effective date first.
          </div>
        </div>
        <div className="filter-row">
          <TableSearch value={query} onChange={(v) => { setQuery(v); setPage(0); }} />
          <select className="filter" value={outcome}
                  onChange={(e) => { setOutcome(e.target.value); setPage(0); }}>
            <option value="all">All outcomes</option>
            <option value="won">Won only</option>
            <option value="lost">Lost only</option>
          </select>
        </div>
      </div>
      {rows.length === 0 ? (
        <div className="driver-empty">No decided quotes on record.</div>
      ) : (
        <>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Group</th>
                  <th>Outcome</th>
                  <th>Loss reason</th>
                  <th>Lives</th>
                  <th>Effective</th>
                  <th>Quoted</th>
                </tr>
              </thead>
              <tbody>
                {pageRows.map((r, i) => (
                  <tr key={`${r.group_name}-${r.eff_date}-${i}`}>
                    <td>
                      <div className="cell-main">{r.group_name}</div>
                      <div className="cell-dim">
                        {[r.product, kind === "broker" ? r.rsd : r.broker, r.industry]
                          .filter(Boolean).join(" · ") || "—"}
                      </div>
                    </td>
                    <td>
                      <span className={`badge ${r.won ? "badge-High" : "badge-VeryLow"}`}>
                        {r.won ? "Won" : "Lost"}
                      </span>
                    </td>
                    <td className="cell-dim">
                      {r.won ? "—" : (r.loss_reason || r.loss_reason_other || "not recorded")}
                    </td>
                    <td className="mono">{fmtNum(r.lives)}</td>
                    <td className="mono">{fmtDate(r.eff_date)}</td>
                    <td className="mono">{fmtDate(r.created_date)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager count={filtered.length} noun="decided quote" page={clamped}
                 pageCount={pageCount} setPage={setPage} />
        </>
      )}
    </div>
  );
}

// ----------------------------------------------------------------- helpers

function paginate(rows, page) {
  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const clamped = Math.min(page, pageCount - 1);
  return {
    pageRows: rows.slice(clamped * PAGE_SIZE, (clamped + 1) * PAGE_SIZE),
    pageCount,
    clamped,
  };
}

function TableSearch({ value, onChange }) {
  return (
    <div className="search-box">
      <Search size={15} />
      <input
        placeholder="Search group, broker, rep, product, industry…"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

function Pager({ count, noun, page, pageCount, setPage }) {
  return (
    <div className="pager">
      <span>
        {fmtNum(count)} {noun}{count === 1 ? "" : "s"} · page {page + 1} of {pageCount}
      </span>
      <div className="pager-btns">
        <button disabled={page === 0} onClick={() => setPage(0)}>« First</button>
        <button disabled={page === 0} onClick={() => setPage(page - 1)}>‹ Prev</button>
        <button disabled={page >= pageCount - 1} onClick={() => setPage(page + 1)}>Next ›</button>
        <button disabled={page >= pageCount - 1} onClick={() => setPage(pageCount - 1)}>Last »</button>
      </div>
    </div>
  );
}

// Win rate per value of `col` among this entity's decided quotes. Sorted by
// volume, not by rate: a 100% rate on one quote would otherwise headline the
// table over a 12% rate on eighty, which is the wrong read every time.
function mixBy(rows, col) {
  const tally = {};
  for (const r of rows) {
    const k = keyOf(r, col);
    tally[k] = tally[k] || { label: k, quotes: 0, wins: 0 };
    tally[k].quotes += 1;
    tally[k].wins += r.won ? 1 : 0;
  }
  return Object.values(tally)
    .map((t) => ({ ...t, win_rate: t.quotes ? t.wins / t.quotes : null }))
    .sort((a, b) => b.quotes - a.quotes);
}

// Effective MONTH (not year) -- the seasonality question an exec actually asks
// of a rep or broker ("do they only show up at 1/1?"). Year-over-year trend is
// already the spark above, so this axis adds something instead of repeating it.
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function monthMix(rows) {
  const tally = {};
  for (const r of rows) {
    const m = String(r.eff_date ?? "").match(/^\d{4}-(\d{2})/);
    if (!m) continue;
    const k = MONTHS[Number(m[1]) - 1];
    tally[k] = tally[k] || { label: k, quotes: 0, wins: 0 };
    tally[k].quotes += 1;
    tally[k].wins += r.won ? 1 : 0;
  }
  return MONTHS
    .filter((m) => tally[m])
    .map((m) => ({ ...tally[m], win_rate: tally[m].quotes ? tally[m].wins / tally[m].quotes : null }));
}

function MixTable({ rows, label, avg }) {
  if (!rows.length) return <div className="driver-empty">Nothing on record.</div>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>{label}</th><th>Quotes</th><th>Wins</th><th>Win Rate</th><th>vs Avg</th></tr>
        </thead>
        <tbody>
          {rows.slice(0, 12).map((r) => (
            <tr key={r.label}>
              <td className="cell-main">{r.label}</td>
              <td className="mono">{fmtNum(r.quotes)}</td>
              <td className="mono">{fmtNum(r.wins)}</td>
              <td className="mono">{fmtPct(r.win_rate, 1)}</td>
              <td><RateBar rate={r.win_rate} avg={avg} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 12 && (
        <div className="card-note">
          <HoverTip label={rows.slice(12).map((r) => `${r.label}: ${r.wins}/${r.quotes}`).join("\n")}>
            +{rows.length - 12} more with fewer quotes
          </HoverTip>
        </div>
      )}
    </div>
  );
}
