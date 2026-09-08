"""
Training-data quality report — computed live from the real renewal history.

Powers the Data Quality page in the dashboard. Every number here is MEASURED
from data/real_history.csv (the actual training set the model learns from), so
the report stays honest and refreshes automatically after a Monthly Retrain.

The editorial narrative (what's already been cleaned, the open-items roadmap)
lives in the frontend; this module supplies only the measured facts behind it.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .forward_book import forward_mask
from .paths import backend_dir
from .real_mode import get_or_assign_group_id

BACKEND_DIR = backend_dir()
REAL_HISTORY = BACKEND_DIR / "data" / "real_history.csv"

# Columns that identify/label a record rather than feed the model as a feature.
META_COLS = {"group_name", "eff_date", "source", "renewed", "tenure_is_derived"}

# Raw source code -> human label, for the lineage panel.
SOURCE_LABELS = {
    "tracker": "AM Renewal Tracker (Jul 2023 →)",
    "uw2025": "2025 UW Renewal Sheets",
    "ret2026": "2026 Retention Analysis",
    "renewal2026": "Salesforce Renewal Report (2026 closed)",
}

# Friendly field names + the group each field belongs to, for the completeness panel.
FIELD_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Identity & outcome", [
        ("group_name", "Group name"),
        ("eff_date", "Renewal date"),
        ("renewed", "Renewed / termed"),
        ("product", "Product line"),
    ]),
    ("Relationship & team", [
        ("broker", "Broker"),
        ("am", "Account manager"),
        ("rsd", "Regional sales director"),
        ("tpa", "TPA"),
        ("carrier", "Stop-loss carrier"),
        ("state", "State"),
    ]),
    ("Underwriting signal", [
        ("nlr", "Net loss ratio"),
        ("mature_to_attachment", "Mature claims to attachment"),
        ("isl_loss_ratio", "ISL loss ratio"),
        ("total_increase_pct", "Total renewal increase %"),
        ("initial_uw_increase_pct", "Initial UW increase %"),
        ("fixed_increase_pct", "Fixed-cost increase %"),
        ("annual_premium", "Annual premium (total)"),
        ("premium_stoploss", "Stop-loss premium"),
        ("corridor", "Aggregate corridor"),
        ("lives", "Enrolled lives"),
    ]),
    ("Lasers", [
        ("lasers_current", "Lasers (current)"),
        ("lasers_renewal", "Lasers (renewal)"),
        ("laser_liability", "Laser liability $"),
    ]),
    ("History & tenure", [
        ("tenure_years", "Years with Crumdale"),
        ("bor_change", "Broker-of-record change"),
        ("captive_offer", "Captive offer"),
    ]),
    ("Negotiation detail", [
        ("neg_initial_increase_pct", "Initial increase (negotiation)"),
        ("neg_final_increase_pct", "Final increase (negotiation)"),
    ]),
    ("Broker relationship", [
        ("broker_years_with_cs", "Broker years with CS"),
        ("broker_groups_with_cs", "Broker groups with CS"),
        ("broker_products_sold", "Broker products sold"),
        ("broker_preferred", "Preferred broker"),
    ]),
]

# A row carries a "rich" underwriting signal when its net loss ratio is present.
RICH_FIELD = "nlr"

_PBM_RE = re.compile(r"pbm|pharmac|\brx\b", re.I)


def _derived_share(df: pd.DataFrame, col: str) -> float | None:
    """Fraction of a field's values that are ESTIMATED rather than confirmed.
    Returns None for fields that are always real."""
    if col == "tenure_years" and "tenure_is_derived" in df.columns:
        flag = df["tenure_is_derived"].isin([True, "True", 1, 1.0])
        return round(float(flag.mean()), 3)
    if col == "bor_change":
        # confirmed = real recorded change (2026 sheet field, BOR-changes file, or
        # the broker audit log); the rest is inferred year-over-year.
        if "bor_change_confirmed" in df.columns:
            conf = df["bor_change_confirmed"].isin([True, "True", 1, 1.0])
            return round(float((~conf).mean()), 3)
        if "source" in df.columns:
            return round(float((df["source"] != "ret2026").mean()), 3)
    return None


def _completeness(df: pd.DataFrame) -> list[dict]:
    groups = []
    for label, fields in FIELD_GROUPS:
        present = [(col, name) for col, name in fields if col in df.columns]
        if not present:
            continue
        rows = [
            {"field": name, "complete": round(float(df[col].notna().mean()), 3),
             "derived": _derived_share(df, col)}
            for col, name in present
        ]
        groups.append({
            "group": label,
            "avg": round(float(np.mean([r["complete"] for r in rows])), 3),
            "fields": rows,
        })
    return groups


def build_report() -> dict:
    """Measured data-quality facts for the real training history."""
    if not REAL_HISTORY.exists():
        return {"available": False}

    df = pd.read_csv(REAL_HISTORY, parse_dates=["eff_date"])
    # Match the training set: forward-book months are held out of training, so the
    # training-health report should not count them either (see app/forward_book.py).
    df = df[~forward_mask(df["eff_date"])]
    n = len(df)
    if n == 0:
        return {"available": False}

    names = df["group_name"].astype(str)
    rich = int(df[RICH_FIELD].notna().sum()) if RICH_FIELD in df else 0

    by_year = (
        df.assign(year=df["eff_date"].dt.year)
        .groupby("year")
        .agg(n=("renewed", "size"), renewal_rate=("renewed", "mean"))
        .reset_index()
    )
    years = [
        {"year": int(r.year), "n": int(r.n), "renewal_rate": round(float(r.renewal_rate), 3)}
        for r in by_year.itertuples()
    ]

    sources = [
        {"source": k, "label": SOURCE_LABELS.get(k, k), "n": int(v)}
        for k, v in df["source"].value_counts().items()
    ]

    derived_tenure = int((df["tenure_is_derived"] == True).sum()) if "tenure_is_derived" in df else 0

    hygiene = {
        # 0 = ETL already keeps one record per group per renewal month
        "duplicates": int(df.duplicated(subset=["group_name", "eff_date"]).sum()),
        # names carrying PBM / pharmacy text (a different line of business)
        "pbm_rows": int(names.str.contains(_PBM_RE).sum()),
        # names with embedded line breaks / working notes ("PBM confirmed term.")
        "annotated_names": int(names.str.contains("\n").sum()),
        # tenure that is a derived first-seen proxy, not a true inception count
        "derived_tenure": derived_tenure,
        "derived_tenure_share": round(derived_tenure / n, 3),
    }

    file_mtime = datetime.fromtimestamp(REAL_HISTORY.stat().st_mtime, tz=timezone.utc)

    return {
        "available": True,
        "n_decisions": n,
        "n_rich": rich,
        "rich_share": round(rich / n, 3),
        "fields_tracked": len([c for c in df.columns if c not in META_COLS]),
        "date_start": df["eff_date"].min().date().isoformat(),
        "date_end": df["eff_date"].max().date().isoformat(),
        "renewal_rate": round(float(df["renewed"].mean()), 3),
        "overall_complete": round(float(df.notna().mean().mean()), 3),
        "by_year": years,
        "by_source": sources,
        "completeness": _completeness(df),
        "hygiene": hygiene,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "source_updated": file_mtime.isoformat(timespec="seconds"),
    }


def _cell(v):
    """A single CSV value -> a JSON-safe native Python scalar (NaN -> None)."""
    if v is None:
        return None
    if isinstance(v, float) and np.isnan(v):
        return None
    if isinstance(v, (np.integer, np.floating)):
        return v.item()
    if isinstance(v, np.bool_):
        return bool(v)
    return v


def build_records() -> dict:
    """Row-level renewal history — every historical renewal decision in
    data/real_history.csv, browsable. Powers the Renewal Database page.
    Unlike the Book of Business (the upcoming 2-quarter window), this is the
    full record the model trains on, past and present."""
    if not REAL_HISTORY.exists():
        return {"available": False}

    df = pd.read_csv(REAL_HISTORY, parse_dates=["eff_date"])
    # Forward-book months are the current Book of Business, not past history — hidden
    # here by default. Exception: rows a user explicitly moved over with the "Move to
    # Renewal Database" button (source == manual_transfer) always show — that click is
    # a deliberate override of the default split, for that one group.
    hide = forward_mask(df["eff_date"]) & (df.get("source") != "manual_transfer")
    df = df[~hide]
    n = len(df)
    if n == 0:
        return {"available": False}

    df = df.sort_values("eff_date", ascending=False).reset_index(drop=True)

    records = []
    for i, row in df.iterrows():
        rec = {
            "row_id": f"H{i + 1:04d}",   # this RENEWAL EVENT — unique per row
            "group_id": get_or_assign_group_id(row["group_name"]),  # this COMPANY — same
            # id across every year it renews, and the same id shown for it on
            # Upcoming Renewals / Needs Data. Several rows here can share one group_id.
            "source_label": SOURCE_LABELS.get(row.get("source"), row.get("source")),
        }
        for col in df.columns:
            v = row[col]
            if col == "eff_date":
                rec[col] = v.date().isoformat()
            elif col == "renewed":
                rec[col] = bool(v)
            else:
                rec[col] = _cell(v)
        records.append(rec)

    return {
        "available": True,
        "count": n,
        "date_start": df["eff_date"].min().date().isoformat(),
        "date_end": df["eff_date"].max().date().isoformat(),
        "renewal_rate": round(float(df["renewed"].mean()), 3),
        "records": records,
    }
