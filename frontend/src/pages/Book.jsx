import { useEffect, useMemo, useState } from "react";
import { Archive, ArrowRight, Download, Info, Lightbulb, Search, Trash2, Upload, X } from "lucide-react";
import { api } from "../api.js";
import { fmtDate, fmtMoney, fmtNum, fmtPct } from "../format.js";
import { ProbCell, RiskBadge, tierColor } from "../components/shared.jsx";
import { UploadModal } from "../components/UploadPanel.jsx";

// A group is "estimate" basis when its underwriting wasn't loaded (came from the
// renewal pipeline list: name/date/lives/team only). We blank the fields we don't
// actually know rather than show a guess as if it were real.
const isEstimate = (g) => g.data_basis === "estimate";
const fmtTenure = (y) => (y == null ? "—" : `${y}y`);
// Show a real string value, or "not loaded" for blanks/placeholders.
const txt = (v) => (v == null || v === "—" || v === "Unknown" || v === "" ? "not loaded" : v);
// Table-cell version: blanks render as a dash (compact).
const dash = (v) => (v == null || v === "—" || v === "Unknown" || v === "" ? "—" : v);
const fmtRate = (v) => (v == null ? "—" : `${v.toFixed(1)}%`);
const yesno = (v) => (v == null ? "—" : v ? "Yes" : "No");

// Loss-ratio status badge. Neutral: flags the loss-ratio risk that pulls the score
// down, NOT who ends the relationship (a hot group may leave on its own or not be
// re-offered). Colors track severity.
const LR_STATUS = {
  Severe: { color: "var(--red)", title: "150%+ loss ratio — deep underwriting loss. Strongly lowers renewal likelihood; the group may leave or the renewal may not be offered." },
  "Running hot": { color: "var(--red)", title: "100–150% loss ratio — running at a loss. Lowers renewal likelihood." },
  Elevated: { color: "var(--amber)", title: "85–100% loss ratio — approaching break-even." },
  Healthy: { color: "var(--green, var(--stable))", title: "Under 85% loss ratio — favorable experience." },
};
function LossRatioStatus({ status }) {
  if (!status) return <span className="cell-dim">—</span>;
  const s = LR_STATUS[status] || {};
  return <span className="lr-status" style={{ color: s.color }} title={s.title}>{status}</span>;
}

// Real recorded outcome for this renewal, shown next to the model's likelihood so you
// can eyeball prediction vs reality. Blank = not yet decided (a genuine open renewal).
function ActualOutcome({ outcome }) {
  if (!outcome) return <span className="cell-dim" title="Not yet decided">—</span>;
  const badge = outcome === "Renewed" ? "Renewed" : "Termed";
  return <span className={`badge badge-${badge}`}>{outcome}</span>;
}

const PAGE_SIZE = 25;

const COLUMNS = [
  { key: "group_id", label: "Group ID" },
  { key: "group_name", label: "Group" },
  { key: "renewal_probability", label: "Renewal Likelihood" },
  { key: "actual_outcome", label: "Actual Outcome" },
  { key: "risk_tier", label: "Tier" },
  { key: "renewal_date", label: "Renewal Date" },
  { key: "group_size", label: "Lives" },
  { key: "annual_premium", label: "Premium" },
  { key: "premium_at_risk", label: "Exp. Lost" },
  { key: "loss_ratio", label: "Loss Ratio" },
  { key: "loss_ratio_status", label: "LR Status" },
  { key: "agg_loss_ratio", label: "Agg Loss Ratio" },
  { key: "ratio_to_attachment", label: "Ratio to Attach" },
  { key: "corridor", label: "Corridor" },
  { key: "rate_increase_pct", label: "Renewal Inc" },
  { key: "tenure_years", label: "Tenure" },
  { key: "state", label: "State" },
  { key: "broker_name", label: "Broker" },
  { key: "am", label: "AM" },
  { key: "rsd", label: "RSD" },
  { key: "carrier", label: "Carrier" },
  { key: "tpa", label: "TPA" },
  { key: "recent_bor", label: "BOR" },
  { key: "laser_at_renewal", label: "Laser" },
];

