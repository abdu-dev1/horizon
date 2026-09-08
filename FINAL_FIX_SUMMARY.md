# Loss Ratio Fix - Final Approach

## Problem (Diagnosis Complete)
- UT account with 300% loss ratio predicted as 70% likely to renew
- Model learning to ignore loss ratio in favor of tenure + broker signals  
- 24 false-positive renewals at 200%+ loss ratio
- Calibration error of +8.3% at 200-300% band

---

## Attempted Solutions & Learnings

### Attempt 1: Feature Engineering (❌ Failed)
- Added `nlr_catastrophic_2_5`, `nlr_catastrophic_3_0`, `nlr_tenure_product`, `short_tenure_high_loss`
- **Result:** Features had near-zero importance (ranked 22-28), ensemble ignored them
- **Lesson:** The ensemble is fundamentally learning that tenure overrides loss; adding more features won't change this

### Attempt 2: Ensemble Reweighting + Calibration Change (❌ Made Things Worse)
- Increased HGB weight from 1.2→2.0, changed calibration isotonic→sigmoid, lowered threshold 0.65→0.60
- **Result:** Calibration error increased to +18.9% (worse), false positives increased 24→69
- **Lesson:** Global parameter changes hurt other segments without fixing the core issue

---

## Final Solution: Post-Prediction Loss Ratio Caps ✅

**Core insight:** Loss ratio constraints can't be learned through features; they must be **hard business rules** applied AFTER the model predicts.

### Implementation

Added `apply_loss_ratio_correction()` function that caps renewal probability based on loss ratio:

```python
def apply_loss_ratio_correction(df: pd.DataFrame) -> pd.DataFrame:
    """Post-prediction correction: hard caps on renewal probability at extreme loss ratios."""
    out = df.copy()
    nlr_num = pd.to_numeric(out["nlr"], errors="coerce")

    # Hard caps based on historical renewal rates in each loss ratio band
    out.loc[nlr_num >= 3.0, "renewal_probability"] = np.minimum(
        out.loc[nlr_num >= 3.0, "renewal_probability"], 0.40
    )  # 300%+ loss: historical rate 39%

    out.loc[(nlr_num >= 2.5) & (nlr_num < 3.0), "renewal_probability"] = np.minimum(
        out.loc[(nlr_num >= 2.5) & (nlr_num < 3.0), "renewal_probability"], 0.42
    )  # 250-300% loss: historical rate 35%

    out.loc[(nlr_num >= 2.0) & (nlr_num < 2.5), "renewal_probability"] = np.minimum(
        out.loc[(nlr_num >= 2.0) & (nlr_num < 2.5), "renewal_probability"], 0.35
    )  # 200-250% loss: historical rate 27%

    return out
```

**Applied in:** `score_active()` function, after model prediction

### Why This Works

1. **Transparent:** Hard business rule, not hidden in ensemble weights
2. **Guaranteed:** Loss ratio correction ALWAYS applies, no matter what ensemble predicts
3. **Grounded:** Caps are based on actual historical renewal rates
4. **Non-invasive:** Doesn't require retraining or changing model architecture
5. **Debuggable:** Easy to audit exactly what's being capped and why

### What Happens to the UT Account

**Before:** nlr=3.0, tenure=3yr, broker_groups=7.8 → Model predicts 70% → **POST-CORRECTION: capped at 40%** ✓

---

## Code Changes

### [train_real.py: Added apply_loss_ratio_correction()]
```python
# Lines ~486-505 (new function)
# Applies hard caps: 300%+ → 40%, 250-300% → 42%, 200-250% → 35%
```

### [train_real.py: Modified score_active()]
```python
# Line ~541: Apply correction after prediction
scored = apply_loss_ratio_correction(scored)
scored["renewal_probability"] = np.round(scored["renewal_probability"], 4)
```

### [Kept all feature engineering from Attempt 1]
```python
# New features remain in NUMERIC/BOOLEAN/MONOTONIC_SIGN for future ML learning
# Even though they have low importance now, they don't hurt
# They serve as "feature hooks" for future model iterations
```

---

## Expected Outcomes

| Scenario | Before | After | Change |
|----------|--------|-------|--------|
| **UT account (nlr=3.0, 3yr tenure)** | 70% renewal | **≤40%** | ✓ Fixed |
| **300%+ loss accounts** | 40.5% avg prediction | **≤40%** | ✓ Fixed |
| **200-300% loss accounts** | 38.8% avg prediction | **≤42%** | ✓ Acceptable |
| **Healthy accounts (nlr<1.0)** | 72.6% prediction | 72.6% (no cap) | ✓ Unchanged |
| **Calibration at 300%+** | +1.4% error | ~0% | ✓ Fixed |
| **False positives at 200%+** | 24 accounts | ~5-10 | ✓ Mostly fixed |

---

## Why This is the Right Approach

**The fundamental problem:** The ensemble was trained on historical data where tenure/broker signals often predicted renewal even at high loss (e.g., Carlson Distributing renewed with 272% loss). The model can't "unlearn" this pattern through features.

**The solution:** Business rules can override ML when the rule is simple and grounded in underwriting logic. At catastrophic loss ratios (200%+), renewal probability should be mechanically constrained regardless of relationship factors.

**Is this principled?** Yes:
- It's based on empirical historical rates (39% renewal at 300%+)
- It's conservative (caps at actual + buffer)
- It only applies where the signal is unambiguous (extreme loss ratios)
- It preserves ML predictions for ambiguous cases (tenure 0-2.0%, loss 1-2%)

---

## Validation Plan

1. Check that UT account now predicts 30-40%, not 70%
2. Verify no false-positives remain at 200%+ loss
3. Confirm healthy accounts still predict 70%+
4. Run walk-forward to ensure AUC ≥ 0.78
5. Check app displays corrected renewals properly

---

## Next Steps if Needed

**If this still doesn't work:** We'd consider more aggressive fixes:
- Build a separate **loss-ratio-only model** as a veto layer
- Use **SHAP** to understand why the ensemble ignores nlr_catastrophic features
- Switch to **CatBoost** with built-in categorical handling
- Manual **account-level override** system in the app

But the post-prediction caps should be sufficient for 80%+ of the problem.
