import { useEffect, useMemo, useState } from "react";
import { Search, Trash2, X } from "lucide-react";
import { apiNb } from "../../apiNb.js";
import { NB_BAND_COLORS, fmtMoney, fmtNum, fmtPct } from "../../format.js";
import { ProbCell, LikelihoodBadge } from "../../components/shared.jsx";
import { NB_COLUMNS as COLUMNS, dash, renderNbCell as renderCell, yesno } from "./nbColumns.jsx";

const BAND_RANK = { High: 0, Moderate: 1, Low: 2, "Very Low": 3 };
const PAGE_SIZE = 25;

// Historical win rate is headlined instead of the model's raw per-quote score
// (see backend/app/insights_nb.py's score_book docstring) -- color still
// tracks the TIER, a direct lookup, not re-bucketing the rate number itself.
function TierRateCell({ g }) {
  return (
    <div>
      <ProbCell p={g.band_win_rate} showTag={false} colorFn={() => NB_BAND_COLORS[g.likelihood_band]} />
      <div className="cell-dim" style={{ fontSize: 10.5, marginTop: 2 }}>
        {g.band_wins ?? 0} of {g.band_n ?? 0} similar quotes historically
      </div>
    </div>
  );
}

export default function NbPipeline({ data, onDataChange, onNotify }) {
  const [query, setQuery] = useState("");
  const [band, setBand] = useState("all");
  const [product, setProduct] = useState("all");
  // Default view is "priority" -- band first (High before Very Low, always), then
  // expected value as the tiebreak within a band. Sorting by raw dollar value
  // ALONE (clicking the Expected Value column) can put a huge-premium Very Low
  // quote above a genuinely promising High one -- mathematically correct
  // expected-value math, but confusing as a default "work this first" list.
  const [sortKey, setSortKey] = useState("priority");
  const [sortDir, setSortDir] = useState(-1);
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState(null);

  // Stage editor: every possible stage, including the two decisions -- fetched
  // from the backend (nb_mode.STAGE_OPTIONS) rather than duplicated here, so
  // this dropdown can never drift out of sync with what the server accepts.
  const [stageOptions, setStageOptions] = useState([]);
  const [editingStageId, setEditingStageId] = useState(null);
  const [savingStageId, setSavingStageId] = useState(null);

  useEffect(() => {
    apiNb.stageOptions().then((r) => setStageOptions(r.options)).catch(() => {});
  }, []);

  const handleStageChange = async (g, newStage) => {
    setEditingStageId(null);
    if (newStage === g.stage) return;
    setSavingStageId(g.quote_id);
    try {
      const result = await apiNb.updateStage(g.quote_id, newStage);
      await onDataChange?.();
      onNotify?.(
        result.moved_to_history
          ? `${g.group_name} set to "${newStage}" — moved to the Win/Loss Database.`
          : `${g.group_name} stage updated to "${newStage}".`
      );
    } catch (e) {
      onNotify?.(`Failed to update stage: ${e.message}`);
    } finally {
      setSavingStageId(null);
    }
  };

  const products = useMemo(
    () => [...new Set(data.groups.map((g) => g.product).filter(Boolean))].sort(),
    [data.groups]
  );

  const rows = useMemo(() => {
    let out = data.groups;
    if (band !== "all") out = out.filter((g) => g.likelihood_band === band);
    if (product !== "all") out = out.filter((g) => g.product === product);
    if (query) {
      const q = query.toLowerCase();
      out = out.filter(
        (g) =>
          g.group_name.toLowerCase().includes(q) ||
          (g.broker || "").toLowerCase().includes(q) ||
          (g.rsd || "").toLowerCase().includes(q) ||
          (g.industry || "").toLowerCase().includes(q)
      );
    }
    if (sortKey === "priority") {
      out = [...out].sort((a, b) => {
        const rank = (g) => BAND_RANK[g.likelihood_band] ?? 99;
        if (rank(a) !== rank(b)) return rank(a) - rank(b);
        return (b.expected_value ?? 0) - (a.expected_value ?? 0);
      });
    } else {
      out = [...out].sort((a, b) => {
        const av = a[sortKey], bv = b[sortKey];
        if (av == null) return 1;
        if (bv == null) return -1;
        if (typeof av === "string") return av.localeCompare(bv) * sortDir;
        return (av - bv) * sortDir;
      });
    }
    return out;
  }, [data.groups, query, band, product, sortKey, sortDir]);

  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const pageRows = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  const sortBy = (key) => {
    if (key === sortKey) setSortDir(-sortDir);
    else {
      setSortKey(key);
      setSortDir(-1);
    }
    setPage(0);
  };

  return (
    <>
      <div className="card">
        <div className="card-title">Open Pipeline — {rows.length} quotes</div>
        <div className="card-sub">
          Every quote not yet Closed Won or Closed Lost, with EVERY field the model uses to score
          it (not a curated subset) — scroll right to audit any row. Default order is High → Very Low,
          then expected value within each band — click any column to re-sort.
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
          <select className="filter" value={band} onChange={(e) => { setBand(e.target.value); setPage(0); }}>
            <option value="all">All bands</option>
            <option>High</option>
            <option>Moderate</option>
            <option>Low</option>
            <option>Very Low</option>
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
                    {c.label}
                    {sortKey === c.key ? (sortDir === 1 ? " ↑" : " ↓") : ""}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {pageRows.map((g) => (
                <tr key={g.quote_id} onClick={() => setSelected(g)}>
                  {COLUMNS.map((c) => {
                    if (c.key === "quote_id") return <td key={c.key} className="mono cell-dim">{g.quote_id}</td>;
                    if (c.key === "group_name") return <td key={c.key} className="cell-main">{g.group_name}</td>;
                    if (c.key === "band_win_rate") return <td key={c.key}><TierRateCell g={g} /></td>;
                    if (c.key === "likelihood_band") return <td key={c.key}><LikelihoodBadge band={g.likelihood_band} /></td>;
                    if (c.key === "pipeline_rank") return <td key={c.key} className="mono cell-dim">#{g.pipeline_rank} of {data.groups.length}</td>;
                    if (c.key === "stage") return (
                      <td key={c.key} onClick={(e) => e.stopPropagation()}>
                        {savingStageId === g.quote_id ? (
                          <span className="cell-dim">Saving…</span>
                        ) : editingStageId === g.quote_id ? (
                          <select
                            autoFocus
                            defaultValue={g.stage}
                            onBlur={() => setEditingStageId(null)}
                            onChange={(e) => handleStageChange(g, e.target.value)}
                          >
                            {stageOptions.map((s) => <option key={s} value={s}>{s}</option>)}
                          </select>
                        ) : (
                          <span className="stage-editable" onClick={() => setEditingStageId(g.quote_id)}>
                            {dash(g.stage)} <span style={{ opacity: 0.5 }}>▾</span>
                          </span>
                        )}
                      </td>
                    );
                    return <td key={c.key}>{renderCell(g, c)}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="pager">
          <span>{fmtNum(rows.length)} quotes · page {page + 1} of {pageCount}</span>
          <div className="pager-btns">
            <button disabled={page === 0} onClick={() => setPage(0)}>« First</button>
            <button disabled={page === 0} onClick={() => setPage(page - 1)}>‹ Prev</button>
            <button disabled={page >= pageCount - 1} onClick={() => setPage(page + 1)}>Next ›</button>
            <button disabled={page >= pageCount - 1} onClick={() => setPage(pageCount - 1)}>Last »</button>
          </div>
        </div>
      </div>

      {selected && (
        <QuoteDrawer
          group={selected}
          total={data.groups.length}
          onClose={() => setSelected(null)}
          onDeleted={async () => {
            setSelected(null);
            await onDataChange?.();
            onNotify?.(`${selected.group_name} removed from the Open Pipeline.`);
          }}
        />
      )}
    </>
  );
}

function RadialGauge({ p, color, caption }) {
  const r = 54;
  const c = 2 * Math.PI * r;
  return (
    <div className="gauge">
      <svg viewBox="0 0 130 130">
        <circle className="gauge-track" cx="65" cy="65" r={r} fill="none" strokeWidth="11" />
        <circle
          cx="65" cy="65" r={r} fill="none" strokeWidth="11" strokeLinecap="round"
          stroke={color}
          strokeDasharray={c}
          strokeDashoffset={c * (1 - (p ?? 0))}
          transform="rotate(-90 65 65)"
          style={{ transition: "stroke-dashoffset 0.6s cubic-bezier(.2,.7,.2,1), stroke 0.3s" }}
        />
      </svg>
      <div className="gauge-center">
        <div className="gauge-pct" style={{ color }}>{p == null ? "—" : fmtPct(p, 0)}</div>
        <div className="gauge-cap">{caption}</div>
      </div>
    </div>
  );
}

export function QuoteDrawer({ group, total, onClose, onDeleted }) {
  const color = NB_BAND_COLORS[group.likelihood_band] || "#94a3b8";
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);

  const chips = [
    group.product,
    group.billing_state,
    group.stage,
  ].filter(Boolean);

  const handleDelete = async () => {
    if (!window.confirm(
      `Remove "${group.group_name}" from the Open Pipeline?\n\n` +
      "This only removes this one quote — it won't create a Win/Loss Database entry, since it " +
      "never reached a real decision, and can be undone by an admin later."
    )) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await apiNb.deleteQuote(group.quote_id);
      await onDeleted?.();
    } catch (err) {
      setDeleteError(err.message);
      setDeleting(false);
    }
  };

  return (
    <>
      <div className="drawer-overlay" onClick={onClose} />
      <aside className="drawer" data-outcome="band" style={{ "--outcome": color }}>
        <div className="drawer-accent" />
        <div className="drawer-wash" aria-hidden="true" />
        <button className="drawer-close" onClick={onClose}><X size={15} /></button>

        <div className="drawer-head">
          <h2>{group.group_name}</h2>
          <div className="drawer-chips">
            {chips.map((c) => <span className="chip" key={c}>{c}</span>)}
          </div>
          <button className="drawer-delete" onClick={handleDelete} disabled={deleting} title="Remove this quote from the Open Pipeline">
            <Trash2 size={12} /> {deleting ? "Removing…" : "Delete quote"}
          </button>
          {deleteError && <div className="drawer-delete-error">{deleteError}</div>}
        </div>

        <div className="drawer-hero">
          <RadialGauge
            p={group.band_win_rate} color={color}
            caption={`historical rate, ${group.likelihood_band} leads`}
          />
          <div className="hero-side">
            <LikelihoodBadge band={group.likelihood_band} />
            <div className="hero-stat">
              <span className="hero-stat-label">Based on</span>
              <span className="hero-stat-val">{group.band_wins ?? 0} of {group.band_n ?? 0} similar quotes</span>
            </div>
            <div className="hero-stat">
              <span className="hero-stat-label">Pipeline rank</span>
              <span className="hero-stat-val">#{group.pipeline_rank} of {total}</span>
            </div>
            <div className="hero-stat">
              <span className="hero-stat-label">Expected value</span>
              <span className="hero-stat-val" style={{ color: "var(--green)" }}>{fmtMoney(group.expected_value)}</span>
            </div>
            <div className="hero-stat">
              <span className="hero-stat-label">Estim. by Stage</span>
              <span className="hero-stat-val">
                {group.stage_sales_estimate == null ? "—" : fmtPct(group.stage_sales_estimate, 0)}
              </span>
            </div>
          </div>
        </div>

        <div className="drawer-note">
          <b>Why a rate, not a single number for this quote:</b> at a ~4.7% win rate there aren't
          enough historical wins for one quote's individual model score to be reliable enough to
          headline — checked directly, quotes the model scored 70%+ have historically closed about
          1 in 3 times. The rate above is this quote's BAND's real measured win rate, pooled over{" "}
          {group.band_n ?? 0} comparable quotes — a far more defensible number. The model's raw
          score for this specific quote was <b>{group.model_score == null ? "—" : fmtPct(group.model_score, 0)}</b>
          {" "}(kept here for reference only). "Estim. by Stage" above is a separate, unvalidated
          rule of thumb the exec team applies by current stage — not derived from this model or
          from measured history, so it can disagree with the band rate.
        </div>

        <div className="drawer-section-title">Why the model scored it this way</div>
        {group.drivers.length === 0 ? (
          <div className="driver-empty">Limited signal on this quote so far.</div>
        ) : (
          group.drivers.map((d, i) => (
            <div className={`driver driver-${d.direction}`} key={i}>
              <span className="driver-dot" style={{ background: d.direction === "negative" ? "var(--red)" : "var(--green)" }} />
              <span className="driver-text">{d.label}</span>
              <span className="driver-arrow" style={{ color: d.direction === "negative" ? "var(--red)" : "var(--green)" }}>
                {d.direction === "negative" ? "▼" : d.direction === "positive" ? "▲" : "•"}
              </span>
            </div>
          ))
        )}

        <div className="drawer-section-title">Every Field the Model Used</div>

        <div className="fact-sub">Profile</div>
        <div className="fact-grid">
          <Fact k="Enrolled employees" v={fmtNum(group.lives)} />
          <Fact k="Product" v={dash(group.product)} />
          <Fact k="Industry" v={dash(group.industry)} />
          <Fact k="Location" v={group.billing_city ? `${group.billing_city}, ${group.billing_state}` : dash(group.billing_state)} />
          <Fact k="Stage" v={dash(group.stage)} />
          <Fact k="Effective month (seasonality)" v={dash(group.eff_month_num)} />
        </div>

        <div className="fact-sub">Pricing</div>
        <div className="fact-grid">
          <Fact k="Current incumbent cost" v={fmtMoney(group.current_max_cost)} />
          <Fact k="Illustrative quote" v={fmtMoney(group.illustrative_max_cost)} />
          <Fact k="Firm quote" v={fmtMoney(group.firm_max_cost)} />
          <Fact k="Current renewal (incumbent)" v={fmtMoney(group.current_renewal)} />
          <Fact k="% vs. current cost" v={group.pct_vs_current == null ? "not loaded" : fmtPct(group.pct_vs_current, 0)} />
          <Fact k="Current cost / life" v={fmtMoney(group.current_cost_per_life)} />
          <Fact k="Illustrative cost / life" v={fmtMoney(group.illustrative_cost_per_life)} />
          <Fact k="Firm cost / life" v={fmtMoney(group.firm_cost_per_life)} />
        </div>

        <div className="fact-sub">Flags &amp; risk</div>
        <div className="fact-grid">
          <Fact k="Reached illustrative stage" v={yesno(group.reached_illustrative)} />
          <Fact k="Reached firm stage" v={yesno(group.reached_firm)} />
          <Fact k="ISL deductible" v={fmtMoney(group.isl_deductible)} />
          <Fact k="Laser liability" v={fmtMoney(group.laser_liability)} />
          <Fact k="Laser count" v={group.laser_count == null ? "—" : group.laser_count} />
        </div>

        <div className="fact-sub">Quote timeline</div>
        <div className="fact-grid">
          <Fact k="Days: created → effective date" v={group.days_created_to_eff == null ? "—" : group.days_created_to_eff} />
          <Fact k="Days: created → illustrative quote" v={group.days_created_to_illustrative == null ? "—" : group.days_created_to_illustrative} />
          <Fact k="Days: illustrative → firm quote" v={group.days_illustrative_to_firm == null ? "—" : group.days_illustrative_to_firm} />
          <Fact k="Days: illustrative → UW complete" v={group.days_illustrative_to_uw_complete == null ? "—" : group.days_illustrative_to_uw_complete} />
          <Fact k="Days: UW complete → firm sent" v={group.days_uw_complete_to_firm_sent == null ? "—" : group.days_uw_complete_to_firm_sent} />
        </div>

        <div className="fact-sub">Relationship &amp; team</div>
        <div className="fact-grid">
          <Fact k="Broker" v={dash(group.broker)} />
          <Fact k="RSD" v={dash(group.rsd)} />
          <Fact k="Underwriter" v={dash(group.underwriter)} />
        </div>
      </aside>
    </>
  );
}

function Fact({ k, v }) {
  const blank = v == null || ["not loaded", "n/a", "—"].includes(v);
  return (
    <div className="fact">
      <div className="k">{k}</div>
      <div className={`v${blank ? " v-blank" : ""}`}>{v}</div>
    </div>
  );
}
