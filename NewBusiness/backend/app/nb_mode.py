"""
NewBusiness serving layer — loads the scored pipeline + history + model
registry and builds the full app state the API hands to the dashboard.
Self-contained: reads only from NewBusiness/backend/data and
NewBusiness/backend/models, never from the renewal project.
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import insights_nb
from . import paths

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
from train_nb import NUMERIC, ONEHOT_CATEGORICAL, TARGET_ENC_CATEGORICAL, _prep  # noqa: E402

DATA = paths.backend_dir() / "data"
MODELS = paths.backend_dir() / "models"

# Every field the model actually sees, in the same groupings train_nb.py uses
# -- single source of truth, so the dashboard tables can never silently drift
# out of sync with what the model is actually trained on.
MODEL_FIELDS = NUMERIC + ONEHOT_CATEGORICAL + TARGET_ENC_CATEGORICAL

# Fields the model no longer trains on (the 2026-08-26 serve-consistency
# rework -- see train_nb.py's docstring) but that the per-quote tables still
# show. They are real, recorded data and the Open Pipeline / Win/Loss Database
# pages exist to expose EVERY field on a row, not a curated subset, so
# dropping them from the model must not also drop them from the audit view.
# nbColumns.jsx labels each one "(not a feature)" so nobody reads a populated
# cell as something that moved the score.
CONTEXT_FIELDS = [
    "underwriter", "illustrative_max_cost", "pct_vs_current", "firm_max_cost",
    "illustrative_cost_per_life", "firm_cost_per_life", "isl_deductible",
    "laser_liability", "laser_count", "has_illustrative_quote", "has_firm_quote",
    "days_created_to_illustrative", "days_illustrative_to_firm",
    "days_illustrative_to_uw_complete", "days_uw_complete_to_firm_sent",
    # New Logic's comparison rule (etl_nb.py's _load_market_pricing) -- kept
    # visibly separate from pct_vs_current above rather than blended into it.
    "pct_vs_current_full", "pct_vs_renewal",
]
ALL_MODEL_FIELDS = MODEL_FIELDS + CONTEXT_FIELDS
BOOL_FIELDS = {"has_illustrative_quote", "has_firm_quote"}

SCORED_PATH = DATA / "nb_scored_book.csv"
HISTORY_PATH = DATA / "nb_history.csv"
PIPELINE_PATH = DATA / "nb_pipeline.csv"
MODEL_PATH = MODELS / "nb_latest.joblib"
REGISTRY_PATH = MODELS / "registry.json"

# Every stage a quote can sit in (insights_nb.STAGE_SALES_ESTIMATE's keys are
# the full canonical open-stage list already -- verified against nb_pipeline.
# csv's actual distinct values), plus the two decisions. Single source of
# truth for the Open Pipeline page's stage-editor dropdown (GET
# /api/stage-options) so the frontend can never drift out of sync with it.
STAGE_OPTIONS = list(insights_nb.STAGE_SALES_ESTIMATE.keys()) + ["Closed Won", "Closed Lost"]


def available() -> bool:
    return SCORED_PATH.exists() and HISTORY_PATH.exists() and MODEL_PATH.exists()


def update_stage(quote_id: str, new_stage: str) -> dict:
    """Manually correct one open quote's stage. If the new stage is a
    DECISION (Closed Won/Lost), this moves the quote out of the open pipeline
    and into the permanent Win/Loss Database in the same action -- mirrors
    the renewal project's "move to Renewal Database" button (see
    backend/app/real_mode.py's move_to_history).

    `quote_id` (e.g. "NB0007") encodes the row's position in nb_pipeline.csv /
    nb_scored_book.csv at the moment the caller's `groups` list was built
    (see build_state/_group_row) -- build_book.py writes both files 1:1, same
    row order, so the index still resolves correctly as long as nothing else
    mutated the pipeline in between. The caller MUST reload state (build_state)
    after this returns; every other quote's id shifts once a row is removed.

    Never fabricates the outcome detail fields (notice_of_sale_date,
    loss_reason, loss_reason_other) -- those are genuinely unknown at the
    moment of a manual stage correction, so they're left blank rather than
    guessed (same rule as everywhere else in this project)."""
    if new_stage not in STAGE_OPTIONS:
        raise ValueError(f"unknown stage {new_stage!r} -- must be one of {STAGE_OPTIONS}")
    if not quote_id.startswith("NB"):
        raise ValueError(f"bad quote id {quote_id!r}")
    idx = int(quote_id[2:])

    pipeline = pd.read_csv(PIPELINE_PATH)
    if idx not in pipeline.index:
        raise KeyError(f"quote {quote_id} not found in the open pipeline "
                        f"(it may have already been moved by another update)")

    decided = new_stage in ("Closed Won", "Closed Lost")
    if decided:
        row = pipeline.loc[idx].copy()
        row["stage"] = new_stage
        row["sold"] = 1 if new_stage == "Closed Won" else 0
        row["notice_of_sale_date"] = np.nan
        row["loss_reason"] = np.nan
        row["loss_reason_other"] = np.nan
        # Provenance, so the next ETL rebuild re-appends this row instead of
        # discarding it -- see etl_nb._preserve_local_decisions.
        row["source"] = "manual_transfer"

        history = pd.read_csv(HISTORY_PATH)
        for c in history.columns:
            if c not in row.index:
                row[c] = np.nan
        history = pd.concat([history, pd.DataFrame([row[history.columns]])], ignore_index=True)
        history.to_csv(HISTORY_PATH, index=False)

        pipeline = pipeline.drop(index=idx)
    else:
        pipeline.loc[idx, "stage"] = new_stage
    pipeline.to_csv(PIPELINE_PATH, index=False)

    # Rescore with the ALREADY-trained model (no retrain) so nb_scored_book.csv
    # reflects the edit. `stage` itself isn't a model feature (see train_nb.py's
    # NUMERIC/ONEHOT_CATEGORICAL/TARGET_ENC_CATEGORICAL) so model_score/likelihood_band
    # don't move, but stage_sales_estimate (keyed directly by stage) and
    # pipeline_rank/percentile (recomputed over the open set) need refreshing.
    import build_book
    build_book.build()
    return {"moved_to_history": decided}


def _excl_writer(path: Path, id_cols: tuple[str, str]) -> None:
    if not path.exists():
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([*id_cols, "note", "excluded_at"])


def delete_quote(quote_id: str) -> dict:
    """Permanently remove one open quote — entered in error, a duplicate, a
    sandbox row the dummy-name filter in etl_nb.py missed. Unlike update_stage
    above, this never creates a history row: the quote never reached a real
    decision, so there's nothing to record.

    Edits nb_pipeline.csv directly, same idx-based lookup as update_stage
    (see its docstring), and persists to data/nb_excluded_groups.csv (keyed
    by group_name + created_date, the same identity etl_nb._row_keys uses)
    so a future etl_nb.py rebuild — which reconstructs nb_pipeline.csv from
    the raw export — doesn't bring it back. Same reasoning as
    etl_nb._preserve_local_decisions for stage edits, opposite direction.
    """
    if not quote_id.startswith("NB"):
        raise ValueError(f"bad quote id {quote_id!r}")
    idx = int(quote_id[2:])

    pipeline = pd.read_csv(PIPELINE_PATH)
    if idx not in pipeline.index:
        raise KeyError(f"quote {quote_id} not found in the open pipeline "
                        f"(it may have already been moved or removed)")
    row = pipeline.loc[idx]
    group_name, created_date = row["group_name"], row["created_date"]

    pipeline = pipeline.drop(index=idx).reset_index(drop=True)
    pipeline.to_csv(PIPELINE_PATH, index=False)

    excl_path = DATA / "nb_excluded_groups.csv"
    _excl_writer(excl_path, ("group_name", "created_date"))
    with open(excl_path, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([group_name, created_date, "removed via Open Pipeline",
                                 datetime.now(timezone.utc).isoformat(timespec="seconds")])

    import build_book
    build_book.build()
    return {"group_name": group_name}


def delete_history_record(group_name: str, created_date: str) -> dict:
    """Permanently remove ONE decided quote from the Win/Loss Database —
    entered in error, a stale duplicate. Heavier than delete_quote above:
    this is real training data, not a forward-looking forecast row.

    Two things happen: nb_history.csv is edited directly, right now (this
    process cannot rerun etl_nb.py's raw-workbook ETL — see the note above
    /api/retrain used to be), AND persisted to data/nb_excluded_history.csv
    so a future etl_nb.py rebuild doesn't bring it back. Does NOT retrain —
    same rule as every other data-import path in this app.
    """
    history = pd.read_csv(HISTORY_PATH)
    mask = ((history["group_name"].astype(str).str.strip().str.lower()
             == group_name.strip().lower())
            & (pd.to_datetime(history["created_date"], errors="coerce")
               == pd.to_datetime(created_date, errors="coerce")))
    if not mask.any():
        raise KeyError(f"No matching Win/Loss Database record for "
                        f"{group_name!r} @ {created_date}")
    removed_count = int(mask.sum())
    history = history[~mask].reset_index(drop=True)
    history.to_csv(HISTORY_PATH, index=False)

    excl_path = DATA / "nb_excluded_history.csv"
    _excl_writer(excl_path, ("group_name", "created_date"))
    with open(excl_path, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([group_name, created_date, "removed via Win/Loss Database",
                                 datetime.now(timezone.utc).isoformat(timespec="seconds")])

    return {"group_name": group_name, "removed_count": removed_count}


def auto_expire_lost() -> int:
    """A quote that's still open PAST its own effective date never came on --
    there's no decision left to wait for, so it's lost. Moves every such row
    from nb_pipeline.csv straight into nb_history.csv as Closed Lost, same
    mechanics as a manual update_stage() call (see its docstring for why the
    outcome-detail fields stay blank). Runs automatically every time state is
    rebuilt (app startup, after any edit, after retrain) -- by design, per
    user request 2026-08-25, so a stale quote never needs a human to notice
    and click something.

    `eff_date < today` (strictly before, not <=) -- a quote effective TODAY
    still has the rest of the day to close. Returns the number of quotes
    expired, so the caller can decide whether a rescore is worth doing."""
    if not PIPELINE_PATH.exists():
        return 0
    pipeline = pd.read_csv(PIPELINE_PATH)
    if pipeline.empty:
        return 0
    eff = pd.to_datetime(pipeline["eff_date"], errors="coerce")
    expired = pipeline[eff.notna() & (eff < pd.Timestamp.now().normalize())]
    if expired.empty:
        return 0

    rows = expired.copy()
    rows["stage"] = "Closed Lost"
    rows["sold"] = 0
    rows["notice_of_sale_date"] = np.nan
    rows["loss_reason"] = np.nan
    rows["loss_reason_other"] = np.nan
    rows["source"] = "auto_expired"   # see etl_nb._preserve_local_decisions

    history = pd.read_csv(HISTORY_PATH)
    for c in history.columns:
        if c not in rows.columns:
            rows[c] = np.nan
    history = pd.concat([history, rows[history.columns]], ignore_index=True)
    history.to_csv(HISTORY_PATH, index=False)

    pipeline.drop(index=expired.index).to_csv(PIPELINE_PATH, index=False)
    return len(expired)


def _n(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return float(v) if isinstance(v, (int, float, np.floating, np.integer)) else v


def _load_registry() -> list[dict]:
    if not REGISTRY_PATH.exists():
        return []
    return json.loads(REGISTRY_PATH.read_text())


def _feature_val(r: pd.Series, f: str):
    if f in BOOL_FIELDS:
        v = r.get(f)
        return None if pd.isna(v) else bool(v)
    return _n(r.get(f))


def _all_features(r: pd.Series) -> dict:
    """Every field on this row -- the model's own inputs (MODEL_FIELDS) plus
    the recorded-but-unused ones (CONTEXT_FIELDS) -- literally everything, not
    a curated subset, by request, so a row can be fully audited against what
    actually produced its band and rate."""
    return {f: _feature_val(r, f) for f in ALL_MODEL_FIELDS}


def _group_row(r: pd.Series, track_maps: dict) -> dict:
    row = {
        "quote_id": f"NB{r.name:04d}",
        "group_name": _n(r.get("group_name")),
        "billing_city": _n(r.get("billing_city")),
        "stage": _n(r.get("stage")),
        "eff_date": _n(r.get("eff_date")),
        "created_date": _n(r.get("created_date")),
    }
    row.update(_all_features(r))
    row.update({
        # Headline fields: the BAND's measured historical win rate (honest,
        # pooled over dozens of quotes) + the sample size backing it, NOT the
        # raw per-quote model score -- see insights_nb.score_book's docstring.
        "likelihood_band": _n(r.get("likelihood_band")),
        "band_win_rate": _n(r.get("band_win_rate")),
        "band_n": _n(r.get("band_n")),
        "band_wins": _n(r.get("band_wins")),
        # Exec rule-of-thumb by stage -- separate from band_win_rate above,
        # see insights_nb.STAGE_SALES_ESTIMATE's docstring for why.
        "stage_sales_estimate": _n(r.get("stage_sales_estimate")),
        "pipeline_rank": _n(r.get("pipeline_rank")),
        "pipeline_percentile": _n(r.get("pipeline_percentile")),
        "potential_premium": _n(r.get("potential_premium")),
        "expected_value": _n(r.get("expected_value")),
        # Secondary/technical field -- the model's raw calibrated score, kept
        # for the detail drawer only, not the primary display.
        "model_score": _n(r.get("model_score")),
        "drivers": insights_nb.drivers(r, track_maps),
    })
    return row


def build_state() -> dict:
    if not available():
        raise RuntimeError(
            "NewBusiness data/model artifacts are missing — run etl_nb.py + "
            "train_nb.py + build_book.py first (see NewBusiness/README.md)"
        )
    # Auto-expire anything that's blown past its own effective date without a
    # decision (see auto_expire_lost's docstring) BEFORE loading the scored
    # book, so a stale quote never survives a state rebuild -- runs on every
    # app startup and after every edit/retrain, no human action needed.
    if auto_expire_lost():
        import build_book
        build_book.build()
    pipeline = _prep(pd.read_csv(SCORED_PATH))
    history = _prep(pd.read_csv(HISTORY_PATH))
    registry = _load_registry()
    latest = registry[-1] if registry else {}
    track_maps = insights_nb.track_record_maps(history)

    groups = [_group_row(r, track_maps)
              for _, r in pipeline.sort_values("expected_value", ascending=False).iterrows()]

    return {
        "summary": insights_nb.summary(pipeline, history),
        "segments": insights_nb.segments(pipeline, history),
        # Computed here rather than per-request: it's a full pass over all
        # 9k decided rows and it only changes when the data does, which is
        # exactly when this state is rebuilt anyway.
        "performance": insights_nb.performance(history, pipeline),
        "groups": groups,
        "model_version": latest.get("version"),
        "metrics": latest.get("metrics"),
        "registry": registry,
    }


def history_records() -> list[dict]:
    """Every decided quote (Closed Won/Lost), row-level, for the historical
    database view -- every field the model saw (_all_features), plus the
    identity/outcome fields that are NOT features (see below)."""
    history = _prep(pd.read_csv(HISTORY_PATH))
    out = []
    for _, r in history.iterrows():
        row = {
            "group_name": _n(r.get("group_name")),
            "billing_city": _n(r.get("billing_city")),
            "stage": _n(r.get("stage")),
            "eff_date": _n(r.get("eff_date")),
            "created_date": _n(r.get("created_date")),
        }
        row.update(_all_features(r))
        row.update({
            "won": bool(r.get("sold")),
            # NOT model features -- excluded from training as leakage (only
            # knowable after the decision; see etl_nb.py) -- shown here purely
            # for historical context on WHY a lost quote was lost.
            "loss_reason": _n(r.get("loss_reason")),
            "loss_reason_other": _n(r.get("loss_reason_other")),
            "notice_of_sale_date": _n(r.get("notice_of_sale_date")),
        })
        out.append(row)
    return out
