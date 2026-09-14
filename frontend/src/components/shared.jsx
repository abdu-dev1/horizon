import { memo } from "react";
import { NB_BAND_COLORS, TIER_COLORS, fmtNum, fmtPct } from "../format.js";

// One chart hue plus a two-state comparison pair (above/below a reference).
// Lives here rather than in NbPerformance.jsx because Spark and RateBar below
// are now used by both that page and the RSD/broker drawer -- two copies of
// these values is exactly how a palette silently drifts.
export const CHART_INK = "#38bdf8";
export const CHART_INK_DIM = "#64748b";
export const UP = "#34d399";
export const DOWN = "#f87171";

// A styled replacement for a native `title=` tooltip -- the OS-rendered kind
// is tiny, low-contrast, ignores line breaks inconsistently across browsers,
// and only shows after a real hover delay. This is CSS-only (:hover/:focus
// opacity, no JS state), so it costs nothing on a page like Sales Performance
// with many rows each carrying one. `label` may contain "\n" for multi-line
// content (the wrapping span uses white-space: pre-line).
export function HoverTip({ label, children }) {
  return (
    <span className="hovertip" tabIndex={0}>
      {children}
      <span className="hovertip-bubble">{label}</span>
    </span>
  );
}

export function KpiCard({ icon: Icon, label, value, foot, tone }) {
  return (
    <div className="card">
      <div className="kpi-label">
        {Icon && <Icon size={13} strokeWidth={2.4} />}
        {label}
      </div>
      <div className={`kpi-value ${tone ? `kpi-${tone}` : ""}`}>{value}</div>
      {foot && <div className="kpi-foot">{foot}</div>}
    </div>
  );
}

export function RiskBadge({ tier }) {
  // Renewal-side tiers (Secure/Stable/Watch/Concern/Critical) — single words,
  // so the label doubles as the CSS class name.
  return <span className={`badge badge-${tier}`}>{tier}</span>;
}

// New Business win-likelihood band. "Very Low" needs its space stripped for
// the CSS class name (see .badge-VeryLow in styles.css) but keeps the space
// in the visible label.
export function LikelihoodBadge({ band }) {
  if (!band) return <span className="muted">—</span>;
  return <span className={`badge badge-${band.replace(/\s+/g, "")}`}>{band}</span>;
}

// The model is "high confidence" only at the extremes — near-certain to renew (>=80%)
// or near-certain to term (<=30%). The 30-80% middle is where it's genuinely unsure
// (and, per the calibration curve, mildly over-optimistic), so those get NO tag: it's
// the AM-review band. Extremes are also the best-calibrated, so the tag is honest.
export const isHighConfidence = (p) => p != null && (p >= 0.8 || p <= 0.3);

// Mirrors backend/app/insights.py's tier() thresholds exactly - single source of
// truth for both places (here and Book.jsx) that need a color from a raw
// probability, so they can't silently drift apart the way ensemble weights once did.
export const tierColor = (p) =>
  p >= 0.8 ? TIER_COLORS.Secure
  : p >= 0.68 ? TIER_COLORS.Stable
  : p >= 0.59 ? TIER_COLORS.Watch
  : p >= 0.5 ? TIER_COLORS.Concern
  : TIER_COLORS.Critical;

// Mirrors NewBusiness/backend/app/insights_nb.py's BAND_CUTS exactly — the New
// Business equivalent of tierColor above. Win probabilities cluster far lower
// than renewal probabilities (~4.7% base rate vs. ~60-70%), so this is NOT the
// same threshold set scaled down — it's the model's own band cutoffs, kept in
// sync by hand the same way tierColor already is with insights.py's tier().
export const NB_BAND_CUTS = [["High", 0.20], ["Moderate", 0.08], ["Low", 0.03], ["Very Low", 0]];
export const nbBandOf = (p) =>
  (NB_BAND_CUTS.find(([, lo]) => p >= lo) ?? NB_BAND_CUTS[NB_BAND_CUTS.length - 1])[0];
