import { useEffect, useMemo, useState } from "react";
import { CalendarRange, Database, Percent, Search, TrendingDown, X } from "lucide-react";
import { api } from "../api.js";
import { fmtDate, fmtMoney, fmtNum } from "../format.js";
import { KpiCard } from "../components/shared.jsx";

const PAGE_SIZE = 25;

const dash = (v) => (v == null || v === "" ? "—" : v);
const yesno = (v) => (v == null ? "—" : v ? "Yes" : "No");
// ratio fields (nlr, isl_loss_ratio, mature_to_attachment, corridor) are stored as
// fractions (0.15 = 15%); the *_increase_pct fields are already percent-scale.
const ratioPct = (v) => (v == null ? "—" : `${Math.round(v * 100)}%`);
const rawPct = (v) => (v == null ? "—" : `${Number(v).toFixed(1)}%`);

const COLUMNS = [
  { key: "group_id", label: "Group ID" },
  { key: "group_name", label: "Group" },
  { key: "eff_date", label: "Renewal Date" },
  { key: "renewed", label: "Outcome" },
  { key: "product", label: "Product" },
  { key: "lives", label: "Lives" },
  { key: "premium", label: "Premium" },
  { key: "nlr", label: "Loss Ratio" },
  { key: "agg_loss_ratio", label: "Agg Loss Ratio" },
  { key: "ratio_to_attachment", label: "Ratio to Attach" },
  { key: "total_increase_pct", label: "Increase" },
  { key: "lasers_renewal", label: "Lasers" },
  { key: "tenure_years", label: "Tenure" },
  { key: "state", label: "State" },
  { key: "broker", label: "Broker" },
  { key: "am", label: "AM" },
  { key: "carrier", label: "Carrier" },
  { key: "source_label", label: "Source" },
];

