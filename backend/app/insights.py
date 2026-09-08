"""
Business-facing analytics on top of the scored book:
  * risk tiering
  * per-group driver explanations (reason codes)
  * 2-quarter forward renewal forecast (monthly + quarterly rollups)
  * segment retention views (LOB, RSD, broker quality, tenure)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Start of LAST quarter (relative to whenever this module loads / the server restarts),
# not a hand-picked date someone has to remember to bump. This is what was silently
# hiding whole months of real data: a fixed "2026-06-01" was right the day it was
# written and wrong forever after. Anchoring one quarter back means the just-completed
# quarter is always in view (for actual-vs-predicted comparison) along with everything
# still ahead, with no manual edit ever required again.
AS_OF = (pd.Timestamp.today().normalize().to_period("Q") - 1).start_time


def horizon_end(as_of: pd.Timestamp = AS_OF) -> pd.Timestamp:
    """Last day of the quarter three quarters out. Extended from 2 to 3 quarters so the
    Dec/Jan active-business renewals (anniversary in Dec 2026 / Jan 2027) are in window."""
    q_end = as_of.to_period("Q").end_time.normalize()
    return (q_end + pd.DateOffset(months=9)).to_period("M").end_time.normalize()


def due_leakfree(scored: pd.DataFrame, as_of: pd.Timestamp = AS_OF) -> pd.DataFrame:
    """The due-window book, LEAK-FREE: same filter as group_rows() (Upcoming Renewals /
    Needs Data), so every 'model scorecard' view — KPIs, LOB/RSD/tenure segments, and the
    two renewal lists — counts the same set of groups. (forecast() deliberately uses the
    raw due window INCLUDING leaked rows instead — it's the business outlook, not the
    model scorecard — so it does not call this helper.)"""
    h_end = horizon_end(as_of)
    due = scored[(scored["renewal_date"] >= as_of) & (scored["renewal_date"] <= h_end)]
    if "leaked" in due.columns:
        due = due[~due["leaked"].fillna(False).astype(bool)]
    return due


def tier(p: float) -> str:
    # FIXED, absolute bands keyed to what the probability MEANS (not tuned to any one
    # book's histogram, so a tier means the same thing every retrain). Balanced for
    # legibility but with the label kept honest: the Stable floor sits at 68% so a
    # mid-60s coin-lean (~1-in-3 churn) reads "Watch", not "Stable".
    #   Secure   >= 0.80  very likely to renew (<=20% churn)
    #   Stable   >= 0.68  leans solidly to renew
    #   Watch    >= 0.59  leans toward renewing, but real uncertainty — monitor
    #   Concern  >= 0.50  leans toward NOT renewing — needs attention
    #   Critical < 0.50   predicted to lapse
    # Watch/Concern split (added 2026-08-04) sits at 0.59 - the MIDPOINT of the
    # original 0.50-0.68 "Watch" band, not a value picked to match today's book.
    # Reason: on ~600 rows of training data, isotonic calibration's resolution is
    # coarse enough that the model's output concentrates heavily in that range (73%
    # of one book landed in the single old "Watch" tier) - a fixed, generally-derived
    # split gives real differentiation there without becoming meaningless the moment
    # the underlying distribution shifts on a future retrain.
    if p >= 0.80:
        return "Secure"
    if p >= 0.68:
        return "Stable"
    if p >= 0.59:
        return "Watch"
    if p >= 0.50:
        return "Concern"
    return "Critical"


TIER_ORDER = ["Secure", "Stable", "Watch", "Concern", "Critical"]


def drivers(row: pd.Series) -> list[dict]:
    """Plain-English reason codes for a group's score, ranked by severity.
    Mirrors the factor list the business identified; severity weights track
    the model's learned importances."""
    found = []

    def add(label, direction, severity):
        found.append({"label": label, "direction": direction, "severity": round(severity, 2)})

    lr = row["loss_ratio"]
    if lr >= 1.0:
        add(f"Loss ratio {lr:.0%} — severely adverse", "negative", 3.0)
    elif lr >= 0.85:
        add(f"Loss ratio {lr:.0%} — running hot", "negative", 2.2)
    elif lr <= 0.55:
        add(f"Loss ratio {lr:.0%} — favorable experience", "positive", 1.5)

    if row["recent_bor"]:
        add("BOR change in the last 12 months", "negative", 2.6)
    if row["laser_at_renewal"]:
        add("Laser applied at renewal", "negative", 2.0)
    if row["rate_increase_pct"] >= 20:
        add(f"Steep renewal increase ({row['rate_increase_pct']:.0f}%)", "negative", 2.1)
    elif row["rate_increase_pct"] >= 12:
        add(f"Above-trend renewal increase ({row['rate_increase_pct']:.0f}%)", "negative", 1.4)
    elif row["rate_increase_pct"] <= 3:
        add(f"Modest renewal increase ({row['rate_increase_pct']:.0f}%)", "positive", 0.9)

    if row["tenure_years"] <= 1:
        add("First renewal — highest-churn point in lifecycle", "negative", 1.3)
    elif row["tenure_years"] >= 4:
        add(f"{int(row['tenure_years'])} years with Crumdale", "positive", 1.6)

    if row["broker_quality"] <= 2:
        add("Weak broker relationship", "negative", 1.5)
    elif row["broker_quality"] >= 4:
        add("Strong broker relationship", "positive", 1.4)

    if row["uw_rtm_initial"] >= 1.10 and row["tenure_years"] <= 2:
        add("Priced above market at initial sale", "negative", 1.2)
    if row["service_escalations"] >= 3:
        add(f"{int(row['service_escalations'])} service escalations this period", "negative", 1.1)
    if row["large_claim_flag"] and not row["laser_at_renewal"]:
        add("Large claim activity", "negative", 0.8)
    if row["laser_at_initial_sale"]:
        add("Laser at initial sale", "negative", 0.6)

    found.sort(key=lambda d: d["severity"], reverse=True)
    return found[:5]