export default function Book({ data, onDataChange }) {
  const [query, setQuery] = useState("");
  const [lob, setLob] = useState("all");
  const [tier, setTier] = useState("all");
  const [sortKey, setSortKey] = useState("renewal_probability");
  const [sortDir, setSortDir] = useState(1);
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState(null);
  const [moving, setMoving] = useState(false);
  const [moveError, setMoveError] = useState(null);
  const [showUpload, setShowUpload] = useState(false);

  const decidedCount = useMemo(
    () => data.groups.filter((g) => g.actual_outcome).length,
    [data.groups]
  );

  const moveDecided = async () => {
    setMoving(true);
    setMoveError(null);
    try {
      await api.moveDecidedToDatabase();
      await onDataChange?.();
    } catch (e) {
      setMoveError(e.message);
    } finally {
      setMoving(false);
    }
  };

  // Groups with no loss ratio loaded aren't confidently scorable — they live on their
  // own "Needs Data" page (left nav), NOT the clean Upcoming Renewals list. When their
  // underwriting is added they graduate into this list automatically.
  const rows = useMemo(() => {
    let out = data.groups.filter((g) => g.prediction_confidence !== "low");
    if (lob !== "all") out = out.filter((g) => g.line_of_business === lob);
    if (tier !== "all") out = out.filter((g) => g.risk_tier === tier);
    if (query) {
      const q = query.toLowerCase();
      out = out.filter(
        (g) =>
          (g.group_id || "").toLowerCase().includes(q) ||
          g.group_name.toLowerCase().includes(q) ||
          g.broker_name.toLowerCase().includes(q) ||
          g.rsd.toLowerCase().includes(q) ||
          g.am.toLowerCase().includes(q) ||
          g.industry.toLowerCase().includes(q)
      );
    }
    out = [...out].sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey];
      if (typeof av === "string") return av.localeCompare(bv) * sortDir;
      return (av - bv) * sortDir;
    });
    return out;
  }, [data.groups, query, lob, tier, sortKey, sortDir]);

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

  return (
    <>
      <div className="data-note">
        <Info size={15} />
        <div>
          <b>New — "LR Status" flags loss-ratio risk.</b> The model now weighs a group's
          loss ratio more sharply, so groups running hot score lower on their own line.
          The badge tells you why: <span className="lr-status" style={{ color: "var(--red)" }}>Severe</span> (150%+)
          and <span className="lr-status" style={{ color: "var(--red)" }}>Running hot</span> (100–150%)
          are deep underwriting losses; <span className="lr-status" style={{ color: "var(--amber)" }}>Elevated</span> (85–100%)
          is near break-even; <span className="lr-status">Healthy</span> is under 85%.
          <div className="data-note-how">
            A hot group's non-renewal can go <b>either way</b> — the group may leave on its
            own, or the renewal may not be offered. This flag marks the loss-ratio risk that
            pulls the score down; it does <b>not</b> claim who ends the relationship. Blank ("—")
            means the loss ratio isn't loaded yet.
          </div>
        </div>
      </div>
      <div className="card">
        <div className="card-title">Upcoming Renewals — {rows.length} data-complete renewals</div>
        <div className="card-sub">
          Data-complete renewals (loss ratio loaded), scored and ranked by renewal likelihood.
          Groups still awaiting underwriting are on the <b>Needs Data</b> page.
        </div>
        <div className="table-tools">
          <div className="search-box">
            <Search size={15} />
            <input
              placeholder="Search group ID, group, broker, RSD, AM, industry…"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setPage(0);
              }}
            />
          </div>
          <select className="filter" value={lob} onChange={(e) => { setLob(e.target.value); setPage(0); }}>
            <option value="all">All lines</option>
            <option>HPS Level Funded</option>
            <option>HPS Self Funded</option>
          </select>
          <select className="filter" value={tier} onChange={(e) => { setTier(e.target.value); setPage(0); }}>
            <option value="all">All tiers</option>
            <option>Secure</option>
            <option>Stable</option>
            <option>Watch</option>
            <option>Concern</option>
            <option>Critical</option>
          </select>
          <a className="export-btn" href={api.exportBookUrl()} title="Download all upcoming renewals as an Excel file">
            <Download size={14} /> Export to Excel
          </a>
          <button
            className="export-btn"
            onClick={() => setShowUpload(true)}
            title="Upload this month's renewal data — upcoming renewals, past outcomes, or both"
          >
            <Upload size={14} /> Upload
          </button>
          <button
            className="export-btn"
            onClick={moveDecided}
            disabled={moving || decidedCount === 0}
            title="Move every group with a confirmed outcome (Renewed/Termed) into the Renewal Database"
          >
            <Archive size={14} />
            {moving ? "Moving…" : `Move Decided to Renewal Database (${decidedCount})`}
          </button>
        </div>
        {moveError && <div className="move-to-db-error">{moveError}</div>}
        {showUpload && (
          <UploadModal onClose={() => setShowUpload(false)} onImported={onDataChange} />
        )}

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
                <tr key={g.group_id} onClick={() => setSelected(g)}>
                  <td className="mono cell-dim">{g.group_id}</td>
                  <td>
                    <div className="cell-main">{g.group_name}</div>
                    <div className="cell-dim">{g.line_of_business} · {g.industry}</div>
                  </td>
                  <td><ProbCell p={g.renewal_probability} /></td>
                  <td><ActualOutcome outcome={g.actual_outcome} /></td>
                  <td><RiskBadge tier={g.risk_tier} /></td>
                  <td className="mono">{fmtDate(g.renewal_date)}</td>
                  <td className="mono">{fmtNum(g.group_size)}</td>
                  <td className="mono">{fmtMoney(g.annual_premium)}</td>
                  <td className="mono" style={{ color: g.premium_at_risk > 200000 ? "var(--red)" : undefined }}>
                    {fmtMoney(g.premium_at_risk)}
                  </td>
                  <td className="mono" style={{ color: g.loss_ratio >= 0.85 ? "var(--amber)" : undefined }}>
                    {fmtPct(g.loss_ratio, 0)}
                  </td>
                  <td><LossRatioStatus status={g.loss_ratio_status} /></td>
                  <td className="mono">{fmtPct(g.agg_loss_ratio, 0)}</td>
                  <td className="mono">{fmtPct(g.ratio_to_attachment, 0)}</td>
                  <td className="mono">{g.corridor == null ? "—" : `${Math.round(g.corridor * 100)}%`}</td>
                  <td className="mono">{fmtRate(g.rate_increase_pct)}</td>
                  <td className="mono">{fmtTenure(g.tenure_years)}</td>
                  <td className="mono">{dash(g.state)}</td>
                  <td className="cell-dim">{dash(g.broker_name)}</td>
                  <td className="cell-dim">{dash(g.am)}</td>
                  <td className="cell-dim">{dash(g.rsd)}</td>
                  <td className="cell-dim">{dash(g.carrier)}</td>
                  <td className="cell-dim">{dash(g.tpa)}</td>
                  <td className="mono">{yesno(g.recent_bor)}</td>
                  <td className="mono">{yesno(g.laser_at_renewal)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="pager">
          <span>
            {fmtNum(rows.length)} groups · page {page + 1} of {pageCount}
          </span>
          <div className="pager-btns">
            <button disabled={page === 0} onClick={() => setPage(0)}>« First</button>
            <button disabled={page === 0} onClick={() => setPage(page - 1)}>‹ Prev</button>
            <button disabled={page >= pageCount - 1} onClick={() => setPage(page + 1)}>Next ›</button>
            <button disabled={page >= pageCount - 1} onClick={() => setPage(pageCount - 1)}>Last »</button>
          </div>
        </div>
      </div>

      {selected && (
        <GroupDrawer
          group={selected}
          onClose={() => setSelected(null)}
          onDeleted={async () => { setSelected(null); await onDataChange?.(); }}
        />
      )}
    </>
  );
}

