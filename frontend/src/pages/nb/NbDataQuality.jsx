import { useEffect, useState } from "react";
import { Database, Inbox, Layers, ShieldCheck } from "lucide-react";
import { apiNb } from "../../apiNb.js";
import { fmtNum, fmtPct } from "../../format.js";
import { KpiCard } from "../../components/shared.jsx";

const barColor = (v) =>
  v >= 0.8 ? "var(--green)" : v >= 0.6 ? "var(--accent)" : v >= 0.4 ? "var(--amber)" : "var(--red)";

function CompletenessPanel({ title, sub, groups }) {
  return (
    <div className="card">
      <div className="card-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Layers size={15} /> {title}
      </div>
      <div className="card-sub">
        {sub} <span className="dq-feature-dot" /> marks a field the model actually trains on.
      </div>
      {groups.map((g) => (
        <div key={g.group} className="dq-group">
          <div className="dq-group-head">
            <span>{g.group}</span>
            <span className="mono" style={{ color: barColor(g.avg) }}>{fmtPct(g.avg, 0)}</span>
          </div>
          {g.fields.map((f) => (
            <div key={f.field} className="dq-bar-row">
              <div className="dq-bar-label">
                <span className="dq-bar-name">{f.field}</span>
                {/* A field at 67% on history and 0% here is a broken feature,
                    not just a data gap — that mismatch is what made the old
                    model report 0.94 while running at 0.74 in production. The
                    dot marks the ones the model actually trains on, so the two
                    facts can be read off one row. */}
                {f.is_feature && <span className="dq-feature-dot" title="Model feature" />}
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
  );
}

export default function NbDataQuality() {
  const [q, setQ] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    apiNb.dataQuality().then(setQ).catch((e) => setErr(e.message));
  }, []);

  if (err)
    return <div className="card" style={{ color: "var(--red)" }}>Couldn't load the data-quality report — {err}</div>;
  if (!q) return <div className="card">Loading the data-quality report…</div>;
  if (!q.available)
    return (
      <div className="card">
        No New Business data is loaded yet. This page reports on{" "}
        <span className="mono">nb_history.csv</span> / <span className="mono">nb_pipeline.csv</span>.
      </div>
    );

  return (
    <>
      <div className="card guide-hero">
        <div className="guide-hero-icon"><ShieldCheck size={22} strokeWidth={2.2} /></div>
        <div>
          <div className="card-title" style={{ fontSize: 15 }}>
            The health of the data the model learns from — and scores with
          </div>
          <div className="card-sub" style={{ marginBottom: 0 }}>
            Measured live from <span className="mono">nb_history.csv</span> (decided quotes) and{" "}
            <span className="mono">nb_pipeline.csv</span> (still-open quotes). Split deliberately in
            two, because that split IS the story: pricing fields are reasonably populated once a
            quote is decided, but next to nothing for quotes still open — verified directly against
            the raw source file, not assumed.
          </div>
        </div>
      </div>

      <div className="grid grid-kpi">
        <KpiCard icon={Database} label="Decided quotes" value={fmtNum(q.n_history)} foot={`win rate ${fmtPct(q.history_win_rate, 1)}`} />
        <KpiCard icon={Inbox} label="Open quotes" value={fmtNum(q.n_pipeline)} tone="accent" foot="still awaiting a decision" />
        <KpiCard
          icon={Layers}
          label="Pipeline pricing completeness"
          value={fmtPct(q.pipeline_completeness.find((g) => g.group === "Pricing")?.avg, 0)}
          tone="red"
          foot="vs. history's richer pricing coverage below"
        />
        <KpiCard
          icon={ShieldCheck}
          label="Source"
          value="1 file"
          foot={q.hygiene.source_file}
        />
      </div>

      <div className="grid grid-2">
        <CompletenessPanel
          title="Open pipeline — field completeness"
          sub={`Share of the ${fmtNum(q.n_pipeline)} still-open quotes that carry each field. Most pricing fields are near-empty because most open quotes haven't reached a pricing stage yet — this is a real limit of the source data, not a pipeline bug.`}
          groups={q.pipeline_completeness}
        />
        <CompletenessPanel
          title="Decided history — field completeness"
          sub={`Share of the ${fmtNum(q.n_history)} decided (Closed Won/Lost) quotes that carry each field — the model's actual training set.`}
          groups={q.history_completeness}
        />
      </div>

      <div className="card">
        <div className="card-title">Pricing coverage by open-pipeline stage</div>
        <div className="card-sub">
          The concrete evidence: even quotes already at "Firm Quote Released" mostly lack a firm
          cost number in the source system — Salesforce doesn't reliably fill that field in until a
          deal actually closes, not just when it reaches that stage.
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Stage</th>
                <th>Open Quotes</th>
                <th>Has Illustrative Quote</th>
                <th>Has Firm Quote</th>
              </tr>
            </thead>
            <tbody>
              {q.pipeline_by_stage.map((s) => (
                <tr key={s.stage} style={{ cursor: "default" }}>
                  <td className="cell-main">{s.stage}</td>
                  <td className="mono">{fmtNum(s.n)}</td>
                  <td className="mono" style={{ color: s.has_illustrative ? "var(--green)" : "var(--text-faint)" }}>
                    {s.has_illustrative} / {s.n}
                  </td>
                  <td className="mono" style={{ color: s.has_firm ? "var(--green)" : "var(--text-faint)" }}>
                    {s.has_firm} / {s.n}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="dq-footnote">
          {q.hygiene.excluded_sandbox_rows} known sandbox/test row excluded from both tables at the
          ETL stage (not a real prospect). Report generated {new Date(q.generated_at).toLocaleString()}.
        </div>
      </div>
    </>
  );
}
