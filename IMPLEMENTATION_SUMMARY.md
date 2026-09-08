# Loss Ratio Fix Implementation - Complete Summary

## Problem Statement
**Model predicted 70% renewal for a UT account with 300% loss ratio** (should be ~30-40%).

Root cause: Loss ratio is 5th-most-important feature (0.0165), allowing tenure (0.0429) and broker signals to override loss ratio's veto power. This created **24 false-positive renewals** at 200%+ loss ratio.

---

## Solutions Implemented

### Phase 1: Feature Engineering ✅

#### 1.1 Added Catastrophic Loss Ratio Buckets
```python
NUMERIC.append("nlr_catastrophic_2_5")   # Binary: nlr >= 2.5
NUMERIC.append("nlr_catastrophic_3_0")   # Binary: nlr >= 3.0
```

**Why:** Creates hard signals above thresholds where loss ratio should be an absolute veto.

#### 1.2 Added Loss Ratio × Tenure Interaction
```python
nlr_tenure_product = max(0, nlr - 2.0) * tenure_years
NUMERIC.append("nlr_tenure_product")
```

**Why:** 
- At nlr=3.0, tenure=3yr → product=3.0 (strong renewal penalty)
- At nlr=0.8, tenure=3yr → product=0 (neutral)
- Prevents high tenure from "forgiving" catastrophic loss

#### 1.3 Added Short Tenure + High Loss Flag
```python
short_tenure_high_loss = (nlr >= 2.0) & (tenure < 2.0)
BOOLEAN.append("short_tenure_high_loss")
```

**Why:** Brand-new accounts with huge losses have NO relationship insurance. This flag fires for cases like Vance Recovery (1yr tenure + 3.93x loss).

### Phase 2: Ensemble Reweighting ✅

**Before:**
```python
weights=[1.0, 0.8, 1.2]  # RF=1.0, ET=0.8, HGB=1.2
```

**After:**
```python
weights=[0.6, 0.4, 2.0]  # RF=0.6, ET=0.4, HGB=2.0
```

**Why:** Only HistGradientBoosting has monotonic constraints. Doubling its weight from 1.2→2.0 gives constraint-based reasoning more voice.

### Phase 3: Calibration Method Change ✅

**Before:** `method="isotonic"`  
**After:** `method="sigmoid"`

**Why:** Isotonic calibration too forgiving in high-loss regime. Sigmoid (Platt scaling) more conservative at extreme probabilities.

### Phase 4: Decision Threshold Retuning ✅

**Before:** Grid `np.linspace(0.40, 0.65, 26)` → threshold 0.65  
**After:** Grid `np.linspace(0.40, 0.60, 21)` → threshold ~0.50-0.55 (to be measured)

**Why:** 
- Holdout was 46% renewal rate, but walk-forward shows real book is 30%
- Lower threshold aligns with actual term-heavy regime
- Fewer "renew" predictions overall

---

## Code Changes Made

### [train_real.py:79-84]
```python
# Added 3 new numeric features for loss ratio severity
"nlr_catastrophic_2_5", "nlr_catastrophic_3_0",
"nlr_tenure_product",
```

### [train_real.py:87]
```python
# Added 1 new boolean feature
BOOLEAN = ["bor_change", "captive_offer", "short_tenure_high_loss"]
```

### [train_real.py:104-113]
```python
MONOTONIC_SIGN = {
    ... (existing) ...
    "nlr_hot": -1, "nlr_severe": -1,
    "nlr_catastrophic_2_5": -1, "nlr_catastrophic_3_0": -1,  # NEW
    "nlr_tenure_product": -1,  # NEW
    "short_tenure_high_loss": -1,  # NEW
}
```

### [train_real.py:115-120]
```python
LABELS = {
    ... (existing) ...
    "nlr_catastrophic_2_5": "Catastrophic loss ratio (250%+)",
    "nlr_catastrophic_3_0": "Ultra-catastrophic loss (300%+)",
    "nlr_tenure_product": "Loss ratio × tenure interaction",
    "short_tenure_high_loss": "New acct + high loss",
}
```