def score_book(model, book: pd.DataFrame) -> pd.DataFrame:
    scored = book.copy()
    scored["renewal_probability"] = np.round(model.predict_proba(book), 4)
    scored["risk_tier"] = scored["renewal_probability"].apply(tier)
    scored["premium_at_risk"] = np.round(
        scored["annual_premium"] * (1 - scored["renewal_probability"]), 0
    )
    scored["renewal_date"] = pd.to_datetime(scored["renewal_date"])
    return scored


def summary(scored: pd.DataFrame, as_of: pd.Timestamp = AS_OF) -> dict:
    h_end = horizon_end(as_of)
    due = due_leakfree(scored, as_of)
    tiers = due["risk_tier"].value_counts().to_dict()
    # $ figures sum only groups with real premium (skipna) — estimates are left blank,
    # so the dollar view reflects confirmed premium, not lives-based guesses.
    due_prem = float(due["annual_premium"].sum())
    n_prem = int(scored["annual_premium"].notna().sum())
    return {
        "as_of": as_of.date().isoformat(),
        "horizon_end": h_end.date().isoformat(),
        "groups_in_force": int(len(scored)),
        "groups_with_premium": n_prem,
        "premium_in_force": float(scored["annual_premium"].sum()),
        "renewals_due": int(len(due)),
        "premium_due": due_prem,
        "expected_renewals": float(due["renewal_probability"].sum()),
        "expected_retention_rate": float(due["renewal_probability"].mean()) if len(due) else None,
        "premium_weighted_retention": float(
            (due["renewal_probability"] * due["annual_premium"]).sum() / due_prem
        ) if due_prem > 0 else None,
        "premium_at_risk": float(due["premium_at_risk"].sum()),
        "tier_counts": {t: int(tiers.get(t, 0)) for t in TIER_ORDER},
    }


