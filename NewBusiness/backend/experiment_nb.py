"""
NewBusiness model experiments — the honest evaluation harness.

Same role as the renewal project's experiment.py / walk_forward.py: this file,
not train_nb.py's printout, is where a modelling change has to prove itself
before it ships.

Why it exists
-------------
train_nb.py's 5-fold CV grades the model on DECIDED quotes, where the
quote-progression fields (illustrative/firm cost, ISL deductible, UW dates,
underwriter) are populated. The OPEN quotes it actually has to score do not
have them -- measured on the 2026-08-24 export:

    isl_deductible                67.4% decided ->  0.0% open
    has_illustrative_quote        74.9% decided ->  4.4% open
    days_created_to_illustrative  61.1% decided ->  0.0% open
    underwriter                   41.9% decided ->  0.0% open
    firm_* / days_illustrative_*  13-21% decided ->  0.0% open

So the headline number was measured under conditions that never hold at
scoring time. Three evaluations are run here to separate that out:

  A  train rich / test rich   -- the old number. Optimistic.
  B  train rich / test masked -- fit on all features, but the test fold has
                                 the serve-absent ones blanked to exactly what
                                 an open quote looks like. This is what the
                                 shipped model was really doing.
  C  train lean / test lean   -- both sides restricted to features that exist
                                 at scoring time. The proposal.

B vs C is the decisive comparison, and it is a fair one: identical rows,
identical folds, identical learner, and in both cases the model is asked the
question it's actually asked in production.

Run:  python experiment_nb.py   (from NewBusiness/backend/)
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

from nb_encoders import SmoothedTargetEncoder  # noqa: E402

DATA = BACKEND_DIR / "data"

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------- feature sets

# The pre-existing (shipped 2026-08-24) feature set.
RICH_NUM = [
    "lives", "current_max_cost", "illustrative_max_cost", "pct_vs_current",
    "current_renewal", "firm_max_cost",
    "isl_deductible", "laser_liability", "laser_count",
    "has_illustrative_quote", "has_firm_quote", "days_created_to_eff",
    "days_created_to_illustrative", "days_illustrative_to_firm",
    "days_illustrative_to_uw_complete", "days_uw_complete_to_firm_sent",
    "current_cost_per_life", "illustrative_cost_per_life", "firm_cost_per_life",
]
RICH_OH = ["product", "eff_month_num"]
RICH_TE = ["broker", "rsd", "underwriter", "industry", "billing_state"]

# Features whose value at scoring time is fixed and uninformative. The value
# each one collapses to on an open quote (measured, not assumed) is what
# eval B masks the test fold to.
SERVE_ABSENT = {
    "illustrative_max_cost": np.nan, "pct_vs_current": np.nan,
    "firm_max_cost": np.nan, "isl_deductible": np.nan,
    "laser_liability": np.nan, "laser_count": 0.0,
    "has_illustrative_quote": 0.0, "has_firm_quote": 0.0,
    "days_created_to_illustrative": np.nan, "days_illustrative_to_firm": np.nan,
    "days_illustrative_to_uw_complete": np.nan, "days_uw_complete_to_firm_sent": np.nan,
    "illustrative_cost_per_life": np.nan, "firm_cost_per_life": np.nan,
    "underwriter": np.nan,
}

LEAN_NUM = ["lives", "current_max_cost", "current_renewal",
            "current_cost_per_life", "days_created_to_eff"]
LEAN_OH = ["product", "eff_month_num"]
LEAN_TE = ["broker", "rsd", "industry", "billing_state"]
GEO_TE = ["billing_zip3", "billing_city"]


def prep(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["eff_month_num"] = pd.to_datetime(df["eff_date"], errors="coerce").dt.month.astype("Int64").astype(str)
    # Incumbent's own renewal vs. what they pay today: the size-independent
    # read on how much pain the prospect is already in, which is the whole
    # reason a first-time buyer shops. Both inputs are partly populated on
    # open quotes, so unlike pct_vs_current this is computable at scoring time.
    with np.errstate(all="ignore"):
        r = df["current_renewal"] / df["current_max_cost"] - 1
    df["renewal_increase"] = r.where((r > -0.95) & (r < 5.0))
    return df


def recency_weight(created: pd.Series, half_life_days: float, asof: pd.Timestamp) -> np.ndarray:
    """Exponential decay on quote age. The book's win rate is not stationary --
    11.1% (2022) -> 4.9% -> 5.2% -> 4.4% -> 2.2% (2026 to date) -- so an
    unweighted fit averages over regimes that no longer exist. Same mechanism
    as the renewal project's recency weighting."""
    age = (asof - pd.to_datetime(created, errors="coerce")).dt.days.clip(lower=0)
    age = age.fillna(age.median())
    return np.power(0.5, age / half_life_days).to_numpy()


