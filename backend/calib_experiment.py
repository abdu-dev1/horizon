"""
Calibration experiment (leak-free walk-forward): can we make the probabilities more
trustworthy than the current recency + isotonic(cv=3)?

Candidates (all recency-weighted, 9mo half-life):
  A current   : isotonic calibration on 3 random CV folds of the training data
  B time-calib: fit ensemble on older 75% of train, fit isotonic on the RECENT 25%
                (aligns probabilities to the recent, term-heavier regime)
  C raw       : no calibration (how much is calibration even doing?)

Judged on Brier + ECE (probability quality) first, then accuracy/AUC.
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

from experiment import _ece, make_pipeline  # noqa: E402  (reuse ensemble builder)
from train_real import (FEATURES, HISTORY, _monotonic_cst, _preprocessor,  # noqa: E402
                        freeze_categories, prepare, recency_weights)

MIN_TRAIN, MIN_TEST = 350, 6


def _fit_predict(kind, cat, Xtr, ytr, wtr, dtr, Xte):
    if kind in ("current", "raw"):
        pipe = make_pipeline(cat, calib="isotonic" if kind == "current" else "none")
        pipe.fit(Xtr, ytr, clf__sample_weight=wtr)
        return pipe.predict_proba(Xte)[:, 1]
    # time-calib: ensemble on older 75% by date, isotonic on recent 25% (prefit)
    order = np.argsort(dtr.values)
    cut = int(len(order) * 0.75)
    old, rec = order[:cut], order[cut:]
    base = make_pipeline(cat, calib="none")
    base.fit(Xtr.iloc[old], ytr.iloc[old], clf__sample_weight=wtr[old])
    cal = CalibratedClassifierCV(base, method="isotonic", cv="prefit")
    cal.fit(Xtr.iloc[rec], ytr.iloc[rec])
    return cal.predict_proba(Xte)[:, 1]


def evaluate(kind, cat, df, y, dm):
    mono = len(_monotonic_cst(cat))
    P, Y = [], []
    for m in sorted(dm.unique()):
        tr, te = (dm < m).values, (dm == m).values
        if tr.sum() < MIN_TRAIN or te.sum() < MIN_TEST:
            continue
        if _preprocessor(cat).fit_transform(df.loc[tr, FEATURES], y[tr]).shape[1] != mono:
            continue
        Xtr, ytr = df.loc[tr, FEATURES], y[tr]
        w = recency_weights(df.loc[tr, "eff_date"])
        p = _fit_predict(kind, cat, Xtr, ytr, w, df.loc[tr, "eff_date"], df.loc[te, FEATURES])
        P.extend(p); Y.extend(y[te].values)
    P, Y = np.array(P), np.array(Y)
    return dict(n=len(Y), AUC=round(roc_auc_score(Y, P), 3),
                acc=round(accuracy_score(Y, (P >= .5).astype(int)), 3),
                bal=round(balanced_accuracy_score(Y, (P >= .5).astype(int)), 3),
                Brier=round(brier_score_loss(Y, P), 3), ECE=round(_ece(Y, P), 3))


def main():
    df = pd.read_csv(HISTORY, parse_dates=["eff_date"])
    df = prepare(df).sort_values("eff_date").reset_index(drop=True)
    df = df[df["renewed"].notna()].copy()
    cat = freeze_categories(df); y = df["renewed"].astype(int)
    dm = df["eff_date"].dt.to_period("M")
    print(f"{'config':14} {'n':>4} {'AUC':>6} {'acc':>6} {'bal':>6} {'Brier':>7} {'ECE':>6}")
    print("-" * 52)
    for kind in ("current", "time-calib", "raw"):
        r = evaluate(kind, cat, df, y, dm)
        print(f"{kind:14} {r['n']:>4} {r['AUC']:>6} {r['acc']:>6} {r['bal']:>6} {r['Brier']:>7} {r['ECE']:>6}")


if __name__ == "__main__":
    main()