def forecast(scored: pd.DataFrame, as_of: pd.Timestamp = AS_OF,
             confirmed: pd.DataFrame | None = None) -> dict:
    """Monthly/quarterly renewal outlook over the FULL renewal book (incl. leaked rows —
    the outlook is the business picture, not the model scorecard). Two layers via each
    row's `actual_outcome`:
      * CONFIRMED = settled renewals (Renewed/Termed) -> real outcome (1.0 / 0.0).
      * PROJECTED = still-open renewals -> model probability.
    The UI's "Projected only / + Confirmed" toggle shows/hides the confirmed layer. So
    "+ Confirmed" shows the true monthly totals (e.g. July's ~33), while "Projected only"
    is the still-open forward view. (`confirmed` param is accepted for backward-compat but
    unused — the book already carries every renewal and its outcome.)"""
    h_end = horizon_end(as_of)
    due = scored[(scored["renewal_date"] >= as_of) & (scored["renewal_date"] <= h_end)].copy()
    if "_canon" in due.columns:  # guard against any accidental dupes
        due["_m"] = due["renewal_date"].dt.to_period("M").astype(str)
        due = due.drop_duplicates(subset=["_canon", "_m"])

    ao = (due["actual_outcome"] if "actual_outcome" in due.columns
          else pd.Series(index=due.index, dtype=object))
    is_dec = ao.isin(["Renewed", "Termed"]).values
    prob = pd.to_numeric(due["renewal_probability"], errors="coerce").values
    renew = np.where(is_dec, (ao.values == "Renewed").astype(float), prob)

    allg = pd.DataFrame({
        "group_name": due["group_name"].astype(str).values,
        "month": due["renewal_date"].dt.to_period("M").astype(str).values,
        "quarter": due["renewal_date"].dt.to_period("Q").astype(str).values,
        "renew": renew,
        "premium": pd.to_numeric(due["annual_premium"], errors="coerce").values,
        "tier": (due["risk_tier"].values if "risk_tier" in due.columns else None),
        "decided": is_dec,
        "probability": np.where(is_dec, np.nan, prob),  # % only for open rows
        "status": np.where(is_dec, np.where(ao.values == "Renewed", "Renewed", "Lost"), "Open"),
    })

    allg["lapse"] = 1 - allg["renew"]
    allg["prem_ret"] = allg["premium"] * allg["renew"]
    allg["prem_risk"] = allg["premium"] * allg["lapse"]

    def _agg(grp: pd.DataFrame, label_key: str, label_val: str) -> dict:
        conf = grp[grp["decided"]]
        opn = grp[~grp["decided"]]
        return {
            label_key: label_val,
            "renewals_due": int(len(grp)),
            # confirmed = ACTUAL outcomes already recorded (renewal database)
            "confirmed_renewed": int((conf["renew"] == 1).sum()),
            "confirmed_lost": int((conf["renew"] == 0).sum()),
            "confirmed_count": int(len(conf)),
            # projected = still-open renewals, probability-weighted (the prediction)
            "projected_renewals": round(float(opn["renew"].sum()), 1),
            "projected_lapses": round(float(opn["lapse"].sum()), 1),
            "open_count": int(len(opn)),
            # totals (confirmed actuals + projected) for the retention line / cards
            "expected_renewals": round(float(grp["renew"].sum()), 1),
            "expected_lapses": round(float(grp["lapse"].sum()), 1),
            "retention_rate": round(float(grp["renew"].mean()), 4) if len(grp) else 0.0,
            # projected-only retention (still-open groups), for the default forecast view
            "projected_retention_rate": (round(float(opn["renew"].mean()), 4)
                                         if len(opn) else None),
            "premium_due": float(grp["premium"].sum()),
            "premium_retained": float(grp["prem_ret"].sum()),
            "premium_at_risk": float(grp["prem_risk"].sum()),
            # premium split so the view can show projected-only or projected+confirmed
            "projected_premium_retained": float(opn["prem_ret"].sum()),
            "projected_premium_at_risk": float(opn["prem_risk"].sum()),
            "confirmed_premium_retained": float(conf["prem_ret"].sum()),
            "confirmed_premium_at_risk": float(conf["prem_risk"].sum()),
        }

    # Fully-settled periods (nothing left to renew or predict) drop off the outlook on
    # their own once every group in them is decided — no one has to remember to go
    # back and manually retire a quarter once it's behind us.
    months = [m for m in (_agg(g, "month", m) for m, g in allg.groupby("month"))
              if m["open_count"] > 0]
    quarters = []
    for quarter, grp in allg.groupby("quarter"):
        q = _agg(grp, "quarter", quarter)
        if q["open_count"] == 0:
            continue
        # watch/concern/critical only apply to still-open (scored) groups
        opn = grp[~grp["decided"]]
        q["critical_groups"] = int((opn["tier"] == "Critical").sum())
        q["concern_groups"] = int((opn["tier"] == "Concern").sum())
        q["watch_groups"] = int((opn["tier"] == "Watch").sum())
        quarters.append(q)

    # per-group detail: confirmed outcomes + open predictions, one row per group
    _status_order = {"Lost": 0, "Open": 1, "Renewed": 2}
    detail = []
    for _, r in allg.iterrows():
        prem = r["premium"]
        prob = r["probability"]
        detail.append({
            "group_name": r["group_name"],
            "month": r["month"],
            "status": r["status"],
            "confirmed": bool(r["decided"]),
            "renewal_probability": (round(float(prob), 4)
                                    if (prob is not None and pd.notna(prob)) else None),
            "annual_premium": (float(prem) if pd.notna(prem) else None),
        })
    detail.sort(key=lambda d: (d["month"], _status_order.get(d["status"], 1),
                               d["group_name"]))

    return {"months": sorted(months, key=lambda m: m["month"]),
            "quarters": sorted(quarters, key=lambda q: q["quarter"]),
            "detail": detail}


def segments(scored: pd.DataFrame, as_of: pd.Timestamp = AS_OF) -> dict:
    due = due_leakfree(scored, as_of).copy()

    def agg(frame, key, label_fn=str):
        out = []
        for value, grp in frame.groupby(key, observed=False):
            if len(grp) == 0:
                continue
            out.append({
                "segment": label_fn(value),
                "groups": int(len(grp)),
                "retention_rate": round(float(grp["renewal_probability"].mean()), 4),
                "premium_at_risk": float(grp["premium_at_risk"].sum()),
            })
        return sorted(out, key=lambda r: r["retention_rate"])

    due["tenure_bucket"] = pd.cut(
        due["tenure_years"], bins=[0, 1, 2, 3, 5, 100],
        labels=["1st renewal", "2nd year", "3rd year", "4-5 years", "6+ years"],
    )
    due["broker_tier"] = pd.cut(
        due["broker_quality"], bins=[0, 2, 3, 5],
        labels=["Weak (1-2)", "Average (3)", "Strong (4-5)"],
    )

    return {
        "by_lob": agg(due, "line_of_business"),
        "by_rsd": agg(due, "rsd"),
        "by_tenure": agg(due, "tenure_bucket", label_fn=lambda v: str(v)),
        "by_broker_tier": agg(due, "broker_tier", label_fn=lambda v: str(v)),
    }


