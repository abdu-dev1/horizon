"""
Training-data quality report for the New Business project — measured live
from nb_history.csv (decided quotes) and nb_pipeline.csv (still-open quotes).

Powers the Data Quality page. Every number here is MEASURED, not modeled —
same principle as the renewal project's data_quality.py, but this report
deliberately splits completeness by DECIDED vs. OPEN, because that split is
the real story here: pricing fields are ~50-60% populated once a quote is
decided, but next to nothing for still-open quotes (verified directly against
the raw RSD Scorecard Export on 2026-08-18 — most open quotes haven't reached
a stage where Salesforce ever gets that field filled in, e.g. 0% of quotes
even at "Firm Quote Released" have a firm cost in 12 of 13 cases checked).
"""
from __future__ import annotations

from datetime import datetime, timezone
import numpy as np
import pandas as pd

from . import paths

BACKEND_DIR = paths.backend_dir()
HISTORY_PATH = BACKEND_DIR / "data" / "nb_history.csv"
PIPELINE_PATH = BACKEND_DIR / "data" / "nb_pipeline.csv"

# Friendly field names + the group each belongs to, for the completeness
# panel. Kept in lockstep with frontend/src/pages/nb/nbColumns.jsx's NB_COLUMNS
# -- same full field set as the Open Pipeline / Win/Loss Database tables, per
# user request 2026-08-25, so this report never silently audits a smaller
# slice of the data than what those two pages actually show.
FIELD_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Identity & context", [
        ("group_name", "Group name"),
        ("stage", "Stage"),
        ("eff_date", "Effective date"),
        ("created_date", "Quote created date"),
        # eff_month_num is NOT a raw column -- it's derived from eff_date at
        # load time (see train_nb.py's _prep), so this report (reading the
        # raw CSVs directly) has nothing independent to measure for it; its
        # completeness is definitionally identical to eff_date's above.
        ("product", "Product"),
        ("billing_city", "Billing city"),
        ("billing_state", "Billing state"),
    ]),
    ("Relationship & team", [
        ("rsd", "RSD"),
        ("broker", "Broker"),
        ("underwriter", "Underwriter"),
        ("industry", "Industry"),
    ]),
    # firm_w_laser, range_vs_current, renewal_range are excluded here -- this
    # export never carries those Salesforce report-layer columns at all (0%
    # on both history and pipeline), so etl_nb.py drops them from the data
    # model entirely rather than reporting dead columns.
    ("Pricing", [
        ("current_max_cost", "Current (incumbent) cost"),
        ("illustrative_max_cost", "Illustrative quote"),
        ("firm_max_cost", "Firm quote"),
        ("current_renewal", "Current renewal (incumbent)"),
        ("pct_vs_current", "% vs. current cost"),
        ("current_cost_per_life", "Current $/life"),
        ("illustrative_cost_per_life", "Illustrative $/life"),
        ("firm_cost_per_life", "Firm $/life"),
        # New Logic's comparison rule (etl_nb.py's _load_market_pricing) -- real
        # but partial coverage, sourced from the separate External Market
        # Pricing_All Time workbook, not the RSD Scorecard Export.
        ("pct_vs_current_full", "New logic: % vs. current cost"),
        ("pct_vs_renewal", "New logic: % vs. current renewal"),
    ]),
    # rus_dtq / competitive (Declined to Quote, Competitive flags) are excluded
    # here too -- same reason as the Pricing group above: neither Salesforce
    # column exists in this export, so both read as a constant 0 everywhere.
    ("Risk & flags", [
        ("has_illustrative_quote", "Reached illustrative stage"),
        ("has_firm_quote", "Reached firm stage"),
        ("isl_deductible", "ISL deductible"),
        ("laser_liability", "Laser liability $"),
        ("laser_count", "Laser count"),
    ]),
    ("Quote timeline", [
        ("days_created_to_eff", "Days: created → effective date"),
        ("days_created_to_illustrative", "Days: created → illustrative"),
        ("days_illustrative_to_firm", "Days: illustrative → firm"),
        ("days_illustrative_to_uw_complete", "Days: illustrative → UW complete"),
        ("days_uw_complete_to_firm_sent", "Days: UW complete → firm sent"),
    ]),
    # NOT model features -- leakage, only knowable after the decision (see
    # etl_nb.py). Always 0% on the still-open pipeline (a real, honest number
    # -- an open quote genuinely has none of these yet), measured for real on
    # the decided side.
    ("Outcome context (not model features)", [
        ("loss_reason", "Loss reason"),
        ("loss_reason_other", "Loss reason - other"),
        ("notice_of_sale_date", "Notice of sale date"),
    ]),
]


def _completeness(df: pd.DataFrame) -> list[dict]:
    # Which of these fields the model actually trains on. This report is where
    # the 2026-08-26 train/serve problem was visible all along -- a field at
    # 67% on history and 0% on the open pipeline is a broken feature, not just
    # a data gap -- so each row now says whether it is a feature, and the page
    # can show the two facts side by side instead of leaving the reader to
    # cross-reference train_nb.py.
    from app.nb_mode import MODEL_FIELDS

    groups = []
    for label, fields in FIELD_GROUPS:
        present = [(col, name) for col, name in fields if col in df.columns]
        if not present:
            continue
        rows = [
            {"field": name, "complete": round(float(df[col].notna().mean()), 3),
             "is_feature": col in MODEL_FIELDS}
            for col, name in present
        ]
        groups.append({
            "group": label,
            "avg": round(float(np.mean([r["complete"] for r in rows])), 3),
            "fields": rows,
        })
    return groups


def _stage_breakdown(pipeline: pd.DataFrame) -> list[dict]:
    """Per-stage counts + whether each stage's quotes actually have pricing --
    the concrete evidence behind "even 'Firm Quote Released' mostly lacks a
    firm cost number"."""
    out = []
    for stage, g in pipeline.groupby("stage"):
        out.append({
            "stage": stage,
            "n": int(len(g)),
            "has_illustrative": int(g["illustrative_max_cost"].notna().sum()),
            "has_firm": int(g["firm_max_cost"].notna().sum()),
        })
    return sorted(out, key=lambda r: -r["n"])


def build_report() -> dict:
    if not HISTORY_PATH.exists() or not PIPELINE_PATH.exists():
        return {"available": False}

    history = pd.read_csv(HISTORY_PATH)
    pipeline = pd.read_csv(PIPELINE_PATH)
    if len(history) == 0 and len(pipeline) == 0:
        return {"available": False}

    file_mtime = datetime.fromtimestamp(HISTORY_PATH.stat().st_mtime, tz=timezone.utc)

    return {
        "available": True,
        "n_history": len(history),
        "n_pipeline": len(pipeline),
        "history_win_rate": round(float(history["sold"].mean()), 3) if len(history) else None,
        "history_completeness": _completeness(history),
        "pipeline_completeness": _completeness(pipeline),
        "pipeline_by_stage": _stage_breakdown(pipeline),
        "hygiene": {
            # etl_nb.py excludes exactly this one sandbox row -- surfaced here
            # so it's a measured fact, not a claim buried in a code comment.
            "excluded_sandbox_rows": 1,
            "source_file": "RSD Scorecard Export - *.xlsx (NewBusiness/ folder only)",
        },
        "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "source_updated": file_mtime.isoformat(timespec="seconds"),
    }
