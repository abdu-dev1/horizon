import { useEffect, useMemo, useState } from "react";
import { Database, Percent, Search, Target } from "lucide-react";
import { apiNb } from "../../apiNb.js";
import { fmtNum, fmtPct } from "../../format.js";
import { KpiCard } from "../../components/shared.jsx";
import { NB_COLUMNS, renderNbCell as renderCell } from "./nbColumns.jsx";

const PAGE_SIZE = 25;

// Decided quotes were never scored by the model (see build_book.py -- only
// the still-open pipeline gets scored), so the pipeline-only columns
// (expected value, historical win rate, likelihood band, stage estimate, pipeline rank)
// are dropped here entirely rather than shown as a column of permanent
// dashes -- see nbColumns.jsx's pipelineOnly note.
const COLUMNS = NB_COLUMNS.filter((c) => !c.pipelineOnly);

export default function NbDatabase() {
  const [db, setDb] = useState(null);
  const [err, setErr] = useState(null);
  const [query, setQuery] = useState("");
  const [outcome, setOutcome] = useState("all");
  const [product, setProduct] = useState("all");
  const [sortKey, setSortKey] = useState("eff_date");
  const [sortDir, setSortDir] = useState(-1);
  const [page, setPage] = useState(0);

  useEffect(() => {
    apiNb.history().then(setDb).catch((e) => setErr(e.message));
  }, []);

  const records = db?.records ?? [];
  const winRate = records.length ? records.filter((r) => r.won).length / records.length : null;

  const products = useMemo(
    () => [...new Set(records.map((r) => r.product).filter(Boolean))].sort(),
    [records]
  );

  const rows = useMemo(() => {
    let out = records;
    if (outcome !== "all") out = out.filter((r) => (outcome === "won" ? r.won : !r.won));
    if (product !== "all") out = out.filter((r) => r.product === product);
    if (query) {
      const q = query.toLowerCase();
      out = out.filter(
        (r) =>
          r.group_name.toLowerCase().includes(q) ||
          (r.broker || "").toLowerCase().includes(q) ||
          (r.rsd || "").toLowerCase().includes(q) ||
          (r.industry || "").toLowerCase().includes(q)
      );
    }
    out = [...out].sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey];
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "string") return av.localeCompare(bv) * sortDir;
      return (av - bv) * sortDir;
    });
    return out;
  }, [records, query, outcome, product, sortKey, sortDir]);

  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const pageRows = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  const sortBy = (key) => {
    if (key === sortKey) setSortDir(-sortDir);
    else { setSortKey(key); setSortDir(1); }
    setPage(0);
  };

  if (err) return <div className="card"><div className="card-title">Cannot load history</div><div className="cell-dim">{err}</div></div>;
  if (!db) return <div className="loading-screen" style={{ position: "static" }}><div className="spinner" /></div>;

  return (
    <>
      <div className="grid grid-kpi">
        <KpiCard icon={Database} label="Decided Quotes" value={fmtNum(records.length)} />
        <KpiCard icon={Target} label="Won" value={fmtNum(records.filter((r) => r.won).length)} tone="green" />
        <KpiCard icon={Percent} label="Win Rate" value={fmtPct(winRate, 1)} />
      </div>

      <div className="card">
        <div className="card-title">Win/Loss Database — every decided quote</div>
        <div className="card-sub">
          Every historical New Business quote (Closed Won or Closed Lost), with EVERY field the
          model trains on (not a curated subset) — scroll right to audit any row. The last 3
          columns (Loss Reason, Loss Reason - Other, Notice of Sale Date) are explicitly NOT model
          features — they're only knowable after the decision, so training on them would be
          leakage; shown here purely as historical context.
        </div>
        <div className="table-tools">
          <div className="search-box">
            <Search size={15} />
            <input
              placeholder="Search group, broker, RSD, industry…"
              value={query}
              onChange={(e) => { setQuery(e.target.value); setPage(0); }}
            />
          </div>
          <select className="filter" value={outcome} onChange={(e) => { setOutcome(e.target.value); setPage(0); }}>
            <option value="all">Won + Lost</option>
            <option value="won">Won only</option>
            <option value="lost">Lost only</option>
          </select>
          <select className="filter" value={product} onChange={(e) => { setProduct(e.target.value); setPage(0); }}>
            <option value="all">All products</option>
            {products.map((p) => <option key={p}>{p}</option>)}
          </select>
        </div>

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                {COLUMNS.map((c) => (
                  <th key={c.key} onClick={() => sortBy(c.key)}>
                    {c.label}{sortKey === c.key ? (sortDir === 1 ? " ↑" : " ↓") : ""}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {pageRows.map((r, i) => (
                <tr key={i} style={{ cursor: "default" }}>
                  {COLUMNS.map((c) => {
                    // No stable id for a historical record (see NbPipeline.jsx's
                    // quote_id note) -- nothing to show, so this column is blank here.
                    if (c.key === "quote_id") return <td key={c.key} className="mono cell-dim">—</td>;
                    if (c.key === "group_name") return <td key={c.key} className="cell-main">{r.group_name}</td>;
                    if (c.key === "won") return (
                      <td key={c.key}>
                        <span className={`badge badge-${r.won ? "Renewed" : "Termed"}`}>{r.won ? "Won" : "Lost"}</span>
                      </td>
                    );
                    return <td key={c.key}>{renderCell(r, c)}</td>;
                  })}
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
    </>
  );
}
