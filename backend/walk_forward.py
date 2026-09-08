"""
Leak-free walk-forward backtest of the REAL renewal model.

For each renewal month M (with enough prior history), train the production ensemble on
ONLY the decisions effective before M, then score the decisions effective in M. Pool
every out-of-sample prediction and report an honest scorecard: AUC, accuracy vs the
naive majority-class baseline, Brier, and a calibration curve. No renewal is ever scored
by a model that trained on it — this is the number to trust.
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

from sklearn.metrics import (accuracy_score, balanced_accuracy_score,  # noqa: E402
                             brier_score_loss, roc_auc_score)

from train_real import (FEATURES, HISTORY, _monotonic_cst, _preprocessor,  # noqa: E402
                        build_pipeline, freeze_categories, prepare)

# 350+ prior decisions => the fold reaches the full, stable feature width (the pre-2026
# folds have too little data for every feature/category to appear, which the production
# monotonic-constraint width assumes). This lands us on the 2026 test months — exactly
# the current book's cycle.
MIN_TRAIN = 350
MIN_TEST = 6


def main() -> None:
    df = pd.read_csv(HISTORY, parse_dates=["eff_date"])
    df = prepare(df).sort_values("eff_date").reset_index(drop=True)
    df = df[df["renewed"].notna()].copy()
    cat = freeze_categories(df)
    y = df["renewed"].astype(int)
    dm = df["eff_date"].dt.to_period("M")

    mono_len = len(_monotonic_cst(cat))
    pooled_p, pooled_y, pooled_m = [], [], []
    per_month = []
    months = sorted(dm.unique())
    print(f"history: {len(df)} decisions, {df['eff_date'].min().date()} .. "
          f"{df['eff_date'].max().date()}\nrunning walk-forward folds...\n")
    for m in months:
        tr, te = (dm < m).values, (dm == m).values
        if tr.sum() < MIN_TRAIN or te.sum() < MIN_TEST:
            continue
        # guard: only score folds where the feature space is fully present (matches the
        # production monotonic-constraint width) — otherwise the ensemble can't fit.
        if _preprocessor(cat).fit_transform(df.loc[tr, FEATURES],
                                            y[tr]).shape[1] != mono_len:
            print(f"  {m}: skipped (feature width not yet stable)")
            continue
        pipe = build_pipeline(cat)
        pipe.fit(df.loc[tr, FEATURES], y[tr])
        p = pipe.predict_proba(df.loc[te, FEATURES])[:, 1]
        yt = y[te].values
        pooled_p.extend(p); pooled_y.extend(yt); pooled_m.extend([str(m)] * len(yt))
        try:
            auc = round(float(roc_auc_score(yt, p)), 3)
        except ValueError:
            auc = None  # single-class month
        per_month.append({
            "month": str(m), "n": int(te.sum()),
            "auc": auc,
            "acc@.5": round(float(accuracy_score(yt, (p >= .5).astype(int))), 2),
            "brier": round(float(brier_score_loss(yt, p)), 3),
            "renew_rate": round(float(yt.mean()), 2),
        })
        print(f"  {m}: trained on {int(tr.sum()):3} prior, tested {int(te.sum()):2} "
              f"-> AUC {auc}  acc {per_month[-1]['acc@.5']}  renew_rate {per_month[-1]['renew_rate']}")

    P = np.array(pooled_p); Y = np.array(pooled_y)
    n = len(Y)
    if n == 0:
        print("no eligible folds")
        return
    acc = accuracy_score(Y, (P >= .5).astype(int))
    bal = balanced_accuracy_score(Y, (P >= .5).astype(int))
    auc = roc_auc_score(Y, P)
    brier = brier_score_loss(Y, P)
    base = max(Y.mean(), 1 - Y.mean())

    print(f"\n{'='*64}\nLEAK-FREE WALK-FORWARD SCORECARD  (pooled out-of-sample, n={n})\n{'='*64}")
    print(f"  AUC ....................... {auc:.3f}   (0.5 = coin flip)")
    print(f"  accuracy @0.5 ............. {acc:.1%}")
    print(f"  balanced accuracy ........ {bal:.1%}")
    print(f"  naive baseline (majority)  {base:.1%}   <-- model must beat this")
    print(f"  Brier ..................... {brier:.3f}   (lower better; 0.25 = coin flip)")
    print(f"  actual renewal rate ...... {Y.mean():.1%}")
    verdict = ("BEATS baseline" if acc > base + 0.02 else
               "MATCHES baseline" if acc >= base - 0.02 else "LOSES to baseline")
    print(f"  verdict ................... {verdict}")

    # calibration: are predicted probabilities honest?
    print(f"\n  calibration (predicted band -> actual renewal rate):")
    edges = np.linspace(0, 1, 6)
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (P >= lo) & (P < hi if hi < 1 else P <= hi)
        if mask.sum():
            print(f"    {lo:.1f}-{hi:.1f}: predicted~{P[mask].mean():.2f}  "
                  f"actual {Y[mask].mean():.2f}  (n={int(mask.sum())})")

    # focus: 2026 only
    m26 = np.array([mm.startswith("2026") for mm in pooled_m])
    if m26.sum():
        a26 = accuracy_score(Y[m26], (P[m26] >= .5).astype(int))
        try:
            auc26 = roc_auc_score(Y[m26], P[m26])
        except ValueError:
            auc26 = float("nan")
        b26 = max(Y[m26].mean(), 1 - Y[m26].mean())
        print(f"\n  2026 renewals only (n={int(m26.sum())}): "
              f"AUC {auc26:.3f}  acc {a26:.1%}  vs baseline {b26:.1%}")


if __name__ == "__main__":
    main()
