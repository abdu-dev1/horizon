"""
Controlled, leak-free experiments to improve the real renewal model.

Every candidate is scored by the SAME walk-forward protocol (train only on decisions
before month M, score M, pool 2026). We compare against the current production config
and keep only what measurably lifts AUC / balanced accuracy / calibration (Brier + ECE).
Nothing here is accepted on vibes.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

from sklearn.calibration import CalibratedClassifierCV  # noqa: E402
from sklearn.metrics import (accuracy_score, balanced_accuracy_score,  # noqa: E402
                             brier_score_loss, roc_auc_score)
from sklearn.pipeline import Pipeline  # noqa: E402

from train_real import (FEATURES, HISTORY, _build_ensemble, _monotonic_cst,  # noqa: E402
                        _preprocessor, freeze_categories, prepare)

MIN_TRAIN, MIN_TEST = 350, 6


def make_pipeline(cat: dict, calib: str = "isotonic") -> Pipeline:
    """calib='isotonic' matches production (build_pipeline() in train_real.py) as
    re-confirmed 2026-08-04 - see that function's docstring: an isolated A/B favored
    dropping calibration, but a full end-to-end test (with the NLR/increase override
    curves included) showed isotonic + overrides winning on every headline metric.
    Always builds the ensemble via _build_ensemble() - the shared definition
    train_real.py itself uses - so this can never silently drift from production
    weights the way it previously did (was hard-coded [1.0, 0.8, 1.2] vs production's
    actual [0.6, 0.4, 3.0], which made an earlier A/B run here misleading until
    caught). NOTE: like train_real.py's own walk_forward(), any calibration
    comparison here still omits the override curves - don't draw production
    conclusions from this script alone; validate through train_and_save()."""
    prep = _preprocessor(cat)
    ens = _build_ensemble(cat)
    if calib == "none":
        clf = ens
    elif calib == "isotonic_noensemble":
        # TESTED 2026-08-10, REJECTED. ensemble=False: ONE isotonic calibrator fit
        # on pooled cross_val_predict OOF probabilities (all rows), vs. production's
        # ensemble=True default (3 separate calibrators, each fit on only its own
        # ~1/3 fold of rows). Motivation: production's per-fold calibrators have a
        # big knot gap at high raw scores (~0.15-0.95 on ~600 rows), so many
        # genuinely-different high-confidence accounts all get clipped to the same
        # calibrated value (e.g. 12 different groups all landing on exactly 0.7031 -
        # see the permutation-importance-chart conversation). ensemble=False pools
        # more data into one calibrator and should smooth that out. It does - but at
        # a real accuracy cost: AUC 0.782->0.73, acc 0.69->0.606, bal_acc 0.717->0.656,
        # Brier 0.202->0.235, ECE 0.121->0.175 (isolated, no override curves, recency
        # hl=9mo). Every metric got worse, not just cosmetically. Conclusion: the
        # plateau is the cost of what's currently the best-performing calibration
        # setup on this ~600-row book, not a free bug - keep ensemble=True.
        clf = CalibratedClassifierCV(ens, method="isotonic", cv=3, ensemble=False)
    elif calib == "isotonic_cv2":
        # TESTED 2026-08-10, REJECTED. Same motivation as isotonic_noensemble (fewer
        # folds -> each calibrator sees ~1/2 the data instead of ~1/3, denser knots
        # at the extremes) at the cost of each fold's base estimator training on
        # less data (1/2 instead of 2/3). Also worse: AUC 0.782->0.76, acc 0.69->0.662,
        # bal_acc 0.717->0.69, Brier 0.202->0.228, ECE 0.121->0.129.
        clf = CalibratedClassifierCV(ens, method="isotonic", cv=2)
    else:
        clf = CalibratedClassifierCV(ens, method=calib, cv=3)
    return Pipeline([("prep", prep), ("clf", clf)])


def _ece(y, p, bins=10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    e = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi if hi < 1 else p <= hi)
        if m.sum():
            e += (m.sum() / len(y)) * abs(p[m].mean() - y[m].mean())
    return e


def evaluate(cat, df, y, dm, calib="isotonic", halflife=None) -> dict:
    """Walk-forward over 2026; returns pooled metrics. halflife (months) enables
    recency weighting: w = 0.5 ** (age_months / halflife)."""
    mono = len(_monotonic_cst(cat))
    P, Y = [], []
    for m in sorted(dm.unique()):
        tr, te = (dm < m).values, (dm == m).values
        if tr.sum() < MIN_TRAIN or te.sum() < MIN_TEST:
            continue
        if _preprocessor(cat).fit_transform(df.loc[tr, FEATURES], y[tr]).shape[1] != mono:
            continue
        pipe = make_pipeline(cat, calib)
        fit_kw = {}
        if halflife:
            age = (m - dm[tr]).apply(lambda x: x.n).values  # months before test month
            w = 0.5 ** (age / halflife)
            fit_kw["clf__sample_weight"] = w
        pipe.fit(df.loc[tr, FEATURES], y[tr], **fit_kw)
        P.extend(pipe.predict_proba(df.loc[te, FEATURES])[:, 1]); Y.extend(y[te].values)
    P, Y = np.array(P), np.array(Y)
    return {
        "n": len(Y),
        "AUC": round(roc_auc_score(Y, P), 3),
        "acc": round(accuracy_score(Y, (P >= .5).astype(int)), 3),
        "bal_acc": round(balanced_accuracy_score(Y, (P >= .5).astype(int)), 3),
        "Brier": round(brier_score_loss(Y, P), 3),
        "ECE": round(_ece(Y, P), 3),
    }


def main() -> None:
    df = pd.read_csv(HISTORY, parse_dates=["eff_date"])
    df = prepare(df).sort_values("eff_date").reset_index(drop=True)
    df = df[df["renewed"].notna()].copy()
    cat = freeze_categories(df)
    y = df["renewed"].astype(int)
    dm = df["eff_date"].dt.to_period("M")

    configs = [
        ("production (isotonic, recency hl=9mo)", dict(calib="isotonic", halflife=9)),
        ("no calibration + recency hl=9mo",       dict(calib="none",     halflife=9)),
        ("sigmoid  + recency hl=9mo",             dict(calib="sigmoid",  halflife=9)),
        ("isotonic ensemble=False + recency hl=9mo", dict(calib="isotonic_noensemble", halflife=9)),
        ("isotonic cv=2 + recency hl=9mo",        dict(calib="isotonic_cv2", halflife=9)),
        ("isotonic, NO recency (sanity check)",   dict(calib="isotonic", halflife=None)),
    ]
    print(f"{'config':34} {'n':>4} {'AUC':>6} {'acc':>6} {'bal':>6} {'Brier':>7} {'ECE':>6}")
    print("-" * 74)
    for name, kw in configs:
        r = evaluate(cat, df, y, dm, **kw)
        print(f"{name:34} {r['n']:>4} {r['AUC']:>6} {r['acc']:>6} {r['bal_acc']:>6} "
              f"{r['Brier']:>7} {r['ECE']:>6}")


if __name__ == "__main__":
    main()