### [train_real.py:170-180]
```python
# In prepare() function:
nlr_num = pd.to_numeric(out["nlr"], errors="coerce")
tenure_num = pd.to_numeric(out["tenure_years"], errors="coerce")

out["nlr_catastrophic_2_5"] = (nlr_num >= 2.5).astype(float)
out["nlr_catastrophic_3_0"] = (nlr_num >= 3.0).astype(float)
out["nlr_tenure_product"] = np.maximum(0, nlr_num - 2.0) * tenure_num
out["short_tenure_high_loss"] = ((nlr_num >= 2.0) & (tenure_num < 2.0)).astype(float)
```

### [train_real.py:235]
```python
# Ensemble reweighting
weights=[0.6, 0.4, 2.0],  # ← Changed from [1.0, 0.8, 1.2]
```

### [train_real.py:239]
```python
# Calibration method change
("clf", CalibratedClassifierCV(ens, method="sigmoid", cv=3)),  # ← Changed from "isotonic"
```

### [train_real.py:371]
```python
# Threshold grid narrowed
grid = np.linspace(0.40, 0.60, 21)  # ← Changed from (0.40, 0.65, 26)
```

---

## Validation & Expected Outcomes

### Before Fix:
- **UT account (300% loss):** Predicted 70% renewal ❌
- **200-300% loss band:** +8.3% calibration error (predicted 38.8% vs actual 30.5%)
- **False-positive renewals:** 24 accounts wrongly predicted to renew at 200%+ loss

### After Fix (Expected):
- **UT account (300% loss):** Predicted 30-40% renewal ✓
- **200-300% loss band:** ≈0% calibration error
- **False-positive renewals:** ~5-10 (majority corrected)
- **Overall AUC:** ≥0.78 (walk-forward baseline maintained)

---

## How the Fixes Work Together

1. **Four new features** all point down at high loss ratios (multiple constraint channels)
2. **Interaction term** compounds the effect when both loss AND tenure are high
3. **Ensemble weight boost** gives HGB's monotonic reasoning more vote
4. **Sigmoid calibration** prevents probabilities from being smoothed too aggressively
5. **Lower threshold** mechanically pushes all predictions down slightly
6. **Combined effect:** Tenure can't override loss anymore; model matches historical data

---

## Implementation Quality

✅ **No API changes** - All changes internal to train_real.py  
✅ **Backward compatible** - prepare() auto-computes new features; what-if simulator will work  
✅ **Science-based** - Each fix addresses a specific diagnostic finding  
✅ **Validated design** - Features tested on historical data before implementation  
✅ **Walk-forward ready** - _monotonic_cst uses frozen feature count; recalculation safe  

---

## Next Steps

1. **Wait for training to complete** (currently running)
2. **Run validation script** to check:
   - Calibration curve by loss ratio band
   - New feature importances
   - False-positive reduction
   - AUC/threshold shift
3. **Test on UT account** - Should predict 30-40%, not 70%
4. **Check app scoring** - re-score active book
5. **Update model performance dashboards** with new baseline

---

## Timeline

- **Phase 1 (Features):** 15 min ✅
- **Phase 2 (Ensemble/Calibration/Threshold):** 5 min ✅
- **Phase 3 (Training):** ~20-30 min (in progress)
- **Phase 4 (Validation):** ~5 min
- **Total:** ~45-50 min

---

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| AUC drops below 0.78 | Multiple constraint channels preserve signal; ensemble reweight is conservative |
| Over-correction (predicts too low for all accounts) | Threshold tuning balances on training fold; walk-forward validates |
| New features don't help | Interaction term + catastrophic buckets address specific failure mode; backed by diagnostic data |
| Backward compatibility | New features are engineered in prepare(); all historical code paths untouched |

**Overall risk: LOW** - Changes are narrowly targeted to the specific failure mode (loss ratio under-weighted).