export const nbBandColor = (p) => NB_BAND_COLORS[nbBandOf(p)];

export function ProbCell({ p, showTag = true, colorFn = tierColor }) {
  const color = colorFn(p);
  // showTag=false for aggregates (e.g. segment retention rates), or whenever
  // a non-default colorFn is in play (the "high confidence" language below is
  // renewal-specific wording tied to tierColor's own thresholds) — the "high
  // confidence" tag is a per-group model signal, meaningless on a pooled rate.
  const sure = showTag && colorFn === tierColor && isHighConfidence(p);
  return (
    <div className="prob-cell">
      <span className="prob-num" style={{ color }}>{fmtPct(p, 0)}</span>
      <div className="prob-track">
        <div className="prob-fill" style={{ width: `${p * 100}%`, background: color }} />
      </div>
      {sure && (
        <span
          className="conf-tag"
          title={`High confidence — the model is near-certain this group will ${
            p >= 0.8 ? "renew" : "lapse"
          } (${fmtPct(p, 0)}). Scores in the 30–80% middle are the uncertain band and get no tag.`}
        >
          High confidence
        </span>
      )}
    </div>
  );
}

export function Legend({ items }) {
  return (
    <div className="legend-row">
      {items.map(([label, color]) => (
        <div key={label} className="legend-item">
          <span className="legend-swatch" style={{ background: color }} />
          {label}
        </div>
      ))}
    </div>
  );
}

// A win rate on its own is hard to place; the bar is drawn relative to a
// reference (the book average) so "good" and "bad" read without doing
// arithmetic. Green/red here is a two-state comparison against one reference,
// always paired with the numeric multiple, so color is never the only signal.
// Moved here from NbPerformance.jsx when the RSD/broker drawer needed the
// same comparison -- one implementation, not two that can disagree.
export function RateBar({ rate, avg }) {
  if (rate == null) return <span className="muted">—</span>;
  const ratio = avg > 0 ? rate / avg : 0;
  const width = Math.min(100, (ratio / 3) * 100);
  const above = rate >= avg;
  return (
    <HoverTip label={`${ratio.toFixed(2)}× the ${(avg * 100).toFixed(1)}% book average`}>
      <div className="ratebar">
        <div className="ratebar-track">
          <div className="ratebar-fill" style={{ width: `${width}%`, background: above ? UP : DOWN }} />
        </div>
        <span className="ratebar-num" style={{ color: above ? UP : DOWN }}>
          {ratio.toFixed(2)}×
        </span>
      </div>
    </HoverTip>
  );
}

