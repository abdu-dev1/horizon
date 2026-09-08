export const fmtMoney = (v) => {
  if (v == null) return "—";
  const abs = Math.abs(v);
  if (abs >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${Math.round(v)}`;
};

export const fmtPct = (v, digits = 1) =>
  v == null ? "—" : `${(v * 100).toFixed(digits)}%`;

export const fmtNum = (v) => (v == null ? "—" : v.toLocaleString("en-US"));

export const fmtMonth = (ym) => {
  const [y, m] = ym.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("en-US", { month: "short", year: "2-digit" });
};

// Defensive against anything past a plain "YYYY-MM-DD" string -- e.g. a
// pandas Timestamp serialized as "YYYY-MM-DD HH:MM:SS", which naively
// appending "T00:00:00" turns into an unparseable
// "YYYY-MM-DD HH:MM:SST00:00:00" and renders as "Invalid Date" (caught
// 2026-08-26 on the Win/Loss Database's Effective Date / Quote Created
// columns, traced to a backend dtype bug -- see NewBusiness/backend/
// etl_nb.py's _preserve_local_decisions). Takes just the leading
// "YYYY-MM-DD" via regex regardless of what follows, so this stays correct
// even if the backend regresses again.
export const fmtDate = (iso) => {
  if (!iso) return "—";
  const m = String(iso).match(/^\d{4}-\d{2}-\d{2}/);
  if (!m) return "—";
  const d = new Date(`${m[0]}T00:00:00`);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
};

// Ordinal renewal-likelihood tiers, best -> worst. A monotonic good->bad ramp
// (teal -> lime -> amber -> rose) so rank reads off hue alone; the old green->blue->
// amber->red detour put a cool "info" blue in the middle of a risk scale. Validated
// (dataviz skill): normal-vision adjacent ΔE >= 15 and >= 3:1 contrast in both modes;
// the tighter CVD pair is carried by secondary encoding (the % value, bar length, and
// the tier text label all repeat the signal).
export const TIER_COLORS = {
  Secure: "#2dd4bf",
  Stable: "#a3e635",
  Watch: "#f59e0b",
  Concern: "#ea580c",
  Critical: "#e11d48",
};

// New Business win-likelihood bands run the SAME direction as the renewal
// tiers above (high probability = good news in both), so this reuses 4 of the
// 5 already-validated TIER_COLORS steps rather than inventing a second
// palette: Secure/Stable/Watch/Critical -> High/Moderate/Low/Very Low,
// dropping only "Concern" (the one step whose removal doesn't touch either
// endpoint). Both endpoints stay put on purpose — High should read as the same
// "best case" teal as Secure, Very Low the same "worst case" rose as Critical.
// Re-validated as its own 4-step set (dataviz skill): normal-vision floor
// passes (17.8, well above the 15 hard gate); the one CVD WARN (amber/lime,
// ΔE 7.4, the same warn band the original 5-step set already lived in) is
// mitigated the way it is everywhere else in this app — LikelihoodBadge always
// pairs the color with the band's text label, ProbCell always pairs it with
// the numeric %, so color is never the only signal.
export const NB_BAND_COLORS = {
  High: TIER_COLORS.Secure,
  Moderate: TIER_COLORS.Stable,
  Low: TIER_COLORS.Watch,
  "Very Low": TIER_COLORS.Critical,
};
