"""
Train Horizon's production-grade ensemble on the REAL renewal history
(real_history.csv from etl_real.py) and score the real active book.

Soft-voting ensemble - RF + ExtraTrees + HistGB with isotonic calibration -
on a real-schema preprocessor (median imputation + missing indicators for
numerics, one-hot for categoricals).

Outputs:
  models/real_latest.joblib   (pipeline + metrics + diagnostics + backfill)
  data/real_scored_book.csv
  registry entries (kind: "real" / "real_monthly")
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import (
    ExtraTreesClassifier,
    HistGradientBoostingClassifier,
    RandomForestClassifier,
    VotingClassifier,
)
from sklearn.impute import MissingIndicator, SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    brier_score_loss,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)
from sklearn.model_selection import StratifiedKFold, cross_val_predict, cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from app.model import append_registry
from app.forward_book import FORWARD_BOOK_MONTHS, forward_mask
from app.paths import backend_dir

BACKEND = backend_dir()
HISTORY = BACKEND / "data" / "real_history.csv"
ACTIVE = BACKEND / "data" / "real_active_book.csv"
MODEL_OUT = BACKEND / "models" / "real_latest.joblib"
# NOT real_scored_book.csv: that file is build_book.py's canonical output (sourced from
# sf_deals.csv, with every current-cycle group + leak flags — what real_mode.py serves).
# This script's score_active() is a lighter debug/sanity-check pass over the raw active
# book; writing it elsewhere means a retrain here can never clobber the canonical book.
SCORED_OUT = BACKEND / "data" / "real_active_book_scored_debug.csv"

NUMERIC = [
    "lives", "annual_premium", "nlr", "isl_loss_ratio", "mature_to_attachment",
    # agg_loss_ratio (Aggregate-layer-only loss ratio) and ratio_to_attachment (how
    # close/over the aggregate attachment point claims are running) — from the AM
    # Experience Report's "Experience Table" sheet, same prior-year expiring-policy
    # merge as nlr (see etl_real.py). Distinct from nlr (which is Spec+Agg combined):
    # a group can be fine on nlr but running hot specifically on the aggregate layer.
    "agg_loss_ratio", "ratio_to_attachment",
    "fixed_increase_pct", "total_increase_pct", "initial_uw_increase_pct",
    "lasers_current", "lasers_renewal", "laser_liability", "tenure_years",
    "broker_groups_with_cs", "broker_years_with_cs",
    "broker_products_sold", "broker_preferred", "renewal_month",
    # kept after the All-Data rebuild: backfills the premium signal annual_premium lost
    # when it became total-renewal-only (leakage-safe). corridor / isl_deductible /
    # prior_nlr_y1 were tried as features but DROPPED — they added noise on 606 rows
    # and regressed CV-AUC (0.821 -> 0.794). corridor stays a DATA column for the BoB.
    "premium_stoploss",
    # Loss-ratio SEVERITY features (engineered in prepare() from nlr). Raw nlr only
    # gets a monotonic nudge; these give the model explicit "how hot" signal so it
    # reacts sharply to catastrophic-loss-ratio groups — the profile of the 2026
    # book the company non-renewed. Validated to lift out-of-time AUC 0.727->0.731,
    # rich-subset AUC 0.789->0.795, and pull the >=150%-loss-ratio band's predicted
    # renewal down toward its 16% actual (was over-predicting at 37%).
    "nlr_hot", "nlr_severe",
    # nlr_catastrophic_200 marks the real breakpoint in the actual book: renewal
    # rate steps 74%(<50% loss) -> 46.7%(150-200%) -> 35.7%(200-250%) -> 19%(250-300%),
    # i.e. crossing 200% is where it gets drastic. One bucket, not a ladder of
    # near-duplicates — extra 250%/300% buckets were tried and just diluted nlr's
    # own permutation importance via collinearity without adding real signal.
    # nlr_tenure_product is the explicit tenure-override lever — it's 0 unless
    # loss ratio is already elevated (nlr > 2.0), and then it SCALES WITH TENURE,
    # so its own monotonic -1 constraint forces high-tenure, high-loss accounts
    # down instead of letting tenure_years' +1 prior offset the loss signal (the
    # failure mode that let one 272%-loss/high-tenure renewer pull 24 similar
    # high-loss termers up toward ~70% predicted renewal).
    "nlr_catastrophic_200", "nlr_tenure_product",
    # tenure_years_capped is the counterpart fix: the real book shows tenure's
    # protective effect is really a 1yr(42%)->2yr(74%) step that then plateaus
    # (76-82% from 2yr to 8yr) — NOT a linear "more is always better" signal.
    # Capping at 3yr and moving the monotonic +1 prior onto the capped version
    # (raw tenure_years keeps its value but loses the constraint) stops tenure
    # from buying unlimited protection for high-tenure accounts, which is what
    # let it outrank and override loss ratio in the first place.
    "tenure_years_capped",
    # rate_increase_penalty: real book shows renewal increase % barely matters
    # below 30% (renewal rate is flat ~74-80% from 0-30%) and then falls off a
    # cliff above it (48.8% at 30-50%, 26.2% at 50-100%, 9.5% at 100%+). Clipping
    # at the real breakpoint (vs. total_increase_pct's plain linear -1 nudge over
    # its whole range) gives the model explicit signal at the point that matters.
    # A second bucket at 50% (rate_increase_severe) was tried and made things
    # worse — same collinearity dilution as the nlr multi-bucket attempt: it
    # dropped total_increase_pct's own rank AND holdout accuracy (0.681->0.639).
    # One clipped-linear breakpoint feature, not a ladder, is what actually held.
    "rate_increase_penalty",
]
# NOTE: tenure_is_derived is deliberately NOT a feature — it's a data-provenance
# flag, and "we have confirmed data for this group" correlates with survival
# (rich-data groups skew toward renewers), so using it leaks the outcome. The
# tenure VALUE is kept; the flag stays only for the Data Quality page.
# carrier_changed was built and tested but added 0.000 to CV AUC (only ~7% coverage),
# so it's NOT a feature — kept in the data file for reference only.
BOOLEAN = ["bor_change", "captive_offer"]
# Dropped the high-cardinality IDENTITY fields (broker, am, rsd): on 606 rows they
# carried negative/zero permutation importance (overfit noise) and don't causally
# generalize — a new AM/RSD/broker shouldn't shift renewal odds; they mostly encode
# "who handles the hard accounts". Removing them lifted 3-seed CV-AUC 0.799 -> 0.802
# and cut variance. product (LF/SF), carrier and state stay — real, low-cardinality
# signal. (broker/am/rsd remain DATA columns for the BoB, just not model features.)
CATEGORICAL = ["product", "carrier", "state"]
FEATURES = NUMERIC + BOOLEAN + CATEGORICAL

# Monotonic priors for the HGB ensemble member: -1 = higher value -> lower renewal
# probability, +1 = higher -> higher, 0 (default, omitted) = unconstrained. Only set
# where the sign is well-established underwriting logic; ambiguous drivers (lives,
# premium, broker book-size/tenure-with-broker) stay unconstrained. This is a
# regularizer for a ~600-row dataset, not a claim of causal certainty - see
# jul2026-forward-test memory, where the unconstrained ensemble ranked a renewer
# BELOW two termers on the one forward cohort with confirmed outcomes.
MONOTONIC_SIGN = {
    "nlr": -1, "isl_loss_ratio": -1, "mature_to_attachment": -1,
    "agg_loss_ratio": -1, "ratio_to_attachment": -1,
    "fixed_increase_pct": -1, "total_increase_pct": -1, "initial_uw_increase_pct": -1,
    "lasers_current": -1, "lasers_renewal": -1, "laser_liability": -1,
    # tenure_years itself is now UNCONSTRAINED (0, i.e. omitted) — its monotonic
    # +1 prior moved to tenure_years_capped below so extreme tenure can't buy
    # unlimited protection.
    "bor_change": -1,
    "nlr_hot": -1, "nlr_severe": -1,
    "nlr_catastrophic_200": -1,
    # -1 here is what actually lets loss ratio override tenure: as this product
    # rises (elevated loss combined with more tenure), predicted renewal can only
    # fall, directly counteracting tenure_years_capped's +1 prior for that account.
    "nlr_tenure_product": -1,
    "tenure_years_capped": 1,
    "rate_increase_penalty": -1,
}

LABELS = {
    "lives": "Enrolled lives", "annual_premium": "Annual premium",
    "nlr": "Net loss ratio (w/ rebates)", "isl_loss_ratio": "ISL loss ratio",
    "agg_loss_ratio": "Aggregate-layer loss ratio", "ratio_to_attachment": "Ratio to attachment point",
    "nlr_hot": "Loss ratio above 100%", "nlr_severe": "Severe loss ratio (150%+)",
    "nlr_catastrophic_200": "Catastrophic loss ratio (200%+)",
    "nlr_tenure_product": "Loss ratio x tenure (high-loss override)",
    "premium_stoploss": "Stop-loss premium",
    "tenure_years_capped": "Tenure with Crumdale (capped at 3yr)",
    "rate_increase_penalty": "Renewal increase above 30%",
    "mature_to_attachment": "Mature claims to attachment",
    "fixed_increase_pct": "Fixed cost increase %",
    "total_increase_pct": "Total renewal increase %",
    "initial_uw_increase_pct": "Initial UW increase %",
    "lasers_current": "Lasers (current)", "lasers_renewal": "Lasers (renewal)",
    "laser_liability": "Laser liability $", "tenure_years": "Tenure with Crumdale",
    "broker_groups_with_cs": "Broker book size w/ CS",
    "broker_years_with_cs": "Broker years w/ CS",
    "broker_products_sold": "Broker products sold",
    "broker_preferred": "Preferred broker", "renewal_month": "Renewal month",
    "bor_change": "BOR change",
    "captive_offer": "Captive offer", "carrier_changed": "Stop-loss carrier changed",
    "tenure_is_derived": "Tenure derived flag",
    "product": "Product (LF/SF)", "rsd": "RSD", "am": "Account manager",
    "carrier": "Carrier", "state": "State", "broker": "Broker",
}


# Recency weighting: the book has regime-shifted (recent renewals are more term-heavy),
# and the unweighted ensemble was over-optimistic in the 0.6-0.8 band. Down-weighting
# older decisions by a 9-month half-life lifted walk-forward balanced accuracy 0.726->0.770
# and accuracy 0.705->0.743 (leak-free), while holding AUC ~0.80 — validated in
# experiment.py. w = 0.5 ** (age_in_months / RECENCY_HALFLIFE_M).
RECENCY_HALFLIFE_M = 9


def recency_weights(eff_dates) -> np.ndarray:
    d = pd.to_datetime(eff_dates)
    ref = d.max().to_period("M")
    age = d.dt.to_period("M").apply(lambda p: (ref - p).n).clip(lower=0)
    return (0.5 ** (age / RECENCY_HALFLIFE_M)).to_numpy()


# Loss-ratio override layer. The multivariate ensemble was directly shown to be
# UNRELIABLE for catastrophic-loss accounts on ~600 rows: for one 300%-loss group
# (Carlson Distributing, see model-loss-ratio-fixes memory), the three CalibratedClassifierCV
# folds' raw ensemble scores ranged 0.13 to 0.75 depending on which random training
# subset each fold saw — too little genuinely-catastrophic-loss data (~20-50 rows per
# severity band) for the full 27-feature model to pin down a stable answer, and the
# noise regresses toward the ~63% base rate instead of the real ~19-36% outcome for
# that band. Feature engineering and monotonic constraints (nlr_catastrophic_200,
# nlr_tenure_product, HGB weight) reduce this but don't eliminate the instability.
#
# The fix: a dedicated, low-variance univariate curve of P(renew | nlr) alone — a
# single monotonic isotonic fit needs far less data to be stable than a 27-feature
# ensemble — and blend the full model toward it as loss ratio crosses into severe
# territory. This is the literal mechanism for "loss ratio overrides everything else":
# below 100% loss the full multivariate model (tenure, broker, rate increase, etc.)
# is trusted completely; from 100% to 300% loss, trust ramps down to zero, so by 300%
# the served number IS the robust nlr-only estimate, full stop — no amount of tenure
# or broker signal can pull it back up.
# LOWERED 2026-07-22 (was 1.5-3.0): the multivariate model was running broadly
# over-optimistic in the 100-150% "running hot" band too (e.g. a 108.6%-loss,
# 101%-increase group scored 58% renewal likelihood with zero help from this
# safety net, since it sat just under the old 150% gate). Starting the ramp at
# nlr_hot's own threshold (100%) means "running hot" groups get real protection
# too, not just "severe"/"catastrophic" ones.
OVERRIDE_NLR_LO, OVERRIDE_NLR_HI = 1.0, 3.0


def _override_alpha(nlr: np.ndarray) -> np.ndarray:
    """Weight on the multivariate model (vs. the univariate nlr curve). 1.0 below
    150% loss ratio, ramping linearly to 0.0 by 300%+. NaN nlr -> 1.0 (no override
    when severity is unknown — nothing to justify overriding on)."""
    nlr = np.asarray(nlr, dtype=float)
    t = np.clip((nlr - OVERRIDE_NLR_LO) / (OVERRIDE_NLR_HI - OVERRIDE_NLR_LO), 0, 1)
    alpha = 1.0 - t
    return np.where(np.isnan(nlr), 1.0, alpha)


def fit_nlr_curve(nlr: pd.Series, y: pd.Series, sample_weight: np.ndarray) -> IsotonicRegression:
    mask = nlr.notna().to_numpy()
    curve = IsotonicRegression(increasing=False, out_of_bounds="clip", y_min=0.03, y_max=0.97)
    curve.fit(nlr.to_numpy()[mask], y.to_numpy()[mask], sample_weight=sample_weight[mask])
    return curve


def apply_override(model_p: np.ndarray, nlr: pd.Series, curve: IsotonicRegression) -> np.ndarray:
    nlr_arr = pd.to_numeric(nlr, errors="coerce").to_numpy()
    alpha = _override_alpha(nlr_arr)
    nlr_filled = np.where(np.isnan(nlr_arr), 0.0, nlr_arr)  # value unused where alpha==1
    curve_p = curve.predict(nlr_filled)
    return alpha * model_p + (1 - alpha) * curve_p


# Renewal-increase override layer — same instability problem as the loss-ratio one
# above, for the OTHER feature shown to matter this much. Only ~20 historical rows
# sit at 100%+ increase (vs. ~600 total) — too few for the full 27-feature model to
# pin down reliably: it was giving one 101%-increase account 58% renewal likelihood
# when the actual 100%+ band renews at 10% (n=20). Same fix, mirrored: trust the
# full model below the real breakpoint (30%, the same one rate_increase_penalty
# already clips at), ramp toward a dedicated univariate P(renew | increase%) curve
# as it climbs, fully overridden by 100%+. Applied AFTER the nlr override in every
# call site (sequential blend) — whichever signal is more extreme for a given
# account pulls harder toward its own curve.
OVERRIDE_INC_LO, OVERRIDE_INC_HI = 30.0, 100.0


def _effective_increase(df: pd.DataFrame) -> pd.Series:
    """Best-available renewal-increase figure per row — same preference order the UI
    displays (total/w-lasers first, else the initial UW figure)."""
    tot = pd.to_numeric(df.get("total_increase_pct"), errors="coerce")
    iuw = pd.to_numeric(df.get("initial_uw_increase_pct"), errors="coerce")
    return tot.fillna(iuw)


def _override_alpha_inc(inc: np.ndarray) -> np.ndarray:
    inc = np.asarray(inc, dtype=float)
    t = np.clip((inc - OVERRIDE_INC_LO) / (OVERRIDE_INC_HI - OVERRIDE_INC_LO), 0, 1)
    alpha = 1.0 - t
    return np.where(np.isnan(inc), 1.0, alpha)


def fit_increase_curve(inc: pd.Series, y: pd.Series, sample_weight: np.ndarray) -> IsotonicRegression:
    mask = inc.notna().to_numpy()
    curve = IsotonicRegression(increasing=False, out_of_bounds="clip", y_min=0.03, y_max=0.97)
    curve.fit(inc.to_numpy()[mask], y.to_numpy()[mask], sample_weight=sample_weight[mask])
    return curve


def apply_increase_override(model_p: np.ndarray, inc: pd.Series, curve: IsotonicRegression) -> np.ndarray:
    inc_arr = pd.to_numeric(inc, errors="coerce").to_numpy()
    alpha = _override_alpha_inc(inc_arr)
    inc_filled = np.where(np.isnan(inc_arr), 0.0, inc_arr)
    curve_p = curve.predict(inc_filled)
    return alpha * model_p + (1 - alpha) * curve_p


def prepare(df: pd.DataFrame, category_map: dict | None = None) -> pd.DataFrame:
    out = df.copy()
    out["renewal_month"] = pd.to_datetime(out["eff_date"]).dt.month
    for c in BOOLEAN:
        if c not in out.columns:
            out[c] = np.nan
        out[c] = out[c].map(
            {True: 1.0, False: 0.0, "True": 1.0, "False": 0.0}
        ).astype(float)
    for c in CATEGORICAL:
        if c not in out.columns:
            out[c] = None
        out[c] = out[c].fillna("Unknown").astype(str).str.strip().str.title()
    if category_map:
        for c, keep in category_map.items():
            out[c] = out[c].where(out[c].isin(set(keep)), "Other")
    # Loss-ratio severity, derived from nlr. Computed here (not in the ETL) so any
    # dynamically-scored row (not just ETL-time history rows) gets them recomputed
    # consistently. nlr_hot is NaN when nlr is unknown (median-imputed like any numeric); nlr_severe
    # defaults to 0 (not-severe) when unknown, with nlr's own missing-indicator carrying
    # the "we don't know" signal.
    nlr_num = (pd.to_numeric(out["nlr"], errors="coerce")
               if "nlr" in out.columns else pd.Series(np.nan, index=out.index))
    out["nlr_hot"] = np.clip(nlr_num - 1.0, 0, None)
    out["nlr_severe"] = (nlr_num >= 1.5).astype(float)
    out["nlr_catastrophic_200"] = (nlr_num >= 2.0).astype(float)
    tenure_num = (pd.to_numeric(out["tenure_years"], errors="coerce")
                  if "tenure_years" in out.columns else pd.Series(np.nan, index=out.index))
    out["nlr_tenure_product"] = np.clip(nlr_num - 2.0, 0, None) * tenure_num
    out["tenure_years_capped"] = np.clip(tenure_num, None, 3.0)
    inc_num = (pd.to_numeric(out["total_increase_pct"], errors="coerce")
               if "total_increase_pct" in out.columns else pd.Series(np.nan, index=out.index))
    out["rate_increase_penalty"] = np.clip(inc_num - 30.0, 0, None)
    for c in NUMERIC:
        if c not in out.columns:
            out[c] = np.nan
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out


def freeze_categories(df: pd.DataFrame, min_count: int = 4) -> dict:
    """Dataset-wide category vocabulary per categorical column, frozen once so the
    one-hot output width never shifts between CV folds / walk-forward slices / the
    holdout fit vs. the full-data production refit - a fixed width is required for
    monotonic_cst, which is keyed by feature position. Categories seen < min_count
    times anywhere collapse to 'Other' (mirrors the old per-fit min_frequency, but
    frozen against the whole dataset instead of being fold-size-dependent)."""
    freeze = {}
    for c in CATEGORICAL:
        counts = df[c].value_counts()
        keep = set(counts[counts >= min_count].index) | {"Unknown"}
        freeze[c] = sorted(keep | {"Other"})
    return freeze


def _monotonic_cst(category_map: dict) -> list[int]:
    """Position-aligned constraint array matching the ColumnTransformer's fixed
    output order: NUMERIC+BOOLEAN values, then their missing-indicators (always
    unconstrained), then the one-hot categorical block (always unconstrained)."""
    n_cat_cols = sum(len(v) for v in category_map.values())
    num_signs = [MONOTONIC_SIGN.get(f, 0) for f in NUMERIC + BOOLEAN]
    return num_signs + [0] * len(NUMERIC + BOOLEAN) + [0] * n_cat_cols


def _preprocessor(category_map: dict) -> ColumnTransformer:
    return ColumnTransformer([
        ("num", SimpleImputer(strategy="median"), NUMERIC + BOOLEAN),
        # features="all" (not the default "missing-only") so every fit - full data,
        # a CV fold, a walk-forward slice - emits the same fixed number of indicator
        # columns regardless of which columns happen to have missing values in it.
        ("num_missing", MissingIndicator(features="all"), NUMERIC + BOOLEAN),
        ("cat", OneHotEncoder(categories=[category_map[c] for c in CATEGORICAL],
                              handle_unknown="ignore", sparse_output=False),
         CATEGORICAL),
    ])


def _build_ensemble(category_map: dict, n_estimators: int = 600) -> VotingClassifier:
    """The production RF+ET+HGB soft-voting ensemble, with no calibration wrapper.
    Single source of truth for the ensemble's architecture/weights - build_pipeline()
    and experiment.py both call this so a comparison script can never silently drift
    from what's actually shipped (previously experiment.py hard-coded stale weights
    [1.0, 0.8, 1.2] instead of production's [0.6, 0.4, 3.0] and gave misleading A/B
    results until caught 2026-08-04)."""
    monotonic_cst = _monotonic_cst(category_map)
    rf = RandomForestClassifier(
        n_estimators=n_estimators, min_samples_leaf=4, max_features="sqrt",
        class_weight="balanced_subsample", n_jobs=-1, random_state=7,
    )
    et = ExtraTreesClassifier(
        n_estimators=n_estimators, min_samples_leaf=5, max_features="sqrt",
        class_weight="balanced", n_jobs=-1, random_state=11,
    )
    hgb = HistGradientBoostingClassifier(
        learning_rate=0.05, max_iter=350, max_leaf_nodes=15,
        l2_regularization=1.0, monotonic_cst=monotonic_cst, random_state=13,
    )
    # Weights favor HGB heavily: it's the only member with monotonic_cst, so it's
    # the only one that structurally enforces "high loss ratio can't be outvoted
    # by tenure/broker signal." RF/ET have no such constraint and were diluting
    # that prior in the blended vote (was [1.0, 0.8, 1.2], then [0.6, 0.4, 2.0]).
    # RAISED 2026-07-22: nlr/increase dominance had been diluted by later data
    # changes (see model-loss-ratio-fixes memory) -- giving HGB even more vote
    # share (66.7% -> 75% of the blend) pushes the monotonic constraint harder.
    return VotingClassifier(
        [("rf", rf), ("et", et), ("hgb", hgb)],
        voting="soft", weights=[0.6, 0.4, 3.0],
    )


def build_pipeline(category_map: dict, n_estimators: int = 600) -> Pipeline:
    # KEPT isotonic (re-confirmed 2026-08-04). A leak-free walk-forward A/B in
    # isolation (calibration only, no override curves) showed dropping calibration
    # winning on ECE/resolution - but that test didn't include the NLR/renewal-
    # increase override curves that production actually applies on top. Once tested
    # end-to-end with the real train_and_save() (overrides included), isotonic +
    # overrides beat no-calibration + overrides on every headline metric: pooled
    # walk-forward AUC 0.807 vs 0.797, accuracy 75.0% vs 70.6%, Brier 0.189 vs 0.193.
    # Lesson: calibration and the override curves interact - whatever isotonic does
    # to the probability distribution apparently sets the overrides up to work
    # better, not worse. Don't re-test calibration in isolation again; any future
    # attempt must go through the full train_and_save() path with overrides included
    # before drawing a conclusion. See scratchpad_seed_check.py (isolated, misleading)
    # vs scratchpad_override_check.py + retrain_result.txt (full pipeline, decisive).
    prep = _preprocessor(category_map)
    ens = _build_ensemble(category_map, n_estimators)
    return Pipeline([("prep", prep), ("clf", CalibratedClassifierCV(ens, method="isotonic", cv=3))])


def _fast_pipeline(category_map: dict) -> Pipeline:
    prep = _preprocessor(category_map)
    monotonic_cst = _monotonic_cst(category_map)
    return Pipeline([
        ("prep", prep),
        ("clf", HistGradientBoostingClassifier(
            learning_rate=0.06, max_iter=250, max_leaf_nodes=15,
            l2_regularization=1.0, monotonic_cst=monotonic_cst, random_state=13)),
    ])


def _calibration_points(y, p, bins=8):
    edges = np.linspace(0, 1, bins + 1)
    pts = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi if hi < 1 else p <= hi)
        if m.sum() >= 8:
            pts.append({"predicted": round(float(p[m].mean()), 4),
                        "actual": round(float(y[m].mean()), 4),
                        "count": int(m.sum())})
    return pts


def _roc_points(y, p, max_points=60):
    fpr, tpr, _ = roc_curve(y, p)
    idx = np.linspace(0, len(fpr) - 1, min(max_points, len(fpr))).astype(int)
    return {"fpr": [round(float(v), 4) for v in fpr[idx]],
            "tpr": [round(float(v), 4) for v in tpr[idx]]}


# Walk-forward eligibility: a fold must have >=350 prior decisions (so the frozen
# feature/category width is fully present — the production monotonic_cst assumes it)
# and >=6 decisions to score. Matches walk_forward.py / experiment.py exactly, so the
# pooled numbers surfaced on the Model Performance page ARE the leak-free backtest.
WF_MIN_TRAIN, WF_MIN_TEST = 350, 6


def walk_forward(df: pd.DataFrame, y_all: pd.Series, category_map: dict):
    """Real-data walk-forward: for each month M, train the PRODUCTION ensemble
    (recency-weighted, isotonic-calibrated) on all decisions before M, then score
    the decisions made in M. Returns per-month entries plus the pooled out-of-sample
    (y, p) arrays — no renewal is ever scored by a model that trained on it, so the
    pooled metrics are the honest number to trust (vs. the noisier single holdout)."""
    dm = pd.to_datetime(df["eff_date"]).dt.to_period("M")
    mono_len = len(_monotonic_cst(category_map))
    out = []
    pooled_y, pooled_p = [], []
    for m in sorted(dm.unique()):
        tr, te = (dm < m).values, (dm == m).values
        if tr.sum() < WF_MIN_TRAIN or te.sum() < WF_MIN_TEST:
            continue
        # skip folds where the feature space isn't yet at full production width
        if _preprocessor(category_map).fit_transform(
                df.loc[tr, FEATURES], y_all[tr]).shape[1] != mono_len:
            continue
        pipe = build_pipeline(category_map)
        w = recency_weights(df.loc[tr, "eff_date"])
        pipe.fit(df.loc[tr, FEATURES], y_all[tr], clf__sample_weight=w)
        p = pipe.predict_proba(df.loc[te, FEATURES])[:, 1]
        # Same loss-ratio + renewal-increase overrides served in production (see
        # apply_override / apply_increase_override) — fit only on this fold's prior
        # months, so the pooled walk-forward metrics honestly reflect what a user
        # would actually have been shown.
        curve = fit_nlr_curve(df.loc[tr, "nlr"], y_all[tr], w)
        p = apply_override(p, df.loc[te, "nlr"], curve)
        inc_curve = fit_increase_curve(_effective_increase(df.loc[tr]), y_all[tr], w)
        p = apply_increase_override(p, _effective_increase(df.loc[te]), inc_curve)
        yt = y_all[te]
        pooled_y.extend(yt.tolist()); pooled_p.extend(p.tolist())
        try:
            auc = round(float(roc_auc_score(yt, p)), 4)
        except ValueError:
            auc = None  # single-class month
        out.append({
            "version": f"v{m}", "trained_at": f"{m}-28T18:00:00",
            "auc": auc,
            "accuracy": round(float(accuracy_score(yt, (p >= .5).astype(int))), 4),
            "brier": round(float(brier_score_loss(yt, p)), 4),
            "n_train": int(tr.sum()), "n_test": int(te.sum()),
            "kind": "real_monthly",
        })
    return out, np.array(pooled_y), np.array(pooled_p)


def train_and_save(verbose: bool = True) -> dict:
    df = pd.read_csv(HISTORY, parse_dates=["eff_date"])
    # Forward-book holdout: the months in FORWARD_BOOK_MONTHS are decided, but we
    # keep them OUT of training so scoring them on the Upcoming Renewals page is a
    # genuine, leak-free forward test (their rows stay in real_history.csv so the
    # book still has their underwriting features). See app/forward_book.py.
    _fwd = forward_mask(df["eff_date"])
    if _fwd.any():
        print(f"forward-book holdout: excluding {int(_fwd.sum())} decided renewals "
              f"in {sorted(FORWARD_BOOK_MONTHS)} from training")
        df = df[~_fwd]
    df = prepare(df).sort_values("eff_date").reset_index(drop=True)
    # Freeze the category vocabulary against the FULL dataset (not just the train
    # split) so one-hot width is identical across the holdout fit, CV folds,
    # walk-forward slices, and the full-data production refit - monotonic_cst
    # requires a stable feature count at every one of those fits.
    category_map = freeze_categories(df)
    y = df["renewed"].astype(int)

    cut = int(len(df) * 0.75)
    X_tr, y_tr = df[FEATURES].iloc[:cut], y.iloc[:cut]
    X_te, y_te = df[FEATURES].iloc[cut:], y.iloc[cut:]

    t0 = time.time()
    pipe = build_pipeline(category_map)
    train_weights = recency_weights(df["eff_date"].iloc[:cut])
    pipe.fit(X_tr, y_tr, clf__sample_weight=train_weights)
    p = pipe.predict_proba(X_te)[:, 1]
    # Loss-ratio + renewal-increase overrides (see definitions above the
    # recency-weighting block): fit only on the train slice, applied to the test
    # slice — leak-free, and the holdout metrics below now reflect what's actually
    # served, not the raw unstable-in-the-tail ensemble.
    holdout_curve = fit_nlr_curve(df["nlr"].iloc[:cut], y_tr, train_weights)
    p = apply_override(p, df["nlr"].iloc[cut:], holdout_curve)
    holdout_inc_curve = fit_increase_curve(_effective_increase(df).iloc[:cut], y_tr, train_weights)
    p = apply_increase_override(p, _effective_increase(df).iloc[cut:], holdout_inc_curve)

    # Tune the decision threshold on out-of-fold TRAIN predictions (leakage-safe).
    # The calibrated, class-balanced ensemble skews probabilities high, so a flat
    # 0.5 cutoff over-predicts renewals on the recent term-heavy book. Pick the
    # cutoff that maximizes balanced accuracy, then report binary metrics at it.
    oof = cross_val_predict(
        build_pipeline(category_map), X_tr, y_tr,
        cv=StratifiedKFold(5, shuffle=True, random_state=5),
        method="predict_proba", n_jobs=-1,
    )[:, 1]
    # Cap the search to a sensible operating band (~base rate). Without this the
    # tuner can run off to an extreme like 0.80 that maximizes balanced accuracy on
    # the training folds but tanks recall on the regime-shifted test set.
    grid = np.linspace(0.40, 0.65, 26)
    threshold = float(max(
        grid, key=lambda t: balanced_accuracy_score(y_tr, (oof >= t).astype(int))
    ))
    pred = (p >= threshold).astype(int)

    cv = cross_val_score(_fast_pipeline(category_map), X_tr, y_tr,
                         cv=StratifiedKFold(5, shuffle=True, random_state=5),
                         scoring="roc_auc")

    metrics = {
        "auc": round(float(roc_auc_score(y_te, p)), 4),
        "accuracy": round(float(accuracy_score(y_te, pred)), 4),
        "balanced_accuracy": round(float(balanced_accuracy_score(y_te, pred)), 4),
        "decision_threshold": round(threshold, 3),
        "precision": round(float(precision_score(y_te, pred)), 4),
        "recall": round(float(recall_score(y_te, pred)), 4),
        "f1": round(float(f1_score(y_te, pred)), 4),
        "brier": round(float(brier_score_loss(y_te, p)), 4),
        "cv_auc_mean": round(float(cv.mean()), 4),
        "cv_auc_std": round(float(cv.std()), 4),
        "n_train": int(len(X_tr)), "n_test": int(len(X_te)),
        "base_renewal_rate": round(float(y.mean()), 4),
        "test_renewal_rate": round(float(y_te.mean()), 4),
        # honest baseline: accuracy of always guessing the majority class ON THE TEST SET
        "naive_baseline": round(float(max(y_te.mean(), 1 - y_te.mean())), 4),
        "test_window_start": str(df["eff_date"].iloc[cut].date()),
        "test_window_end": str(df["eff_date"].iloc[-1].date()),
    }
    rich = df["nlr"].notna().iloc[cut:].values
    if rich.sum() >= 30:
        metrics["auc_rich_subset"] = round(float(roc_auc_score(y_te[rich], p[rich])), 4)
        metrics["n_rich_subset"] = int(rich.sum())

    calibration = _calibration_points(y_te.values, p)
    roc = _roc_points(y_te.values, p)

    imp = permutation_importance(pipe, X_te, y_te, scoring="roc_auc",
                                 n_repeats=8, random_state=3, n_jobs=1)
    importances = sorted(
        ({"feature": f, "label": LABELS.get(f, f),
          "importance": round(float(v), 5), "std": round(float(s), 5)}
         for f, v, s in zip(FEATURES, imp.importances_mean, imp.importances_std)),
        key=lambda r: -r["importance"],
    )

    backfill, wf_y, wf_p = walk_forward(df, y, category_map)
    # Pooled leak-free walk-forward = the honest scorecard the UI headlines (the single
    # 75/25 holdout above is noisier and understates the model). Same protocol as
    # walk_forward.py / experiment.py.
    wf_metrics = None
    if len(wf_y) >= 30:
        wf_pred = (wf_p >= threshold).astype(int)
        wf_metrics = {
            "auc": round(float(roc_auc_score(wf_y, wf_p)), 4),
            "accuracy": round(float(accuracy_score(wf_y, wf_pred)), 4),
            "balanced_accuracy": round(float(balanced_accuracy_score(wf_y, wf_pred)), 4),
            "brier": round(float(brier_score_loss(wf_y, wf_p)), 4),
            "n": int(len(wf_y)),
            "n_months": len(backfill),
            "renewal_rate": round(float(wf_y.mean()), 4),
        }
    wf_calibration = _calibration_points(wf_y, wf_p) if len(wf_y) >= 30 else []
    wf_roc = _roc_points(wf_y, wf_p) if len(wf_y) >= 30 else {"fpr": [], "tpr": []}

    full_weights = recency_weights(df["eff_date"])
    pipe.fit(df[FEATURES], y,  # production refit on everything, recency-weighted
             clf__sample_weight=full_weights)
    # Production loss-ratio + renewal-increase override curves, fit on the full
    # dataset — this is what score_active()/build_book.py apply at serve time (see
    # apply_override / apply_increase_override).
    nlr_curve = fit_nlr_curve(df["nlr"], y, full_weights)
    increase_curve = fit_increase_curve(_effective_increase(df), y, full_weights)
    version = datetime.now().strftime("real-v%Y.%m.%d-%H%M%S")
    MODEL_OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pipeline": pipe, "features": FEATURES, "version": version,
        "threshold": threshold, "category_map": category_map,
        "metrics": metrics, "importances": importances,
        "calibration": calibration, "roc": roc, "backfill": backfill,
        "wf_metrics": wf_metrics, "wf_calibration": wf_calibration, "wf_roc": wf_roc,
        "nlr_curve": nlr_curve, "increase_curve": increase_curve,
        "trained_at": datetime.now().isoformat(timespec="seconds"),
    }
    joblib.dump(payload, MODEL_OUT, compress=3)
    append_registry({"version": version, "trained_at": payload["trained_at"],
                     "kind": "real", **metrics})

    if verbose:
        print(f"trained in {time.time() - t0:.0f}s")
        print("\n=== REAL DATA - production ensemble, out-of-time holdout ===")
        for k, v in metrics.items():
            print(f"  {k:<22} {v}")
        print("\nreal walk-forward (monthly retrain simulation):")
        for e in backfill:
            auc_s = f"{e['auc']:.3f}" if e["auc"] is not None else "  n/a"
            print(f"  {e['version']}: AUC {auc_s} | acc {e['accuracy']:.3f} "
                  f"| n_test {e['n_test']}")
        if wf_metrics:
            print(f"\npooled leak-free walk-forward (n={wf_metrics['n']} over "
                  f"{wf_metrics['n_months']} months): AUC {wf_metrics['auc']:.3f} | "
                  f"acc {wf_metrics['accuracy']:.3f} | Brier {wf_metrics['brier']:.3f}")
        print("\npermutation importance (top 12):")
        for r in importances[:12]:
            print(f"  {r['label']:<30} {r['importance']:+.4f}")
        print(f"\nsaved {version} -> {MODEL_OUT.name}")
    return payload


def score_active(payload: dict, verbose: bool = True) -> pd.DataFrame | None:
    if not ACTIVE.exists():
        return None
    book = pd.read_csv(ACTIVE, parse_dates=["eff_date"])
    book = book.rename(columns={
        "n_renewals": "tenure_years",
        "firm_increase_pct": "initial_uw_increase_pct",
        "firm_increase_w_lasers_pct": "total_increase_pct",
        "laser_count_detail": "lasers_renewal",
    })
    scored = prepare(book, payload.get("category_map"))
    raw_p = payload["pipeline"].predict_proba(scored[FEATURES])[:, 1]
    p = apply_override(raw_p, scored["nlr"], payload["nlr_curve"])
    p = apply_increase_override(p, _effective_increase(scored), payload["increase_curve"])
    scored["renewal_probability"] = np.round(p, 4)
    scored = scored.sort_values("renewal_probability")
    scored.to_csv(SCORED_OUT, index=False)
    if verbose:
        cols = ["group_name", "eff_date", "broker", "lives", "nlr",
                "total_increase_pct", "tenure_years", "renewal_probability"]
        print(f"\n=== REAL ACTIVE BOOK SCORED ({len(scored)}) -> {SCORED_OUT.name} ===")
        with pd.option_context("display.width", 200, "display.max_colwidth", 30):
            print(scored[cols].to_string(index=False))
    return scored


if __name__ == "__main__":
    payload = train_and_save()
    score_active(payload)
