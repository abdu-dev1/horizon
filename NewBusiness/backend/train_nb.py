"""
NewBusiness model training — win-likelihood for first-time-client quotes.

Trains on data/nb_history.csv (decided opportunities only: Closed Won/Lost),
produces a calibrated probability model, and scores data/nb_pipeline.csv (the
still-open quotes) with it.

This is a genuinely different modeling problem from the renewal side, not a
copy of it:
  - unit of analysis: a QUOTE, not a renewal
  - label: won a first-time sale (~4.7% base rate) vs. renewed (~60-70% base
    rate on the renewal side) — severe class imbalance, handled with
    class_weight="balanced" + calibration, not just isotonic-on-a-balanced-set
  - leakage rules: Loss Reason / Notice of Sale Date are excluded entirely
    (see etl_nb.py) because they are assigned only after the decision

SERVE CONSISTENCY (the 2026-08-26 rework, and the reason the headline number
went DOWN)
--------------------------------------------------------------------------
The feature set is restricted to fields that exist on an OPEN quote, because
that is the only kind of quote this model is ever asked about. It previously
used 19 numerics including the illustrative/firm quote costs, ISL deductible,
UW turnaround days and the assigned underwriter — all well populated on decided
history and all essentially absent on the open pipeline:

    isl_deductible                67.4% decided ->  0.0% open
    has_illustrative_quote        74.9% decided ->  4.4% open
    days_created_to_illustrative  61.1% decided ->  0.0% open
    underwriter                   41.9% decided ->  0.0% open
    firm_* / days_illustrative_*  13-21% decided ->  0.0% open

So it was graded under conditions that never hold in production, and it was
reporting ROC 0.944 / PR 0.505 while actually operating at ROC 0.736 / PR 0.203
on a feature-poor open quote (experiment_nb.py, eval B). Worse, since it had
learned "firm quote exists -> likely win" and no open quote has one, the
ranking it produced ran BACKWARDS against pipeline stage: every quote at a
Firm Quote stage landed in the bottom band.

Training on the serve-available set instead measures ROC 0.804 / PR 0.255 —
lower than the old headline, higher than the old reality (+0.068 ROC / +0.052
PR over eval B), and it's a number that holds at scoring time.

Every feature below then had to earn its place by ablation (dropping
billing_state costs 0.028 PR, broker 0.016, all five numerics 0.023), and four
plausible changes were rejected by measurement rather than taste:

  geographic target encoding (billing_zip3 + billing_city)  -0.065 ROC
  renewal-vs-current-cost ratio                              no effect
  eff_month target-encoded instead of one-hot                no effect
  recency weighting (half-life 1y / 1.5y / 2y / 3y)          -0.006 ROC

That last one is worth stating plainly, because the motivation was real: the
book's win rate is NOT stationary (11.1% in 2022 -> 4.9% -> 5.2% -> 4.4% ->
2.2% in 2026 to date), so down-weighting old quotes should have helped. It
didn't -- best case it traded 0.006 ROC overall for 0.006 ROC on the last-18-
months slice, which is inside two-seed noise. Not adopted; the complexity has
to buy something measurable. Re-test if the trend continues, since the case
for it gets stronger as the regimes diverge further.

See experiment_nb.py for all of it, including tune_bands (where the band
thresholds come from) and round4_laser_trap (an instructive near-miss:
laser_count IMPROVES history CV but is constant 0 on every open quote).

Honesty: metrics reported are from stratified 5-fold cross-validation on the
decided rows (never scored on data used to fit that fold) — the same "don't
grade the model on what it could have memorized" principle as the renewal
project's walk_forward.py, via CV rather than a time-ordered split.

Run:  python train_nb.py   (from NewBusiness/backend/)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import (
    ExtraTreesClassifier,
    HistGradientBoostingClassifier,
    RandomForestClassifier,
    VotingClassifier,
)
from sklearn.impute import SimpleImputer
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from nb_encoders import SmoothedTargetEncoder

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))
from app import insights_nb, paths  # noqa: E402

DATA = paths.backend_dir() / "data"
MODELS = paths.backend_dir() / "models"

# Serve-available numerics only -- see the module docstring. `lives` and
# `days_created_to_eff` are ~100% populated on both sides; the three cost
# fields are partial on open quotes (32% / 12% / 32%) but they have real
# VARIANCE there, which is what matters -- a feature that is constant at
# scoring time cannot inform a ranking no matter how predictive it looks on
# history.
NUMERIC = [
    "lives", "current_max_cost", "current_renewal",
    "current_cost_per_life", "days_created_to_eff",
]
# Deliberately NOT features, and why:
#   illustrative_max_cost, pct_vs_current, illustrative_cost_per_life,
#   firm_max_cost, firm_cost_per_life, isl_deductible, laser_liability,
#   laser_count, has_illustrative_quote, has_firm_quote,
#   days_created_to_illustrative, days_illustrative_to_firm,
#   days_illustrative_to_uw_complete, days_uw_complete_to_firm_sent
#     -> 0-4% populated on the open pipeline (laser_count and has_firm_quote
#        are literally constant there). Dropping them measurably IMPROVED
#        real-world performance; see the docstring.
#   firm_w_laser, range_vs_current, renewal_range, rus_dtq, competitive
#     -> this export never carries those Salesforce report-layer columns at
#        all, on either side, so etl_nb.py drops them from the data model.
#   pct_vs_renewal, pct_vs_current_full
#     -> New Logic's comparison rule (etl_nb.py's _load_market_pricing, added
#        2026-09-03). Real coverage on history now (45%/72%), but only
#        4-10% on the open pipeline -- same disqualifying reason as the
#        group above, not a judgment on the logic itself. Shown on the
#        Open Pipeline / Win/Loss Database tables as context, clearly
#        labeled as New Logic, never blended into pct_vs_current above.
#   stage
#     -> the single most predictive field available on an open quote, and
#        unusable: on decided history stage IS the label (Closed Won/Lost), and
#        the export preserves no stage-at-decision-time. The exec team's
#        rule-of-thumb close rate per stage is surfaced separately and
#        unblended instead (insights_nb.STAGE_SALES_ESTIMATE). Capturing stage
#        history in the export is the highest-value data change available to
#        this model.
# Low-cardinality categoricals (a handful of buckets each) -> plain one-hot.
ONEHOT_CATEGORICAL = ["product", "eff_month_num"]
# High-cardinality "identity" categoricals (broker has 740 distinct values
# against 446 wins) -> smoothed win-rate target encoding instead of one-hot.
# This is both the more honest choice (an identity one-hot with that few
# positives per category is pure overfitting bait) and the more informative
# one: what predicts a win is a broker's/RSD's TRACK RECORD, not which specific
# one they are. See SmoothedTargetEncoder.
#
# `underwriter` was dropped from this list: 0% populated on open quotes, so it
# fed every open quote the same global-mean fallback, and it carries no signal
# on history either (4.98% win rate when assigned vs. 4.42% when not).
TARGET_ENC_CATEGORICAL = ["broker", "rsd", "industry", "billing_state"]
CATEGORICAL = ONEHOT_CATEGORICAL + TARGET_ENC_CATEGORICAL
LABEL = "sold"


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["eff_month_num"] = pd.to_datetime(df["eff_date"], errors="coerce").dt.month.astype("Int64").astype(str)
    return df


def _preprocessor() -> ColumnTransformer:
    numeric_tf = Pipeline([("impute", SimpleImputer(strategy="median"))])
    onehot_tf = Pipeline([
        ("impute", SimpleImputer(strategy="constant", fill_value="__missing__")),
        ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
    ])
    target_enc_tf = SmoothedTargetEncoder(columns=TARGET_ENC_CATEGORICAL)
    return ColumnTransformer([
        ("num", numeric_tf, NUMERIC),
        ("onehot", onehot_tf, ONEHOT_CATEGORICAL),
        ("target_enc", target_enc_tf, TARGET_ENC_CATEGORICAL),
    ])


def _build_single(n_estimators: int = 300) -> Pipeline:
    """Single-learner baseline (HistGradientBoosting) -- kept as the A/B
    comparison point for _build_ensemble, not shipped on its own."""
    base = HistGradientBoostingClassifier(
        max_iter=n_estimators, learning_rate=0.05, max_depth=4,
        class_weight="balanced", random_state=42,
    )
    clf = CalibratedClassifierCV(base, method="sigmoid", cv=5)
    return Pipeline([("prep", _preprocessor()), ("clf", clf)])


def _build_ensemble(n_estimators: int = 300) -> Pipeline:
    """RF + ET + HGB soft-voting ensemble, same spirit as the renewal
    project's production ensemble: three diverse tree learners so no single
    model's blind spot dominates. All three get class_weight="balanced" (the
    2.8% win rate needs it structurally, not just via calibration). HGB is
    weighted heaviest, as on the renewal side, since it's the only learner
    that trains on residuals rather than bagged independent trees."""
    rf = RandomForestClassifier(
        n_estimators=n_estimators, min_samples_leaf=3, max_features="sqrt",
        class_weight="balanced_subsample", n_jobs=-1, random_state=7,
    )
    et = ExtraTreesClassifier(
        n_estimators=n_estimators, min_samples_leaf=3, max_features="sqrt",
        class_weight="balanced", n_jobs=-1, random_state=11,
    )
    hgb = HistGradientBoostingClassifier(
        learning_rate=0.05, max_iter=n_estimators, max_leaf_nodes=15,
        l2_regularization=1.0, class_weight="balanced", random_state=13,
    )
    ens = VotingClassifier([("rf", rf), ("et", et), ("hgb", hgb)], voting="soft",
                            weights=[0.8, 0.8, 1.4])
    # sigmoid (Platt), not isotonic: isotonic needs more positives-per-bin than
    # the ~15-16 wins a 5-fold split leaves per calibration fold to stay stable.
    clf = CalibratedClassifierCV(ens, method="sigmoid", cv=5)
    return Pipeline([("prep", _preprocessor()), ("clf", clf)])


def _build_pipeline() -> Pipeline:
    return _build_ensemble()


def _cv_predict(X: pd.DataFrame, y: np.ndarray, n_splits: int = 5, builder=_build_pipeline) -> np.ndarray:
    """Pooled out-of-fold predicted probabilities — every row scored by a model
    that never saw it during that fold's fit. Computed ONCE and reused for both
    the headline metrics and the band-reliability table below, since it's the
    expensive step (a full ensemble refit per fold)."""
    skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
    return cross_val_predict(builder(), X, y, cv=skf, method="predict_proba")[:, 1]


def _metrics_from_oof(proba: np.ndarray, y: np.ndarray, n_splits: int = 5) -> dict:
    return {
        "roc_auc": float(roc_auc_score(y, proba)),
        "pr_auc": float(average_precision_score(y, proba)),
        "base_rate": float(y.mean()),
        "n": int(len(y)),
        "n_won": int(y.sum()),
        "cv_folds": n_splits,
    }


def train_and_save(verbose: bool = True) -> dict:
    history = _prep(pd.read_csv(DATA / "nb_history.csv"))
    X = history[NUMERIC + CATEGORICAL]
    y = history[LABEL].to_numpy()

    oof_proba = _cv_predict(X, y)
    metrics = _metrics_from_oof(oof_proba, y)
    # The band's REAL historical win rate, not the model's raw per-quote score
    # (see insights_nb.band_reliability's docstring) — this is what the
    # dashboard headlines; the raw score is demoted to a secondary field.
    band_reliability = insights_nb.band_reliability(oof_proba, y)
    if verbose:
        print(f"5-fold CV (honest, never scored on its own training fold): "
              f"ROC AUC {metrics['roc_auc']:.3f}, PR AUC {metrics['pr_auc']:.3f} "
              f"(base rate {metrics['base_rate']:.1%}, n={metrics['n']}, wins={metrics['n_won']})")
        print("Band reliability (measured historical win rate per band):")
        for t in insights_nb.BAND_ORDER:
            r = band_reliability[t]
            print(f"  {t:<10} n={r['n']:>5}  wins={r['wins']:>3}  rate={r['rate']:.1%}" if r["rate"] is not None
                  else f"  {t:<10} n=0")

    pipeline = _build_pipeline()
    pipeline.fit(X, y)

    MODELS.mkdir(parents=True, exist_ok=True)
    version = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    model_path = MODELS / "nb_latest.joblib"
    joblib.dump(pipeline, model_path)

    registry_path = MODELS / "registry.json"
    registry = json.loads(registry_path.read_text()) if registry_path.exists() else []
    registry.append({
        "version": version,
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "metrics": metrics,
        "band_reliability": band_reliability,
        "n_history": len(history),
    })
    registry_path.write_text(json.dumps(registry, indent=2))

    if verbose:
        print(f"model saved -> {model_path} (version {version})")

    return {"version": version, "metrics": metrics, "band_reliability": band_reliability}


def score_pipeline(verbose: bool = True) -> pd.DataFrame:
    """Score every still-open quote in data/nb_pipeline.csv with the latest
    trained model -> data/nb_scored_book.csv.

    Produces `model_score`: the raw per-quote calibrated output. This is a
    SECONDARY/technical field now, not the headline number — see
    build_book.py, which attaches the band's measured historical win rate
    (insights_nb.band_reliability) as the honest, dashboard-facing figure.
    """
    model_path = MODELS / "nb_latest.joblib"
    if not model_path.exists():
        raise FileNotFoundError(f"{model_path} not found — run train_and_save() first")
    pipeline = joblib.load(model_path)

    open_book = _prep(pd.read_csv(DATA / "nb_pipeline.csv"))
    X = open_book[NUMERIC + CATEGORICAL]
    open_book["model_score"] = pipeline.predict_proba(X)[:, 1]

    out_path = DATA / "nb_scored_book.csv"
    open_book.to_csv(out_path, index=False)
    if verbose:
        print(f"scored {len(open_book)} open quotes -> {out_path}")
        print(open_book[["group_name", "stage", "model_score"]]
              .sort_values("model_score", ascending=False).head(10).to_string(index=False))
    return open_book


if __name__ == "__main__":
    train_and_save()
    score_pipeline()
