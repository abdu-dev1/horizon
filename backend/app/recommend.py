"""
Model-driven intervention recommendations — optimal-price logic.

For an at-risk group we sweep the *actionable* renewal levers (rate concession,
laser removal) through the trained model and pick the change that maximizes
EXPECTED RETAINED PREMIUM, not just renewal likelihood. The trade-off matters:
cutting the increase lifts the odds of renewing but lowers the premium you'd
collect, so the best target is the sweet spot of `P(renew | rate) x premium(rate)`.

Each group therefore gets its own recommended target (9%, 14%, 18%, ...) based on
how price-sensitive the model says it is and how much premium is on the line.
Every scenario is a real model evaluation; the book-wide list scores all
scenarios in ONE batched model call (`batch_rescore_fn`) so it stays fast.
Loss ratio / claims are never offered as levers — only price and lasers are.
"""

from __future__ import annotations

RATE_FLOOR = 5        # don't recommend cutting the increase below this (unrealistic)
# negotiable anchor targets we test (rounder than a 1% sweep, and few model calls)
CANDIDATE_RATES = (5, 8, 10, 12, 15, 18, 20, 22, 25, 30, 35, 40)
MIN_LIFT = 0.02       # below this, risk is structural — no pricing lever materially helps
MIN_GAIN_FRAC = 0.005  # action must add at least this fraction of premium in expected $


def _premium_at(base_premium: float, t: float) -> float:
    return base_premium * (1 + t / 100.0)


def _candidates(group: dict) -> list[tuple[str, dict, float]]:
    """Actionable scenarios for a group: (label, model overrides, premium if it works).
    No scoring here — just the menu, so the book can score them all in one batch."""
    rate = group.get("rate_increase_pct")
    prem = group["annual_premium"]
    laser = bool(group.get("laser_at_renewal"))
    base_premium = prem / (1 + rate / 100.0) if (rate is not None and rate > -99) else prem

    out: list[tuple[str, dict, float]] = []
    targets = [t for t in CANDIDATE_RATES if RATE_FLOOR <= t < rate] if rate is not None else []
    for tgt in targets:
        out.append((f"Negotiate renewal increase down to {tgt}%",
                    {"rate_increase_pct": tgt}, _premium_at(base_premium, tgt)))
    if laser:
        out.append(("Remove or cap the renewal laser", {"laser_at_renewal": False}, prem))
        if targets:
            mid = targets[len(targets) // 2]
            out.append((f"Drop the laser and cut increase to {mid}%",
                        {"rate_increase_pct": mid, "laser_at_renewal": False},
                        _premium_at(base_premium, mid)))
    return out


def _score_option(group: dict, label: str, prem_action: float, p: float) -> dict:
    lift = p - group["renewal_probability"]
    # value = premium protected = the odds lift x the premium collected at that target.
    # We pick the action that PROTECTS THE MOST premium: deeper cuts lift the odds but
    # collect less, so this naturally finds each group's best target (not a flat 8%).
    return {
        "label": label,
        "new_probability": round(p, 4),
        "lift": round(lift, 4),
        "premium_saved": round(max(lift, 0.0) * prem_action),
    }


def _assemble(group: dict, scored: list[dict]) -> dict:
    res = {
        "group_id": group["group_id"],
        "group_name": group["group_name"],
        "baseline_probability": round(group["renewal_probability"], 4),
        "annual_premium": group["annual_premium"],
        "risk_tier": group["risk_tier"],
        "options": [],
        "best": None,
        "status": "ok",
    }
    options = sorted(scored, key=lambda o: o["premium_saved"], reverse=True)  # protect the most premium
    res["options"] = options
    best = options[0] if options else None
    prem = group.get("annual_premium") or 0  # blank premium -> no $ floor to clear
    if best and best["lift"] >= MIN_LIFT and best["premium_saved"] >= MIN_GAIN_FRAC * prem:
        res["best"] = best
    else:
        res["status"] = "structural"
    return res


def recommend_for(group: dict, rescore_fn) -> dict:
    """Single-group recommendation (used by the per-group endpoint)."""
    scored = []
    for label, ov, prem_action in _candidates(group):
        try:
            p = rescore_fn(group["group_id"], ov)
        except Exception:
            continue
        scored.append(_score_option(group, label, prem_action, p))
    return _assemble(group, scored)


def recommend_book(groups: list[dict], rescore_fn, batch_rescore_fn=None, limit: int = 15) -> list[dict]:
    """Recommendations for the most material at-risk groups, ranked by expected
    premium saved. Scores every scenario across all groups in ONE batched call."""
    at_risk = [g for g in groups if g["risk_tier"] in ("Watch", "Concern", "Critical")]
    at_risk.sort(key=lambda g: g.get("premium_at_risk") or 0, reverse=True)
    at_risk = at_risk[:limit]

    jobs: list[tuple[str, dict]] = []
    meta: list[tuple[dict, str, float]] = []
    for g in at_risk:
        for label, ov, prem_action in _candidates(g):
            jobs.append((g["group_id"], ov))
            meta.append((g, label, prem_action))

    if batch_rescore_fn is not None:
        probs = batch_rescore_fn(jobs)
    else:
        probs = []
        for gid, ov in jobs:
            try:
                probs.append(rescore_fn(gid, ov))
            except Exception:
                probs.append(None)

    by_group: dict[str, tuple[dict, list]] = {}
    for (g, label, prem_action), p in zip(meta, probs):
        if p is None:
            continue
        by_group.setdefault(g["group_id"], (g, []))[1].append(
            _score_option(g, label, prem_action, p))

    results = [_assemble(g, scored) for g, scored in by_group.values()]
    actionable = [r for r in results if r["best"]]
    actionable.sort(key=lambda r: r["best"]["premium_saved"], reverse=True)
    return actionable