export default function RenewalDatabase() {
  const [db, setDb] = useState(null);
  const [err, setErr] = useState(null);
  const [query, setQuery] = useState("");
  const [outcome, setOutcome] = useState("all");
  const [year, setYear] = useState("all");
  const [source, setSource] = useState("all");
  const [sortKey, setSortKey] = useState("eff_date");
  const [sortDir, setSortDir] = useState(-1);
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    api.renewalDatabase().then(setDb).catch((e) => setErr(e.message));
  }, []);

  const records = useMemo(
    () => (db?.records ?? []).map((r) => ({ ...r, premium: r.annual_premium ?? r.premium_stoploss })),
    [db]
  );

  const years = useMemo(
    () => [...new Set(records.map((r) => r.eff_date.slice(0, 4)))].sort().reverse(),
    [records]
  );

  const sources = useMemo(() => {
    const seen = new Map();
    records.forEach((r) => r.source && seen.set(r.source, r.source_label));
    return [...seen.entries()];
  }, [records]);

  const rows = useMemo(() => {
    let out = records;
    if (outcome !== "all") out = out.filter((r) => (outcome === "renewed" ? r.renewed : !r.renewed));
    if (year !== "all") out = out.filter((r) => r.eff_date.slice(0, 4) === year);
    if (source !== "all") out = out.filter((r) => r.source === source);
    if (query) {
      const q = query.toLowerCase();
      out = out.filter((r) =>
        [r.group_id, r.group_name, r.broker, r.am, r.rsd, r.tpa, r.carrier, r.state].some(
          (f) => f && String(f).toLowerCase().includes(q)
        )
      );
    }
    out = [...out].sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "string") return av.localeCompare(bv) * sortDir;
      return (av - bv) * sortDir;
    });
    return out;
  }, [records, query, outcome, year, source, sortKey, sortDir]);

  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const pageRows = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  const sortBy = (key) => {
    if (key === sortKey) setSortDir(-sortDir);
    else {
      setSortKey(key);
      setSortDir(1);
    }
    setPage(0);
  };

  if (err)
    return <div className="card" style={{ color: "var(--red)" }}>Couldn't load the renewal database — {err}</div>;
  if (!db) return <div className="card">Loading the renewal database…</div>;
  if (!db.available)
    return (
      <div className="card">
        No real renewal history is loaded yet. This page reads{" "}
        <span className="mono">real_history.csv</span> directly, independent of the active mode.
      </div>
    );

  return (
    <>
      <div className="grid grid-kpi">
        <KpiCard
          icon={Database}
          label="Renewal decisions"
          value={fmtNum(db.count)}
          foot="every historical record the model trains on"
        />
        <KpiCard
          icon={CalendarRange}
          label="Date range"
          value={`${fmtDate(db.date_start)} – ${fmtDate(db.date_end)}`}
        />
        <KpiCard
          icon={Percent}
          label="Overall renewal rate"
          value={`${Math.round(db.renewal_rate * 100)}%`}
        />
        <KpiCard
          icon={TrendingDown}
          label="Lapsed"
          value={fmtNum(Math.round(db.count * (1 - db.renewal_rate)))}
          tone="red"
          foot="renewals that termed"
        />
      </div>

      <div className="card">
        <div className="table-tools">
          <div className="search-box">
            <Search size={15} />
            <input
              placeholder="Search group, broker, AM, RSD, TPA, carrier, state…"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setPage(0);
              }}
            />
          </div>
          <select className="filter" value={outcome} onChange={(e) => { setOutcome(e.target.value); setPage(0); }}>
            <option value="all">All outcomes</option>
            <option value="renewed">Renewed</option>
            <option value="termed">Termed</option>
          </select>
          <select className="filter" value={year} onChange={(e) => { setYear(e.target.value); setPage(0); }}>
            <option value="all">All years</option>
            {years.map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
          <select className="filter" value={source} onChange={(e) => { setSource(e.target.value); setPage(0); }}>
            <option value="all">All sources</option>
            {sources.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
          </select>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {COLUMNS.map((c) => (
                  <th key={c.key} onClick={() => sortBy(c.key)}>
                    {c.label}
                    {sortKey === c.key ? (sortDir === 1 ? " ↑" : " ↓") : ""}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {pageRows.map((r) => (
                <tr key={r.row_id} onClick={() => setSelected(r)}>
                  <td className="mono cell-dim">{r.group_id}</td>
                  <td><div className="cell-main">{r.group_name}</div></td>
                  <td className="mono">{fmtDate(r.eff_date)}</td>
                  <td><span className={`badge badge-${r.renewed ? "Renewed" : "Termed"}`}>{r.renewed ? "Renewed" : "Termed"}</span></td>
                  <td className="cell-dim">{dash(r.product)}</td>
                  <td className="mono">{r.lives == null ? "—" : fmtNum(Math.round(r.lives))}</td>
                  <td className="mono">{fmtMoney(r.premium)}</td>
                  <td className="mono">{ratioPct(r.nlr)}</td>
                  <td className="mono">{ratioPct(r.agg_loss_ratio)}</td>
                  <td className="mono">{ratioPct(r.ratio_to_attachment)}</td>
                  <td className="mono">{rawPct(r.total_increase_pct)}</td>
                  <td className="mono">{r.lasers_renewal == null ? "—" : fmtNum(r.lasers_renewal)}</td>
                  <td className="mono">{r.tenure_years == null ? "—" : `${fmtNum(r.tenure_years)}y`}</td>
                  <td className="mono">{dash(r.state)}</td>
                  <td className="cell-dim">{dash(r.broker)}</td>
                  <td className="cell-dim">{dash(r.am)}</td>
                  <td className="cell-dim">{dash(r.carrier)}</td>
                  <td className="cell-dim">{dash(r.source_label)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="pager">
          <span>{fmtNum(rows.length)} records · page {page + 1} of {pageCount}</span>
          <div className="pager-btns">
            <button disabled={page === 0} onClick={() => setPage(0)}>« First</button>
            <button disabled={page === 0} onClick={() => setPage(page - 1)}>‹ Prev</button>
            <button disabled={page >= pageCount - 1} onClick={() => setPage(page + 1)}>Next ›</button>
            <button disabled={page >= pageCount - 1} onClick={() => setPage(pageCount - 1)}>Last »</button>
          </div>
        </div>
      </div>

      {selected && <RecordDrawer record={selected} onClose={() => setSelected(null)} />}
    </>
  );
}

function Fact({ k, v }) {
  const blank = v == null || v === "—";
  return (
    <div className="fact">
      <div className="k">{k}</div>
      <div className={`v${blank ? " v-blank" : ""}`}>{v ?? "—"}</div>
    </div>
  );
}

function RecordDrawer({ record: r, onClose }) {
  const chips = [r.product, r.state && r.state !== "—" ? r.state : null, r.source_label].filter(Boolean);
  return (
    <>
      <div className="drawer-overlay" onClick={onClose} />
      <aside className="drawer" data-outcome={r.renewed ? "renewed" : "termed"}>
        <div className="drawer-accent" />
        <div className="drawer-wash" aria-hidden="true" />
        <button className="drawer-close" onClick={onClose}><X size={15} /></button>

        <div className="drawer-head">
          <h2>{r.group_name}</h2>
          <div className="drawer-chips">
            <span className="chip chip-outcome">{r.renewed ? "Renewed" : "Termed"}</span>
            {chips.map((c) => <span className="chip" key={c}>{c}</span>)}
          </div>
        </div>

        <div className="drawer-section-title">Identity &amp; outcome</div>
        <div className="fact-grid">
          <Fact k="Renewal date" v={fmtDate(r.eff_date)} />
          <Fact k="Outcome" v={r.renewed ? "Renewed" : "Termed"} />
          <Fact k="Product" v={dash(r.product)} />
          <Fact k="Source" v={dash(r.source_label)} />
        </div>

        <div className="drawer-section-title">Relationship &amp; team</div>
        <div className="fact-grid">
          <Fact k="Broker" v={dash(r.broker)} />
          <Fact k="Account manager" v={dash(r.am)} />
          <Fact k="RSD" v={dash(r.rsd)} />
          <Fact k="TPA" v={dash(r.tpa)} />
          <Fact k="Carrier" v={dash(r.carrier)} />
          <Fact k="State" v={dash(r.state)} />
          <Fact k="Network" v={dash(r.network)} />
        </div>

        <div className="drawer-section-title">Underwriting signal</div>
        <div className="fact-grid">
          <Fact k="Enrolled lives" v={r.lives == null ? "—" : fmtNum(Math.round(r.lives))} />
          <Fact k="Annual premium" v={fmtMoney(r.annual_premium)} />
          <Fact k="Stop-loss premium" v={fmtMoney(r.premium_stoploss)} />
          <Fact k="Net loss ratio" v={ratioPct(r.nlr)} />
          <Fact k="Aggregate loss ratio" v={ratioPct(r.agg_loss_ratio)} />
          <Fact k="Ratio to attachment" v={ratioPct(r.ratio_to_attachment)} />
          <Fact k="ISL loss ratio" v={ratioPct(r.isl_loss_ratio)} />
          <Fact k="Mature claims to attachment" v={ratioPct(r.mature_to_attachment)} />
          <Fact k="Aggregate corridor" v={ratioPct(r.corridor)} />
          <Fact k="Total renewal increase" v={rawPct(r.total_increase_pct)} />
          <Fact k="Initial UW increase" v={rawPct(r.initial_uw_increase_pct)} />
          <Fact k="Fixed-cost increase" v={rawPct(r.fixed_increase_pct)} />
        </div>

        <div className="drawer-section-title">Lasers</div>
        <div className="fact-grid">
          <Fact k="Lasers (current)" v={r.lasers_current == null ? "—" : fmtNum(r.lasers_current)} />
          <Fact k="Lasers (renewal)" v={r.lasers_renewal == null ? "—" : fmtNum(r.lasers_renewal)} />
          <Fact k="Laser liability $" v={fmtMoney(r.laser_liability)} />
        </div>

        <div className="drawer-section-title">History &amp; tenure</div>
        <div className="fact-grid">
          <Fact k="Years with Crumdale" v={r.tenure_years == null ? "—" : `${fmtNum(r.tenure_years)}y`} />
          <Fact k="Tenure derived (proxy)" v={yesno(r.tenure_is_derived)} />
          <Fact k="Broker-of-record change" v={yesno(r.bor_change)} />
          <Fact k="BOR change confirmed" v={yesno(r.bor_change_confirmed)} />
          <Fact k="Captive offer" v={yesno(r.captive_offer)} />
          <Fact k="Carrier changed" v={yesno(r.carrier_changed)} />
          <Fact k="AM changed" v={yesno(r.am_changed)} />
        </div>

        <div className="drawer-section-title">Negotiation detail</div>
        <div className="fact-grid">
          <Fact k="Initial increase (negotiation)" v={rawPct(r.neg_initial_increase_pct)} />
          <Fact k="Final increase (negotiation)" v={rawPct(r.neg_final_increase_pct)} />
        </div>

        <div className="drawer-section-title">Broker relationship</div>
        <div className="fact-grid">
          <Fact k="Broker years with CS" v={r.broker_years_with_cs == null ? "—" : fmtNum(r.broker_years_with_cs)} />
          <Fact k="Broker groups with CS" v={r.broker_groups_with_cs == null ? "—" : fmtNum(r.broker_groups_with_cs)} />
          <Fact k="Broker products sold" v={r.broker_products_sold == null ? "—" : fmtNum(r.broker_products_sold)} />
          <Fact k="Preferred broker" v={yesno(r.broker_preferred)} />
        </div>
      </aside>
    </>
  );
}