def _n(v):
    """NaN-safe float -> None, so JSON stays valid for projected rows."""
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)


def _ni(v):
    """NaN-safe int -> None."""
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else int(v)


def _nb(v):
    """Tri-state bool: True / False / None (unknown)."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    return bool(v)


def group_rows(scored: pd.DataFrame, as_of: pd.Timestamp = AS_OF) -> list[dict]:
    due = due_leakfree(scored, as_of).sort_values("renewal_probability")
    rows = []
    for _, r in due.iterrows():
        rows.append({
            "group_id": r["group_id"],
            "group_name": r["group_name"],
            "line_of_business": r["line_of_business"],
            "industry": r["industry"],
            "state": r["state"],
            "group_size": int(r["group_size"]),
            "annual_premium": _n(r["annual_premium"]),
            "renewal_date": r["renewal_date"].date().isoformat(),
            "tenure_years": _ni(r["tenure_years"]),
            "broker_name": r["broker_name"],
            "broker_quality": _ni(r["broker_quality"]),
            "recent_bor": _nb(r["recent_bor"]),
            "rsd": r["rsd"],
            "am": r["am"],
            "carrier": r.get("carrier"),
            "tpa": r.get("tpa"),
            "corridor": _n(r.get("corridor")),
            "premium_stoploss": _n(r.get("premium_stoploss")),
            "loss_ratio": _n(r["loss_ratio"]),
            "loss_ratio_status": r.get("loss_ratio_status"),
            "agg_loss_ratio": _n(r.get("agg_loss_ratio")),
            "ratio_to_attachment": _n(r.get("ratio_to_attachment")),
            "rate_increase_pct": _n(r["rate_increase_pct"]),
            "laser_at_renewal": bool(r["laser_at_renewal"]),
            "laser_at_initial_sale": bool(r["laser_at_initial_sale"]),
            "uw_rtm_initial": _n(r["uw_rtm_initial"]),
            "service_escalations": int(r["service_escalations"]),
            "renewal_probability": float(r["renewal_probability"]),
            "risk_tier": r["risk_tier"],
            "premium_at_risk": _n(r["premium_at_risk"]),
            "confirmed": bool(r.get("confirmed", True)),
            "premium_estimated": bool(r.get("premium_estimated", False)),
            "loss_ratio_known": bool(r.get("loss_ratio_known", True)),
            "tenure_known": bool(r.get("tenure_known", True)),
            "lives_known": bool(r.get("lives_known", True)),
            "data_basis": r.get("data_basis", "underwriting"),
            # trust signal: a prediction with no loss ratio loaded (the top underwriting
            # driver) is scored on imputed medians -> flag it as lower confidence so the
            # probability isn't over-trusted.
            "prediction_confidence": ("low" if not bool(r.get("loss_ratio_known", True))
                                      else "standard"),
            # real recorded outcome for this exact renewal (null if not yet decided)
            "actual_outcome": (r.get("actual_outcome")
                               if "actual_outcome" in r.index else None),
            "drivers": drivers(r),
            # Rest of the model's raw UW block (same canonical names as
            # upload_feed.py) - powers the Needs Data editable grid, which shows
            # and edits every field the model uses, not just a curated subset.
            "product": r.get("product"),
            "isl_loss_ratio": _n(r.get("isl_loss_ratio")),
            "mature_to_attachment": _n(r.get("mature_to_attachment")),
            "initial_uw_increase_pct": _n(r.get("initial_uw_increase_pct")),
            "fixed_increase_pct": _n(r.get("fixed_increase_pct")),
            "lasers_current": _n(r.get("lasers_current")),
            "lasers_renewal_count": _n(r.get("lasers_renewal_count")),
            "laser_liability": _n(r.get("laser_liability")),
            "captive_offer": _nb(r.get("captive_offer")),
            "network": r.get("network"),
            "broker_years_with_cs": _n(r.get("broker_years_with_cs")),
            "broker_groups_with_cs": _n(r.get("broker_groups_with_cs")),
            "broker_products_sold": _n(r.get("broker_products_sold")),
            "broker_preferred": _nb(r.get("broker_preferred")),
        })
    return rows