function RadialGauge({ p, color }) {
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
          strokeDashoffset={c * (1 - p)}
          transform="rotate(-90 65 65)"
          style={{ transition: "stroke-dashoffset 0.6s cubic-bezier(.2,.7,.2,1), stroke 0.3s" }}
        />
      </svg>
      <div className="gauge-center">
        <div className="gauge-pct" style={{ color }}>{fmtPct(p, 0)}</div>
        <div className="gauge-cap">likely to renew</div>
      </div>
    </div>
  );
}

export function GroupDrawer({ group, onClose, onDeleted }) {
  const p = group.renewal_probability;
  const color = tierColor(p);  // keep the drawer gauge on the same tier ramp as the table
  const estimate = isEstimate(group);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);

  const chips = [
    group.line_of_business,
    group.state && group.state !== "—" ? group.state : null,
    group.tenure_years == null ? null : `${group.tenure_years} yr${group.tenure_years > 1 ? "s" : ""} tenure`,
  ].filter(Boolean);

  const handleDelete = async () => {
    if (!window.confirm(
      `Remove "${group.group_name}" (${fmtDate(group.renewal_date)}) from Upcoming Renewals / Needs Data?\n\n` +
      "This only removes this one renewal from the current book - it won't touch any past decided " +
      "renewals for this company in the Renewal Database, and can be undone by an admin later."
    )) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await api.deleteGroup(group.group_id);
      await onDeleted?.();
    } catch (err) {
      setDeleteError(err.message);
      setDeleting(false);
    }
  };

  return (
    <>
      <div className="drawer-overlay" onClick={onClose} />
      <aside className="drawer" data-outcome="tier" style={{ "--outcome": color }}>
        <div className="drawer-accent" />
        <div className="drawer-wash" aria-hidden="true" />
        <button className="drawer-close" onClick={onClose}><X size={15} /></button>

        <div className="drawer-head">
          <h2>{group.group_name}</h2>
          <div className="drawer-chips">
            {chips.map((c) => <span className="chip" key={c}>{c}</span>)}
            {estimate && <span className="chip chip-est">needs data</span>}
          </div>
          <button className="drawer-delete" onClick={handleDelete} disabled={deleting} title="Remove this renewal from Upcoming Renewals / Needs Data">
            <Trash2 size={12} /> {deleting ? "Removing…" : "Delete group"}
          </button>
          {deleteError && <div className="drawer-delete-error">{deleteError}</div>}
        </div>

        {estimate && (
          <div className="est-banner">
            <b>Awaiting renewal underwriting.</b> Everything we have from the other files is real —
            premium, lives, tenure, state, broker, carrier and team are filled below. What's not loaded
            yet is the <b>loss ratio</b> (this renewal hasn't been quoted) — that's the one field that
            gates a confident score; it shows <b>blank ("—"), never a guess</b>. Renewal increase and
            everything else sharpen the score further but aren't required. The score uses the real
            facts we do have.
            <div className="est-banner-how">
              <b>To sharpen it:</b> once worked, load this group's block from the <b>Executive Log / UW
              renewal sheet</b> (loss ratio, quoted rate increase, lasers), then re-import. Full field
              list: <b>Model Maintenance → Feed 1</b>.
            </div>
          </div>
        )}

        <div className="drawer-hero">
          <RadialGauge p={p} color={color} />
          <div className="hero-side">
            <RiskBadge tier={group.risk_tier} />
            <div className="hero-stat">
              <span className="hero-stat-label">Annual premium</span>
              <span className="hero-stat-val">{fmtMoney(group.annual_premium)}</span>
            </div>
            <div className="hero-stat">
              <span className="hero-stat-label">Expected lost premium</span>
              <span className="hero-stat-val" style={{ color: "var(--red)" }}>{fmtMoney(group.premium_at_risk)}</span>
            </div>
            <div className="hero-stat">
              <span className="hero-stat-label">Renews</span>
              <span className="hero-stat-val">{fmtDate(group.renewal_date)}</span>
            </div>
          </div>
        </div>

        <div className="drawer-section-title">Why the model scored it this way</div>
        {group.drivers.length === 0 ? (
          <div className="driver-empty">
            Limited signal — underwriting isn't loaded for this group, so the score leans on size and
            relationship only. Load its UW block to surface real drivers.
          </div>
        ) : (
          group.drivers.map((d, i) => (
            <div className={`driver driver-${d.direction}`} key={i}>
              <span className="driver-dot" style={{ background: d.direction === "negative" ? "var(--red)" : "var(--green)" }} />
              <span className="driver-text">{d.label}</span>
              <span className="driver-arrow" style={{ color: d.direction === "negative" ? "var(--red)" : "var(--green)" }}>
                {d.direction === "negative" ? "▼" : "▲"}
              </span>
            </div>
          ))
        )}

        <Recommendation group={group} />

        <div className="drawer-section-title">Account Facts</div>

        <div className="fact-sub">Profile</div>
        <div className="fact-grid">
          <Fact k="Enrolled employees" v={fmtNum(group.group_size)} />
          <Fact k="Annual premium" v={fmtMoney(group.annual_premium)} />
          <Fact
            k="Tenure"
            v={group.tenure_years == null ? "not loaded" : `${group.tenure_years} yr${group.tenure_years > 1 ? "s" : ""}`}
          />
          <Fact k="State" v={txt(group.state)} />
          <Fact k="Product line" v={txt(group.line_of_business)} />
        </div>

        <div className="fact-sub">
          Underwriting
          {estimate && <span className="fact-sub-note"> — loads from the UW renewal sheet</span>}
        </div>
        <div className="fact-grid">
          <Fact k="Loss ratio" v={group.loss_ratio == null ? "not loaded" : fmtPct(group.loss_ratio, 0)} />
          <Fact
            k="Loss ratio status"
            v={group.loss_ratio_status == null ? "not loaded" : <LossRatioStatus status={group.loss_ratio_status} />}
          />
          <Fact k="Aggregate loss ratio" v={group.agg_loss_ratio == null ? "not loaded" : fmtPct(group.agg_loss_ratio, 0)} />
          <Fact k="Ratio to attachment" v={group.ratio_to_attachment == null ? "not loaded" : fmtPct(group.ratio_to_attachment, 0)} />
          <Fact
            k="Renewal increase"
            v={group.rate_increase_pct == null ? "not loaded" : `${group.rate_increase_pct.toFixed(1)}%`}
          />
          <Fact k="Laser at renewal" v={group.laser_at_renewal ? "Yes" : "No"} />
          <Fact k="Corridor" v={group.corridor == null ? "not loaded" : `${Math.round(group.corridor * 100)}%`} />
          <Fact k="Initial UW RTM" v={group.uw_rtm_initial == null ? "n/a" : group.uw_rtm_initial.toFixed(2)} />
        </div>

        <div className="fact-sub">Relationship &amp; team</div>
        <div className="fact-grid">
          <Fact k="Broker" v={txt(group.broker_name)} />
          <Fact k="Broker quality" v={group.broker_quality == null ? "n/a" : `${group.broker_quality} / 5`} />
          <Fact k="Recent BOR" v={group.recent_bor == null ? "not loaded" : group.recent_bor ? "Yes" : "No"} />
          <Fact k="Carrier" v={txt(group.carrier)} />
          <Fact k="TPA" v={txt(group.tpa)} />
          <Fact k="RSD" v={txt(group.rsd)} />
          <Fact k="Account manager" v={txt(group.am)} />
        </div>
      </aside>
    </>
  );
}