def make_pipeline(num, oh, te, seed=13, smoothing=10.0) -> Pipeline:
    steps = []
    if num:
        steps.append(("num", Pipeline([("i", SimpleImputer(strategy="median"))]), num))
    if oh:
        steps.append(("oh", Pipeline([
            ("i", SimpleImputer(strategy="constant", fill_value="__missing__")),
            ("e", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
        ]), oh))
    if te:
        steps.append(("te", SmoothedTargetEncoder(columns=te, smoothing=smoothing), te))
    base = HistGradientBoostingClassifier(
        learning_rate=0.05, max_iter=300, max_leaf_nodes=15,
        l2_regularization=1.0, class_weight="balanced", random_state=seed)
    return Pipeline([("prep", ColumnTransformer(steps)), ("clf", CalibratedClassifierCV(base, method="sigmoid", cv=5))])


def cv_oof(df, y, num, oh, te, weights=None, mask=None, n_splits=5, seed=42, smoothing=10.0):
    """Manual stratified-CV out-of-fold probabilities.

    Hand-rolled rather than cross_val_predict because both sample_weight
    (needs per-fold subsetting; sklearn 1.3 has no metadata routing) and the
    test-fold masking that eval B depends on are impossible to express through
    it. Every fold's encoder/imputer/model still only ever sees training rows,
    so the out-of-fold scores stay honest.
    """
    cols = num + oh + te
    X = df[cols]
    oof = np.zeros(len(df))
    for tr, te_idx in StratifiedKFold(n_splits, shuffle=True, random_state=seed).split(X, y):
        pipe = make_pipeline(num, oh, te, smoothing=smoothing)
        fit_kw = {}
        if weights is not None:
            fit_kw["clf__sample_weight"] = weights[tr]
        pipe.fit(X.iloc[tr], y[tr], **fit_kw)
        X_test = X.iloc[te_idx]
        if mask:
            X_test = X_test.copy()
            for c, v in mask.items():
                if c in X_test.columns:
                    X_test[c] = v
        oof[te_idx] = pipe.predict_proba(X_test)[:, 1]
    return oof


def report(label, y, oof, recent_mask=None):
    line = (f"{label:<48} ROC {roc_auc_score(y, oof):.3f}   "
            f"PR {average_precision_score(y, oof):.3f}")
    if recent_mask is not None and recent_mask.sum() > 30 and y[recent_mask].sum() >= 5:
        line += (f"   | recent-only ROC {roc_auc_score(y[recent_mask], oof[recent_mask]):.3f} "
                 f"PR {average_precision_score(y[recent_mask], oof[recent_mask]):.3f}")
    print(line)


def main():
    h = prep(pd.read_csv(DATA / "nb_history.csv"))
    y = h["sold"].to_numpy()
    created = pd.to_datetime(h["created_date"], errors="coerce")
    asof = created.max()
    recent = (created >= asof - pd.Timedelta(days=548)).to_numpy()

    print(f"n={len(h)}  wins={y.sum()}  base={y.mean():.2%}  "
          f"created {created.min():%Y-%m} .. {created.max():%Y-%m}")
    print(f"recent-only slice (last 18mo): n={recent.sum()}, wins={y[recent].sum()}\n")

    print("=== Train/serve skew: is the old number reachable in production? ===")
    report("A  train rich / test rich   (the old number)",
           y, cv_oof(h, y, RICH_NUM, RICH_OH, RICH_TE), recent)
    report("B  train rich / test masked (what really happened)",
           y, cv_oof(h, y, RICH_NUM, RICH_OH, RICH_TE, mask=SERVE_ABSENT), recent)
    report("C  train lean / test lean   (serve-consistent)",
           y, cv_oof(h, y, LEAN_NUM, LEAN_OH, LEAN_TE), recent)

    print("\n=== Building on C: added features ===")
    report("C1 + renewal_increase",
           y, cv_oof(h, y, LEAN_NUM + ["renewal_increase"], LEAN_OH, LEAN_TE), recent)
    report("C2 + geo (zip3, city)",
           y, cv_oof(h, y, LEAN_NUM + ["renewal_increase"], LEAN_OH, LEAN_TE + GEO_TE), recent)

    print("\n=== Recency weighting (on the best feature set so far) ===")
    best_num, best_oh, best_te = LEAN_NUM + ["renewal_increase"], LEAN_OH, LEAN_TE + GEO_TE
    for hl in (365, 548, 730, 1095):
        w = recency_weight(h["created_date"], hl, asof)
        report(f"   half-life {hl}d ({hl/365:.1f}y)",
               y, cv_oof(h, y, best_num, best_oh, best_te, weights=w), recent)


if __name__ == "__main__":
    main()


# --------------------------------------------------------------- round 2
# Round 1 (2026-08-26) settled the big question -- serve-consistent (C) beats
# train-rich/serve-lean (B) by +0.069 ROC / +0.050 PR -- and killed two ideas
# outright: geo target-encoding (zip3/city) cost 0.066 ROC, and renewal_increase
# did nothing. Round 2 tunes what's left. Every config is averaged over two
# CV seeds, because round 1 produced differences (0.805 vs 0.801) well inside
# single-seed noise, and a decision made on that is a coin flip with extra steps.

def cv_oof_multi(df, y, num, oh, te, seeds=(42, 7), **kw):
    return [cv_oof(df, y, num, oh, te, seed=s, **kw) for s in seeds]


def report_multi(label, y, oofs, recent_mask):
    roc = np.mean([roc_auc_score(y, o) for o in oofs])
    pr = np.mean([average_precision_score(y, o) for o in oofs])
    rroc = np.mean([roc_auc_score(y[recent_mask], o[recent_mask]) for o in oofs])
    rpr = np.mean([average_precision_score(y[recent_mask], o[recent_mask]) for o in oofs])
    print(f"{label:<44} ROC {roc:.3f}  PR {pr:.3f}   | recent ROC {rroc:.3f} PR {rpr:.3f}", flush=True)
    return pr


def round2():
    h = prep(pd.read_csv(DATA / "nb_history.csv"))
    y = h["sold"].to_numpy()
    created = pd.to_datetime(h["created_date"], errors="coerce")
    asof = created.max()
    recent = (created >= asof - pd.Timedelta(days=548)).to_numpy()
    print(f"n={len(h)} wins={y.sum()} base={y.mean():.2%}  (2-seed averages)\n", flush=True)

    N, O, T = LEAN_NUM, LEAN_OH, LEAN_TE
    report_multi("C  lean baseline", y, cv_oof_multi(h, y, N, O, T), recent)
    report_multi("C  - billing_state", y, cv_oof_multi(h, y, N, O, ["broker", "rsd", "industry"]), recent)
    report_multi("C  - broker", y, cv_oof_multi(h, y, N, O, ["rsd", "industry", "billing_state"]), recent)
    report_multi("C  cats only (no numerics)", y, cv_oof_multi(h, y, [], O, T), recent)
    report_multi("C  + laser_count", y, cv_oof_multi(h, y, N + ["laser_count"], O, T), recent)
    report_multi("C  eff_month target-enc instead of 1-hot",
                 y, cv_oof_multi(h, y, N, ["product"], T + ["eff_month_num"]), recent)
    for hl in (730, 1095):
        w = recency_weight(h["created_date"], hl, asof)
        report_multi(f"C  + recency half-life {hl}d", y, cv_oof_multi(h, y, N, O, T, weights=w), recent)


def round3_smoothing():
    """Target-encoder smoothing. Round 1's geo result (high-cardinality columns
    actively HURTING) is a symptom of the encoder overfitting, so the smoothing
    constant is the first knob worth checking on the columns we do keep.

    Threaded through make_pipeline as a real parameter -- the first attempt
    monkeypatched SmoothedTargetEncoder.__init__, which broke sklearn's
    get_params/clone contract (a captured default showed up as an estimator
    parameter) and took the whole run down."""
    h = prep(pd.read_csv(DATA / "nb_history.csv"))
    y = h["sold"].to_numpy()
    created = pd.to_datetime(h["created_date"], errors="coerce")
    recent = (created >= created.max() - pd.Timedelta(days=548)).to_numpy()
    for sm in (5.0, 10.0, 25.0, 50.0):
        report_multi(f"C  TE smoothing={sm:g}" + ("  [current]" if sm == 10.0 else ""),
                     y, cv_oof_multi(h, y, LEAN_NUM, LEAN_OH, LEAN_TE, smoothing=sm), recent)


def tune_bands(n_splits: int = 5, seeds=(42, 7)):
    """Pick the BAND_CUTS thresholds from the shipped model's own out-of-fold
    score distribution, instead of leaving them at inherited round numbers.

    The old Hot/Warm/Long Shot/Cold cutoffs (0.20 / 0.08 / 0.03) produced two
    middle bands measuring 15.8% and 12.7% -- statistically the same band under
    two names, which is exactly the sort of false precision the band scheme is
    supposed to avoid. A cut only earns its place if it separates.

    Constraints, all of which must hold:
      * every band holds >= MIN_BAND_N quotes, so its rate is stateable at all
      * measured win rates are strictly decreasing across the four bands
      * each adjacent pair differs by >= SEPARATION x, so two bands never
        describe the same population
    Among the passing candidates, maximise the top band's measured win rate --
    that band is the one the sales team acts on, so its precision is what the
    scheme is for.

    Prints the winner and the runners-up rather than silently editing
    insights_nb.BAND_CUTS: the thresholds are a judgement the reader should see
    the alternatives for.
    """
    from itertools import combinations

    MIN_BAND_N = 100
    SEPARATION = 1.6

    h = prep(pd.read_csv(DATA / "nb_history.csv"))
    y = h["sold"].to_numpy()
    oofs = cv_oof_multi(h, y, LEAN_NUM, LEAN_OH, LEAN_TE, seeds=seeds, n_splits=n_splits)
    proba = np.mean(oofs, axis=0)
    print(f"pooled OOF over {len(seeds)} seeds — ROC {roc_auc_score(y, proba):.3f} "
          f"PR {average_precision_score(y, proba):.3f}")
    qs = np.unique(np.round(np.quantile(proba, np.arange(0.50, 0.996, 0.01)), 4))
    print(f"candidate thresholds: {len(qs)} (score quantiles p50..p99)\n")

    results = []
    for lo, mid, hi in combinations(qs, 3):
        edges = [hi, mid, lo]
        bands, ok = [], True
        prev_rate = 1.1
        for i, e in enumerate(edges + [0.0]):
            upper = edges[i - 1] if i else 1.1
            m = (proba >= e) & (proba < upper)
            n = int(m.sum())
            if n < MIN_BAND_N:
                ok = False
                break
            rate = float(y[m].mean())
            if rate <= 0 or rate >= prev_rate or prev_rate / max(rate, 1e-9) < SEPARATION:
                ok = False
                break
            bands.append((e, n, rate))
            prev_rate = rate
        if ok and len(bands) == 4:
            results.append((bands[0][2], edges, bands))

    if not results:
        print("no threshold triple satisfies the constraints — the score "
              "distribution does not support four distinguishable bands. "
              "Report three (drop a cut) rather than inventing a fourth.")
        return None

    results.sort(key=lambda r: -r[0])
    names = ["High", "Moderate", "Low", "Very Low"]
    for rank, (top_rate, edges, bands) in enumerate(results[:5]):
        tag = "  <-- BAND_CUTS" if rank == 0 else ""
        print(f"#{rank + 1}  cuts {[round(e, 4) for e in edges]}{tag}")
        for nm, (e, n, rate) in zip(names, bands):
            print(f"      {nm:<9} >= {e:<7.4f} n={n:<6} rate={rate:6.1%}")
    best = results[0][1]
    print(f"\nBAND_CUTS = [(\"High\", {best[0]:.4f}), (\"Moderate\", {best[1]:.4f}), "
          f"(\"Low\", {best[2]:.4f}), (\"Very Low\", 0.0)]")
    return best


def round4_laser_trap():
    """Round 2 found that adding `laser_count` to the lean set improves history
    CV (+0.008 ROC / +0.020 PR). It is a trap, and this is the check that
    proves it: laser_count varies on decided history (0-4, 6.4% non-zero) and
    is constant 0 on every open quote in the book. So the honest question is
    not "does it help on history" but "does it help when the test fold looks
    like an open quote" -- exactly the eval-B masking from round 1.

    If the masked run gives back the gain, the feature is real. If it doesn't,
    the gain was measured under conditions that never occur, and the feature
    stays out however good its CV number looks. Kept as a permanent regression
    test for that reasoning, not just a one-off check.
    """
    h = prep(pd.read_csv(DATA / "nb_history.csv"))
    y = h["sold"].to_numpy()
    created = pd.to_datetime(h["created_date"], errors="coerce")
    recent = (created >= created.max() - pd.Timedelta(days=548)).to_numpy()
    N2 = LEAN_NUM + ["laser_count"]
    report_multi("lean, no laser_count            [ship]",
                 y, cv_oof_multi(h, y, LEAN_NUM, LEAN_OH, LEAN_TE), recent)
    report_multi("lean + laser_count, test as-is  [history]",
                 y, cv_oof_multi(h, y, N2, LEAN_OH, LEAN_TE), recent)
    report_multi("lean + laser_count, masked to 0 [reality]",
                 y, cv_oof_multi(h, y, N2, LEAN_OH, LEAN_TE, mask={"laser_count": 0.0}), recent)