// Small multiple: one entity's win rate by year. One hue, no axes, no legend --
// the shape is the message, and the numbers live in the table or hero beside it.
export const Spark = memo(function Spark({ points, avg, w = 88, hgt = 26 }) {
  // Only settled years form the line. An in_progress year holding a handful of
  // undecided quotes reads as a crash to zero at the right edge, which is the
  // opposite of what it means (that rate can only rise) -- it stays in the
  // hover readout instead, labelled.
  const all = (points ?? []).filter((p) => p.win_rate != null);
  const pts = all.filter((p) => !p.in_progress);
  if (pts.length < 2) return <span className="muted">—</span>;
  const pad = 3;
  const max = Math.max(avg * 2, ...pts.map((p) => p.win_rate)) || 1;
  const x = (i) => pad + (i * (w - 2 * pad)) / (pts.length - 1);
  const y = (v) => hgt - pad - (v / max) * (hgt - 2 * pad);
  const path = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.win_rate).toFixed(1)}`).join(" ");
  const last = pts[pts.length - 1];
  const title = all
    .map((p) => `${p.year}: ${(p.win_rate * 100).toFixed(1)}% (${p.wins}/${p.quotes})${p.in_progress ? " — still in progress" : ""}`)
    .join("\n");
  return (
    <HoverTip label={title}>
      <svg width={w} height={hgt} className="spark" role="img" aria-label={title}>
        <line x1={pad} x2={w - pad} y1={y(avg)} y2={y(avg)} stroke="#5b6c8c" strokeDasharray="3 3" strokeWidth="1" />
        <path d={path} fill="none" stroke={CHART_INK} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        {/* 4px radius = an 8px marker, per the mark spec, with a 2px surface
            ring so it stays legible where it overlaps the average line. The
            ring uses var(--panel) rather than a literal so it works in light
            mode. */}
        <circle cx={x(pts.length - 1)} cy={y(last.win_rate)} r="4"
                fill={last.win_rate >= avg ? UP : DOWN}
                stroke="var(--panel)" strokeWidth="2" />
      </svg>
    </HoverTip>
  );
});

// Categorical mix across the four likelihood bands, in the model's own fixed
// band order (never sorted by size -- a band's position is its meaning, and a
// re-ordering bar makes two entities impossible to compare side by side). Each
// segment gets a 2px surface gap per the mark spec, and the legend beneath
// repeats every band as text + count so color is never the only signal.
export function BandMixBar({ counts, total }) {
  const order = ["High", "Moderate", "Low", "Very Low"];
  if (!total) return <div className="driver-empty">No open quotes to break down.</div>;
  return (
    <div className="bandmix">
      <div className="bandmix-track">
        {order.map((b) => {
          const n = counts[b] || 0;
          if (!n) return null;
          return (
            <HoverTip key={b} label={`${b}: ${fmtNum(n)} of ${fmtNum(total)} open quotes (${fmtPct(n / total, 0)})`}>
              <span
                className="bandmix-seg"
                style={{ width: `${(n / total) * 100}%`, background: NB_BAND_COLORS[b] }}
              />
            </HoverTip>
          );
        })}
      </div>
      <div className="legend-row">
        {order.map((b) => (
          <div key={b} className="legend-item">
            <span className="legend-swatch" style={{ background: NB_BAND_COLORS[b] }} />
            {b} ({fmtNum(counts[b] || 0)})
          </div>
        ))}
      </div>
    </div>
  );
}

export const tooltipStyle = {
  backgroundColor: "#0b1120",
  border: "1px solid #1c2940",
  borderRadius: 10,
  fontSize: 12,
  color: "#e6edf7",
};

// Readable tooltip: labels/values in high-contrast ink, identity carried by a colored
// dot (never faded series-colored text). Dark surface reads fine over light or dark pages.
export function ChartTooltip({ active, payload, label, fmt }) {
  if (!active || !payload || !payload.length) return null;
  const dark = typeof document !== "undefined"
    && document.documentElement.dataset.theme === "dark";
  const c = dark
    ? { bg: "#0b1120", border: "#26344d", label: "#f1f5fb", name: "#c2cde0",
        value: "#ffffff", shadow: "0 10px 28px rgba(0,0,0,0.45)" }
    : { bg: "#ffffff", border: "#e2e8f0", label: "#0f172a", name: "#5b6b85",
        value: "#0f172a", shadow: "0 10px 28px rgba(2,6,23,0.14)" };
  return (
    <div style={{
      background: c.bg, border: `1px solid ${c.border}`, borderRadius: 10,
      padding: "9px 12px", fontSize: 12, boxShadow: c.shadow, minWidth: 168,
    }}>
      <div style={{ color: c.label, fontWeight: 700, marginBottom: 6 }}>{label}</div>
      {payload.map((p) => (
        <div key={p.name} style={{ display: "flex", alignItems: "center", gap: 8, lineHeight: 1.85 }}>
          <span style={{ width: 9, height: 9, borderRadius: 3, background: p.color, flexShrink: 0 }} />
          <span style={{ color: c.name, marginRight: "auto" }}>{p.name}</span>
          <span style={{ color: c.value, fontWeight: 700 }}>
            {fmt ? fmt(p.value, p.name) : p.value}
          </span>
        </div>
      ))}
    </div>
  );
}

export const axisStyle = { fill: "#5b6c8c", fontSize: 11, fontFamily: "Inter" };