function Recommendation({ group }) {
  const [rec, setRec] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setRec(null);
    api
      .recommendation(group.group_id)
      .then((r) => alive && setRec(r))
      .catch(() => alive && setRec(null))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, [group.group_id]);

  return (
    <>
      <div className="drawer-section-title" style={{ display: "flex", alignItems: "center", gap: 7 }}>
        <Lightbulb size={12} /> Recommended Action by ML Model
      </div>
      {loading && <div className="rec-card rec-muted">Analyzing levers…</div>}
      {!loading && rec?.best && (
        <div className="rec-card rec-best">
          <div className="rec-headline">{rec.best.label}</div>
          <div className="rec-impact">
            <span className="rec-move">
              {fmtPct(rec.baseline_probability, 0)}
              <ArrowRight size={13} style={{ verticalAlign: "middle", margin: "0 4px", color: "var(--text-faint)" }} />
              <b style={{ color: "var(--green)" }}>{fmtPct(rec.best.new_probability, 0)}</b>
            </span>
            <span className="rec-saved">~{fmtMoney(rec.best.premium_saved)} premium saved</span>
          </div>
        </div>
      )}
      {!loading && rec && !rec.best && rec.status === "structural" && (
        <div className="rec-card rec-muted">
          No pricing lever materially moves this group — risk is structural (tenure, broker, or
          experience). Focus on relationship and service, not rate.
        </div>
      )}
    </>
  );
}

function Fact({ k, v }) {
  const blank = v == null || ["not loaded", "Pending UW", "n/a", "—", "Unknown"].includes(v);
  return (
    <div className="fact">
      <div className="k">{k}</div>
      <div className={`v${blank ? " v-blank" : ""}`}>{v}</div>
    </div>
  );
}
