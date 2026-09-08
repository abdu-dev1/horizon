import { useEffect, useState } from "react";
import {
  Database,
  GitBranch,
  Layers,
  ShieldCheck,
  Sparkles,
  TrendingDown,
} from "lucide-react";
import { api } from "../api.js";
import { fmtDate, fmtNum, fmtPct } from "../format.js";
import { KpiCard } from "../components/shared.jsx";

const barColor = (v) =>
  v >= 0.8 ? "var(--green)" : v >= 0.6 ? "var(--accent)" : v >= 0.4 ? "var(--amber)" : "var(--red)";

export default function DataQuality() {
  const [q, setQ] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    api.dataQuality().then(setQ).catch((e) => setErr(e.message));
  }, []);

  if (err)
    return <div className="card" style={{ color: "var(--red)" }}>Couldn't load the data-quality report — {err}</div>;
  if (!q) return <div className="card">Loading the data-quality report…</div>;
  if (!q.available)
    return (
      <div className="card">
        No real training data is loaded yet. This page reports on the real renewal history — it'll
        populate once <span className="mono">real_history.csv</span> is built.
      </div>
    );

  const span = `${fmtDate(q.date_start)} – ${fmtDate(q.date_end)}`;
  const years = q.by_year;
  const latest = years[years.length - 1];
  const prior = years.length > 1 ? years[years.length - 2] : null;
  const maxYearN = Math.max(...years.map((y) => y.n));

  return (
    <>
      <div className="card guide-hero">
        <div className="guide-hero-icon"><ShieldCheck size={22} strokeWidth={2.2} /></div>
        <div>
          <div className="card-title" style={{ fontSize: 15 }}>
            The health of the data the model learns from
          </div>
          <div className="card-sub" style={{ marginBottom: 0 }}>
            Measured live from the unified renewal history (<span className="mono">real_history.csv</span>),
            and refreshed on every Monthly Retrain. Below: how complete the data is, where it comes
            from, and how the renewal rate has moved year over year.
          </div>
        </div>
      </div>

      <div className="grid grid-kpi">
        <KpiCard icon={Database} label="Renewal decisions" value={fmtNum(q.n_decisions)} foot={span} />
        <KpiCard
          icon={Sparkles}
          label="Rich underwriting rows"
          value={`${fmtNum(q.n_rich)} · ${fmtPct(q.rich_share, 0)}`}
          tone="accent"
          foot="loss ratio present → sharpest scores"
        />
        <KpiCard
          icon={Layers}
          label="Overall field completeness"
          value={fmtPct(q.overall_complete, 0)}
          tone="amber"
          foot={`across ${q.fields_tracked} fields tracked per renewal`}
        />
        <KpiCard
          icon={TrendingDown}
          label={`${latest.year} renewal rate`}
          value={fmtPct(latest.renewal_rate, 0)}
          tone="red"
          foot={prior ? `vs ${fmtPct(prior.renewal_rate, 0)} in ${prior.year} — regime shift` : "current cohort"}
        />
      </div>

      <div className="grid grid-3-2">
        <div className="card">
          <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Layers size={15} /> Field completeness by category
          </div>
          <div className="card-sub">
            Share of the {fmtNum(q.n_decisions)} decisions that carry each field. Core identity and
            outcome are near-complete; underwriting and negotiation detail thin out the further back
            and the smaller the block. An <span className="dq-est">est.</span> tag marks fields that
            are filled but partly <b>estimated</b> (derived/inferred), not confirmed values.
          </div>
          {q.completeness.map((g) => (
            <div key={g.group} className="dq-group">
              <div className="dq-group-head">
                <span>{g.group}</span>
                <span className="mono" style={{ color: barColor(g.avg) }}>{fmtPct(g.avg, 0)}</span>
              </div>
              {g.fields.map((f) => (
                <div key={f.field} className="dq-bar-row">
                  <div className="dq-bar-label">
                    <span className="dq-bar-name">{f.field}</span>
                    {f.derived ? (
                      <span className="dq-est" title={`${fmtPct(f.derived, 0)} of these are estimated/derived, not confirmed`}>
                        {fmtPct(f.derived, 0)} est.
                      </span>
                    ) : null}
                  </div>
                  <div className="prob-track">
                    <div className="prob-fill" style={{ width: `${f.complete * 100}%`, background: barColor(f.complete) }} />
                  </div>
                  <div className="dq-bar-pct mono">{fmtPct(f.complete, 0)}</div>
                </div>
              ))}
            </div>
          ))}
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="card">
            <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <GitBranch size={15} /> Where the data comes from
            </div>
            <div className="card-sub">
              Three primary sources, unified on a normalised group key and enriched from the
              executive logs.
            </div>
            {q.by_source.map((s) => (
              <div key={s.source} className="dq-bar-row">
                <div className="dq-bar-label" style={{ width: 170 }}>{s.label}</div>
                <div className="prob-track">
                  <div className="prob-fill" style={{ width: `${(s.n / q.n_decisions) * 100}%`, background: "var(--accent)" }} />
                </div>
                <div className="dq-bar-pct mono">{fmtNum(s.n)}</div>
              </div>
            ))}
          </div>

          <div className="card">
            <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <TrendingDown size={15} /> Renewal rate over time
            </div>
            <div className="card-sub">Each cohort by renewal year — the 2026 drop is the signal to watch.</div>
            {years.map((y) => {
              const isLast = y.year === latest.year;
              return (
                <div key={y.year} className="dq-bar-row">
                  <div className="dq-bar-label" style={{ width: 96 }}>
                    {y.year} <span style={{ color: "var(--text-faint)" }}>· {fmtNum(y.n)}</span>
                  </div>
                  <div className="prob-track">
                    <div
                      className="prob-fill"
                      style={{ width: `${y.renewal_rate * 100}%`, background: isLast ? "var(--red)" : "var(--green)" }}
                    />
                  </div>
                  <div className="dq-bar-pct mono" style={isLast ? { color: "var(--red)" } : {}}>
                    {fmtPct(y.renewal_rate, 0)}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </>
  );
}
