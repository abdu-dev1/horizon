import { fmtDate, fmtMoney, fmtNum, fmtPct } from "../../format.js";

// Single source of truth for every New Business per-quote table (Open
// Pipeline + Win/Loss Database) -- same columns, same order, on both pages,
// per user request 2026-08-25.
//
// `pipelineOnly: true` marks a column that is a genuine OPEN-PIPELINE-only
// concept -- the model only scores still-open quotes (see build_book.py),
// so expected_value/band_win_rate/likelihood_band/stage_sales_estimate/
// pipeline_rank are never computed for a decided quote at all, not just
// "not yet filled in". Showing them on the Win/Loss Database would be a
// column of permanent, meaningless dashes (per follow-up user correction,
// same session) -- NbDatabase.jsx filters these out entirely rather than
// rendering blanks for a concept that doesn't apply there. NbPipeline.jsx
// renders every column, pipelineOnly or not.
//
// "(not a feature)" in a label marks a column the model does NOT train on.
// Two different reasons, both real: the quote-progression fields
// (illustrative/firm cost, ISL, laser, UW turnaround days, underwriter) were
// dropped in the 2026-08-26 serve-consistency rework because they are ~0%
// populated on an open quote (see train_nb.py's docstring); loss_reason /
// notice_of_sale_date are leakage, knowable only after the decision. Both
// groups are still displayed -- these tables exist to show every field on a
// row -- but the label has to say so, or a populated cell reads as something
// that moved the score. backend/app/nb_mode.py's MODEL_FIELDS /
// CONTEXT_FIELDS split is the authority for which is which.
//
// `special: true` marks a column each page renders with its own bespoke
// cell (badge, gauge, editable dropdown, etc.) -- see NbPipeline.jsx /
// NbDatabase.jsx's per-key `if` branches. Every column still carries a
// plain `type` too, so a page that has NO bespoke branch for a given key
// falls through to the shared generic renderer below instead of rendering
// nothing.
export const NB_COLUMNS = [
  { key: "quote_id", label: "Quote ID", type: "text", special: true },
  { key: "group_name", label: "Group", type: "text", special: true },
  { key: "won", label: "Outcome", type: "outcome", special: true },
  { key: "expected_value", label: "Expected Value", type: "money", pipelineOnly: true },
  { key: "band_win_rate", label: "Historical Win Rate", type: "pct", special: true, pipelineOnly: true },
  { key: "likelihood_band", label: "Likelihood", type: "text", special: true, pipelineOnly: true },
  // Exec team's own rule-of-thumb close rate by CURRENT stage -- a separate,
  // unvalidated business estimate, deliberately not merged into the model's
  // measured Historical Win Rate column above so the two can't be confused.
  { key: "stage_sales_estimate", label: "Estim. by Stage", type: "pct", pipelineOnly: true },
  { key: "pipeline_rank", label: "Pipeline Rank", type: "text", special: true, pipelineOnly: true },
  // Editable on Open Pipeline (click -> dropdown, see NbPipeline.jsx); plain
  // text on the Win/Loss Database (already decided, nothing left to edit).
  { key: "stage", label: "Stage", type: "text", special: true },
  { key: "eff_date", label: "Effective Date", type: "date" },
  { key: "created_date", label: "Quote Created", type: "date" },
  { key: "rsd", label: "RSD", type: "text" },
  { key: "broker", label: "Broker", type: "text" },
  { key: "underwriter", label: "Underwriter (not a feature)", type: "text" },
  { key: "industry", label: "Industry", type: "text" },
  { key: "billing_state", label: "State", type: "text" },
  { key: "billing_city", label: "City", type: "text" },
  { key: "product", label: "Product", type: "text" },
  { key: "eff_month_num", label: "Eff. Month", type: "text" },
  { key: "lives", label: "Lives", type: "num" },
  { key: "current_max_cost", label: "Current Cost", type: "money" },
  { key: "illustrative_max_cost", label: "Illustrative Quote (not a feature)", type: "money" },
  { key: "firm_max_cost", label: "Firm Quote (not a feature)", type: "money" },
  { key: "current_renewal", label: "Current Renewal", type: "money" },
  { key: "pct_vs_current", label: "vs. Current Cost (not a feature)", type: "pct" },
  // New Logic: comparison rule (Firm beats Illustrative; Written beats both if
  // cheaper; cheapest across markets wins), applied against Current Cost /
  // Current Renewal -- backed by the External Market Pricing_All Time
  // workbook (see etl_nb.py's _load_market_pricing). Kept as its own labeled
  // columns rather than folded into pct_vs_current above, so this logic is
  // never mistaken for the Salesforce-parity number it isn't.
  { key: "pct_vs_current_full", label: "New Logic: vs. Current Cost (not a feature)", type: "pct" },
  { key: "pct_vs_renewal", label: "New Logic: vs. Current Renewal (not a feature)", type: "pct" },
  { key: "current_cost_per_life", label: "Current $/Life", type: "money" },
  { key: "illustrative_cost_per_life", label: "Illustrative $/Life (not a feature)", type: "money" },
  { key: "firm_cost_per_life", label: "Firm $/Life (not a feature)", type: "money" },
  // NOTE: real field names are has_illustrative_quote / has_firm_quote (see
  // backend/app/nb_mode.py's BOOL_FIELDS) -- both tables previously keyed
  // these as "reached_illustrative"/"reached_firm", which never matched any
  // real field and rendered blank on every row. Fixed here.
  { key: "has_illustrative_quote", label: "Reached Illustrative (not a feature)", type: "bool" },
  { key: "has_firm_quote", label: "Reached Firm (not a feature)", type: "bool" },
  { key: "isl_deductible", label: "ISL Deductible (not a feature)", type: "money" },
  { key: "laser_liability", label: "Laser Liability (not a feature)", type: "money" },
  { key: "laser_count", label: "Laser Count (not a feature)", type: "num" },
  { key: "days_created_to_eff", label: "Days: Created→Eff", type: "num" },
  { key: "days_created_to_illustrative", label: "Days: Created→Illus. (not a feature)", type: "num" },
  { key: "days_illustrative_to_firm", label: "Days: Illus.→Firm (not a feature)", type: "num" },
  { key: "days_illustrative_to_uw_complete", label: "Days: Illus.→UW Done (not a feature)", type: "num" },
  { key: "days_uw_complete_to_firm_sent", label: "Days: UW Done→Firm Sent (not a feature)", type: "num" },
  // NOT model features -- leakage, only knowable after the decision (see
  // etl_nb.py) -- always blank on the still-open Pipeline side, shown here
  // purely as historical context on the Database side.
  { key: "loss_reason", label: "Loss Reason (not a feature)", type: "text" },
  { key: "loss_reason_other", label: "Loss Reason - Other (not a feature)", type: "text" },
  { key: "notice_of_sale_date", label: "Notice of Sale Date (not a feature)", type: "date" },
];

export const dash = (v) => (v == null || v === "" ? "—" : v);
export const yesno = (v) => (v == null ? "—" : v ? "Yes" : "No");

// Generic fallback cell renderer -- used for every column a page doesn't
// special-case with its own bespoke `if (c.key === ...)` branch.
export function renderNbCell(row, col) {
  const v = row[col.key];
  switch (col.type) {
    case "money": return <span className="mono">{fmtMoney(v)}</span>;
    case "pct": return <span className="mono">{v == null ? "—" : fmtPct(v, 0)}</span>;
    case "num": return <span className="mono">{v == null ? "—" : fmtNum(v)}</span>;
    case "bool": return <span className="mono">{yesno(v)}</span>;
    case "date": return <span className="mono">{v == null ? "—" : fmtDate(v)}</span>;
    case "outcome": return v == null ? <span className="cell-dim">—</span> : (
      <span className={`badge badge-${v ? "Renewed" : "Termed"}`}>{v ? "Won" : "Lost"}</span>
    );
    default: return <span className="cell-dim">{dash(v)}</span>;
  }
}
