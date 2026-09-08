"""
Win-likelihood banding / reason-code layer for the New Business model.

Deliberately not a copy of the renewal project's insights.py: the semantics
run the OPPOSITE direction (here, a HIGH probability is the good outcome —
"about to win" — where the renewal side's high probability means "about to
renew, low action needed"), and the base rate is far lower (~4.7% win rate vs.
~60-70% renewal rate), so the band thresholds have to be calibrated to this
book's own distribution, not reused from the renewal side's.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Plain probability language, not sales-temperature labels. The old
# Hot/Warm/Long Shot/Cold set was replaced (2026-08-26) for two reasons:
# "Hot"/"Cold" describe a salesperson's enthusiasm rather than a likelihood,
# and the four labels implied four distinguishable levels when the middle two
# measured 15.8% vs. 12.7% -- statistically the same band wearing two names.
# These four are ordered, self-describing, and each one has to earn its place
# by measuring a materially different win rate (see band_reliability).
BAND_ORDER = ["High", "Moderate", "Low", "Very Low"]

# Score cutoffs, high band first. Checked, not assumed: experiment_nb.py's
# tune_bands() searches the score distribution for cuts where every adjacent
# band differs by >= 1.6x in MEASURED win rate and every band holds enough
# quotes to state a rate at all -- run against a single-HGB proxy (a different
# score scale than the shipped RF+ET+HGB ensemble, so its exact numbers don't
# transfer directly). The number that DOES transfer is the shipped ensemble's
# own out-of-fold reliability under these inherited cutoffs (train_nb.py's
# registry, 2026-08-26 retrain): High 29.5% (n=488) / Moderate 11.1% (n=748) /
# Low 6.4% (n=1292) / Very Low 2.0% (n=6868) -- every adjacent ratio (2.66x,
# 1.73x, 3.2x) already clears the bar, so these cutoffs are kept as-is rather
# than swapped for the proxy model's thresholds. Re-run tune_bands against the
# real ensemble (not the proxy) if the score distribution shifts materially on
# a future retrain.
BAND_CUTS = [("High", 0.20), ("Moderate", 0.08), ("Low", 0.03), ("Very Low", 0.0)]


def band(p: float) -> str:
    """Bucket a raw model score into its likelihood band. Kept as a function
    over BAND_CUTS (rather than an if-ladder) so the thresholds live in exactly
    one place -- the frontend mirrors this list by hand in format.js, and an
    if-ladder is the shape that silently drifts."""
    if p is None or (isinstance(p, float) and np.isnan(p)):
        return BAND_ORDER[-1]
    for name, lo in BAND_CUTS:
        if p >= lo:
            return name
    return BAND_ORDER[-1]


# Exec-provided rule-of-thumb close rate by Salesforce pipeline stage --
# business judgment, NOT measured from data (stage-at-decision-time isn't
# preserved in nb_history.csv, see etl_nb.py, so this can never be validated
# against actuals the way band_reliability() is). Kept completely separate
# from the model's own (measured) band_win_rate -- see score_book() -- so the
# two numbers can disagree without either one silently overwriting the other.
# Bucket boundaries as given: Quote Request/Assignment/Pre-Production/
# Production and "Resource Pro" = 5% (no illustrative or firm quote yet);
# any "Illustrative Quote *" stage = 14%; any "Firm Quote *" stage = 30%;
# "Initial Sales Notification" = 100% (sale already confirmed, pending
# formal close). Any stage not in this map is left blank, not defaulted --
# same blank-over-fake rule as everywhere else in this project.
STAGE_SALES_ESTIMATE = {
    "Quote Request": 0.05,
    "Quote Assignment": 0.05,
    "Quote Pre-Production": 0.05,
    "Quote Production": 0.05,
    "Resource Pro": 0.05,
    "Illustrative Quote Underwriting": 0.14,
    "Illustrative Quote Released": 0.14,
    "Illustrative Quote Pre-Production": 0.14,
    "Illustrative Quote Production and Delivery": 0.14,
    "Firm Quote Released": 0.30,
    "Firm Quote Underwriting": 0.30,
    "Initial Sales Notification": 1.00,
}


def band_reliability(proba: np.ndarray, y: np.ndarray) -> dict:
    """Empirical (measured, not model-predicted) win rate per band, computed
    from pooled out-of-fold CV predictions.

    Why this exists: a single quote's raw calibrated score is not reliable at
    face value in a 4.7%-base-rate book -- there simply aren't enough wins to
    support a fine-grained per-quote number (the original check, at 79 wins:
    quotes scored >=70% won only ~33% of the time, n=9). Pooling dozens of
    quotes INTO one band and reporting THAT band's real historical frequency is
    a far more defensible number -- the same idea as isotonic calibration, done
    by hand at a coarseness the data can actually support. This is the number
    the dashboard headlines; the raw per-quote model score is kept only as a
    secondary, technical field.
    """
    out = {}
    for t in BAND_ORDER:
        mask = np.array([band(p) == t for p in proba])
        n = int(mask.sum())
        wins = int(y[mask].sum()) if n else 0
        out[t] = {"rate": (wins / n) if n else None, "n": n, "wins": wins}
    return out


TRACK_RECORD_COLS = {
    "rsd": "RSD", "broker": "Broker", "industry": "Industry",
    "billing_state": "State", "product": "Product",
}


def track_record_maps(history: pd.DataFrame, smoothing: float = 10.0) -> dict:
    """Smoothed historical win-rate + raw sample size per category, for the
    categorical columns the model reads -- used to explain, in plain English,
    WHY a track-record feature pushed a score up or down (e.g. "this RSD wins
    10% of quotes vs. a 4.7% average"), which the model uses as a real feature
    but would otherwise never surface as a reason code.

    Independent, simpler re-derivation from history -- not reading the fitted
    model's internals -- so it's directly checkable against the data by anyone,
    and never drifts from what a human would compute by hand from
    nb_history.csv.

    `product` is included even though the model one-hots it rather than
    win-rate-encoding it (see train_nb.ONEHOT_CATEGORICAL): a reason code cares
    that the model SEES the column, not how it's encoded, and product is the
    single strongest categorical in the book (PBM Only wins 22.7% on 282
    quotes). `underwriter` was removed along with the feature itself -- 0%
    populated on open quotes and no signal on history either.
    """
    base_rate = float(history["sold"].mean())
    maps = {"_base_rate": base_rate}
    for col in TRACK_RECORD_COLS:
        if col not in history.columns:
            continue
        stats = history.groupby(col)["sold"].agg(["mean", "count"])
        smoothed = (stats["mean"] * stats["count"] + base_rate * smoothing) / (stats["count"] + smoothing)
        maps[col] = {
            cat: {"rate": float(smoothed[cat]), "n": int(stats["count"][cat]), "raw_rate": float(stats["mean"][cat])}
            for cat in stats.index
        }
    # Win rate by deal-size band, same shape as the categorical maps above.
    # Size is the sharpest split in the book (cases over 500 lives win at
    # roughly double the rate of everything below) and `lives` is a model
    # feature, so it deserves a reason code rather than staying invisible.
    lives = pd.to_numeric(history.get("lives"), errors="coerce")
    size_map = {}
    for lo, hi, name in SIZE_BANDS:
        m = (lives > lo) & (lives <= hi)
        if m.sum():
            size_map[name] = {"rate": float(history.loc[m, "sold"].mean()), "n": int(m.sum()),
                              "lo": lo, "hi": hi}
    maps["_size"] = size_map
    return maps


def _size_band(lives) -> str | None:
    if lives is None or pd.isna(lives):
        return None
    for lo, hi, name in SIZE_BANDS:
        if lo < lives <= hi:
            return name
    return None


def drivers(row: pd.Series, track_maps: dict | None = None) -> list[dict]:
    """Plain-English reason codes for one open quote -- rule-based on the same
    fields the model actually sees, not a black-box explainer, so every reason
    is literally checkable against the row.

    Kept in step with train_nb's feature lists by hand. The 2026-08-26 rework
    removed the reason codes for pct_vs_current, has_firm_quote /
    has_illustrative_quote, days_created_to_illustrative, laser_count and
    isl_deductible: those fields stopped being model features (~0% populated on
    an open quote), so a reason code citing them would have credited the score
    to something that no longer touches it. "Reached firm quote stage" was the
    worst offender -- it read as the model's strongest positive while being
    false for every open quote in the book.
    """
    out = []

    def add(label, direction, severity=1.0):
        out.append({"label": label, "direction": direction, "severity": float(severity)})

    if track_maps:
        base = track_maps["_base_rate"]
        for col, noun in TRACK_RECORD_COLS.items():
            val = row.get(col)
            entry = track_maps.get(col, {}).get(val) if pd.notna(val) else None
            if entry is None or entry["n"] < 5:
                continue
            rate, n = entry["rate"], entry["n"]
            if rate >= base * 1.8:
                add(f"{noun} “{val}” wins {rate:.0%} of quotes historically "
                    f"(n={n}) vs. a {base:.0%} average", "positive", min(2.2, 1.0 + rate / base * 0.3))
            elif rate <= base * 0.4 and n >= 15:
                add(f"{noun} “{val}” wins only {rate:.0%} of quotes historically "
                    f"(n={n}) vs. a {base:.0%} average", "negative", 1.2)

        band = _size_band(row.get("lives"))
        entry = track_maps.get("_size", {}).get(band) if band else None
        if entry and entry["n"] >= 25:
            rate = entry["rate"]
            if rate >= base * 1.5:
                add(f"{int(row['lives']):,} lives — cases this size ({band}) win {rate:.0%} "
                    f"of the time (n={entry['n']}) vs. a {base:.0%} average", "positive", 1.7)
            elif rate <= base * 0.75:
                add(f"{int(row['lives']):,} lives — cases this size ({band}) win {rate:.0%} "
                    f"of the time (n={entry['n']}) vs. a {base:.0%} average", "negative", 0.8)

    # rus_dtq / competitive reason codes were removed earlier -- both source
    # columns (RUS:DTQ?, Competitive?) don't exist in this export, so the fields
    # were a constant 0 on every row. That meant "Not yet flagged as
    # price-competitive" fired as a negative reason on literally every quote,
    # which isn't a real signal, just a data gap dressed up as one.

    runway = row.get("days_created_to_eff")
    if pd.notna(runway):
        if runway >= 180:
            add(f"{int(runway)} days of runway to the effective date — quoted early", "positive", 0.9)
        elif runway <= 30:
            add(f"Only {int(runway)} days to the effective date — very late in the cycle", "negative", 1.1)

    cpl = row.get("current_cost_per_life")
    if pd.notna(cpl) and cpl >= 15_000:
        add(f"Incumbent is paying ${cpl:,.0f} per life — expensive plan to displace", "positive", 0.7)

    if pd.isna(row.get("current_max_cost")) and pd.isna(row.get("current_renewal")):
        add("No incumbent cost on file yet — less to price against", "neutral", 0.5)

    out.sort(key=lambda d: -d["severity"])
    return out[:5]


def _potential_premium(frame: pd.DataFrame) -> pd.Series:
    """Best available cost estimate for an open quote, in priority order: a
    firm quote if we have one, else the illustrative quote, else the
    incumbent's current cost as a rough proxy."""
    return frame[["firm_max_cost", "illustrative_max_cost", "current_max_cost"]].bfill(axis=1).iloc[:, 0]


def score_book(pipeline: pd.DataFrame, band_reliability: dict) -> pd.DataFrame:
    """Attaches the honest, dashboard-facing fields on top of the model's raw
    `model_score`:
      likelihood_band   High/Moderate/Low/Very Low, bucketed from model_score
      band_win_rate     the BAND's measured historical win rate (see
                        insights_nb.band_reliability) -- THIS is the headline
                        number, not model_score, because a single quote's raw
                        score isn't reliable with only ~79 historical wins to
                        calibrate against (checked directly -- see
                        train_nb.py's docstring / the 2026-08-18 calibration
                        check).
      band_n, band_wins sample size backing band_win_rate, so the dashboard can
                        show its own confidence honestly rather than implying
                        false precision
      pipeline_rank/
      pipeline_percentile  relative ordering by the raw model_score -- this
                        IS something the model is good at (ROC AUC 0.925), so
                        "ranked #4 of 220" is a claim worth making even though
                        "73% likely to win" isn't
      expected_value    potential_premium x band_win_rate -- the dollar-
                        weighted prioritization metric, and the PRIMARY sort
                        key on the dashboard (executives care more about which
                        deals matter in dollars than an abstract percentage)
      stage_sales_estimate  the exec team's own rule-of-thumb close rate for
                        this quote's CURRENT stage (see STAGE_SALES_ESTIMATE)
                        -- a separate, unvalidated business estimate, deliberately
                        NOT folded into band_win_rate or expected_value, since
                        this one is judgment, not a measured historical rate.
    """
    scored = pipeline.copy()
    scored["likelihood_band"] = scored["model_score"].apply(band)
    scored["band_win_rate"] = scored["likelihood_band"].map(lambda t: band_reliability.get(t, {}).get("rate"))
    scored["band_n"] = scored["likelihood_band"].map(lambda t: band_reliability.get(t, {}).get("n"))
    scored["band_wins"] = scored["likelihood_band"].map(lambda t: band_reliability.get(t, {}).get("wins"))
    scored["stage_sales_estimate"] = scored["stage"].map(STAGE_SALES_ESTIMATE)

    order = scored["model_score"].rank(ascending=False, method="min").astype(int)
    scored["pipeline_rank"] = order
    scored["pipeline_percentile"] = 1.0 - (order - 1) / max(len(scored) - 1, 1)

    scored["potential_premium"] = _potential_premium(scored)
    # NaN when potential_premium is unknown (most quotes this early haven't
    # gotten a cost estimate at all -- see the coverage check from 2026-08-18:
    # 0% of open quotes have a firm quote, only 5% have an illustrative one).
    # $0 would be a false claim of zero value, not "we don't know yet" --
    # same blank-over-fake rule as everywhere else in this project.
    scored["expected_value"] = scored["potential_premium"] * scored["band_win_rate"]
    return scored


def summary(pipeline: pd.DataFrame, history: pd.DataFrame) -> dict:
    tiers = pipeline["likelihood_band"].value_counts().to_dict() if "likelihood_band" in pipeline.columns else {}
    potential_premium = pipeline["potential_premium"] if "potential_premium" in pipeline.columns else _potential_premium(pipeline)
    # Expected wins/premium use the BAND's measured win rate, not the raw
    # per-quote model_score -- see score_book's docstring.
    expected_wins = float(pipeline["band_win_rate"].fillna(0).sum()) if "band_win_rate" in pipeline.columns else None
    expected_premium_won = float(pipeline["expected_value"].sum()) if "expected_value" in pipeline.columns else None
    return {
        "open_quotes": int(len(pipeline)),
        "expected_wins": expected_wins,
        "potential_premium": float(potential_premium.fillna(0).sum()),
        "expected_premium_won": expected_premium_won,
        "band_counts": {t: int(tiers.get(t, 0)) for t in BAND_ORDER},
        "alltime_win_rate": float(history["sold"].mean()) if len(history) else None,
        "alltime_quotes": int(len(history)),
        "alltime_wins": int(history["sold"].sum()) if len(history) else 0,
    }


def segments(pipeline: pd.DataFrame, history: pd.DataFrame) -> dict:
    def agg_open(frame: pd.DataFrame, key: str) -> list[dict]:
        if key not in frame.columns:
            return []
        g = frame.groupby(frame[key].fillna("Unknown"))
        rows = g.agg(open_quotes=("band_win_rate", "size"),
                      expected_wins=("band_win_rate", "sum")).reset_index()
        rows = rows.rename(columns={key: "label"}).sort_values("expected_wins", ascending=False)
        return rows.to_dict("records")

    def agg_history(frame: pd.DataFrame, key: str) -> list[dict]:
        if key not in frame.columns:
            return []
        g = frame.groupby(frame[key].fillna("Unknown"))
        rows = g.agg(quotes=("sold", "size"), wins=("sold", "sum")).reset_index()
        rows["win_rate"] = rows["wins"] / rows["quotes"]
        rows = rows.rename(columns={key: "label"}).sort_values("quotes", ascending=False)
        return rows.to_dict("records")

    return {
        "by_product_open": agg_open(pipeline, "product"),
        "by_industry_open": agg_open(pipeline, "industry"),
        "by_rsd_open": agg_open(pipeline, "rsd"),
        "by_product_history": agg_history(history, "product"),
        "by_industry_history": agg_history(history, "industry"),
        "by_rsd_history": agg_history(history, "rsd"),
        "by_broker_history": agg_history(history, "broker"),
    }


# =====================================================================
# Executive performance analytics (the Performance page)
# =====================================================================
# Everything below is measured straight off nb_history.csv -- decided quotes
# only, no model involvement at all. That separation is deliberate: this page
# answers "what did we actually do?", which must stay checkable by hand against
# the export, and must never move because a model was retrained.

# Group-size bands. Cut points sit where the measured win rate actually steps
# (5.0% / 4.1% / 4.2% / 11.0% / 11.6%), not on round numbers -- the real story
# in this book is that cases above ~500 lives win at more than double the rate
# of everything below, and a band scheme straddling 500 hides it.
SIZE_BANDS = [(0, 50, "Under 50"), (50, 150, "50-150"), (150, 500, "150-500"),
              (500, 1500, "500-1,500"), (1500, 10**9, "1,500+")]

# Below this many decided quotes a win rate is noise, not performance -- one
# lucky win on 6 quotes reads as 17%. Rows under it are still RETURNED (never
# silently dropped); they carry enough_volume=False so the page can gray them
# out while the exec can still see they exist.
MIN_VOLUME = 25


def _premium(frame: pd.DataFrame) -> pd.Series:
    """Best available annual-premium figure for a quote: the firm quote if one
    exists, else the illustrative, else the incumbent's current cost."""
    cols = [c for c in ("firm_max_cost", "illustrative_max_cost", "current_max_cost")
            if c in frame.columns]
    if not cols:
        return pd.Series(np.nan, index=frame.index)
    return frame[cols].bfill(axis=1).iloc[:, 0]


def _agg(frame: pd.DataFrame, keys: pd.Series, label_name: str = "label",
         trend: bool = False, open_years: set | None = None) -> list[dict]:
    """quotes / wins / win_rate / won-premium per group, plus the premium
    coverage behind that dollar figure.

    Premium is reported WITH its coverage, never silently as if complete: only
    82-91% of won quotes carry a cost figure in a good year and 42% in 2026, so
    a bare total would understate reality by an unstated amount. Coverage lets
    the page say "$238M across 82% of wins" instead of implying $238M is all
    of it.
    """
    prem = _premium(frame)
    # Fill the missing label BEFORE grouping, not via dropna=False: pandas
    # cannot build a categorical grouper with a null category, and an
    # "Unknown" bucket has to appear on the page anyway -- a blank RSD or
    # industry on 300 quotes is itself something an exec should see.
    keys = keys.astype(object).where(keys.notna(), "Unknown")
    eff_year = (pd.to_datetime(frame["eff_date"], errors="coerce").dt.year
                if trend and "eff_date" in frame.columns else None)
    out = []
    for label, idx in frame.groupby(keys, sort=False).groups.items():
        sub = frame.loc[idx]
        won_prem = prem.loc[sub.index[sub["sold"] == 1]]
        n, w = len(sub), int(sub["sold"].sum())
        row = {
            label_name: str(label),
            "quotes": n,
            "wins": w,
            "win_rate": (w / n) if n else None,
            "premium_won": float(won_prem.sum()) if won_prem.notna().any() else None,
            "premium_coverage": float(won_prem.notna().mean()) if w else None,
            "enough_volume": n >= MIN_VOLUME,
        }
        if eff_year is not None:
            # Every leaderboard row carries its own by-year series, not just a
            # top-N few: the sparkline column is only worth having if it's
            # populated for the rows the reader is actually looking at.
            yr = eff_year.loc[sub.index]
            pts = []
            for y, g in sub.groupby(yr):
                if pd.isna(y):
                    continue
                # in_progress years are carried, not dropped -- but flagged, so
                # the sparkline can leave them off its line while the hover
                # readout still shows them. A 2027 bucket holding 2 undecided
                # quotes would otherwise pull every single spark to zero at the
                # right edge and read as a collapse.
                pts.append({"year": int(y), "quotes": len(g), "wins": int(g["sold"].sum()),
                            "win_rate": float(g["sold"].mean()),
                            "in_progress": int(y) in (open_years or set())})
            row["trend"] = sorted(pts, key=lambda r: r["year"])
        out.append(row)
    return out


def _year_rows(frame: pd.DataFrame, date_col: str, open_years: set) -> list[dict]:
    yr = pd.to_datetime(frame[date_col], errors="coerce").dt.year
    ok = yr.notna()
    rows = _agg(frame[ok], yr[ok].astype(int), "year")
    for r in rows:
        r["year"] = int(r["year"])
        # A year is "in progress" if quotes belonging to it are still open in
        # the pipeline -- its win rate can only go UP as those decide, so
        # showing it beside finished years without saying so invites exactly
        # the wrong conclusion (that the book fell off a cliff).
        r["in_progress"] = r["year"] in open_years
    return sorted(rows, key=lambda r: r["year"])


def performance(history: pd.DataFrame, pipeline: pd.DataFrame | None = None) -> dict:
    """Everything the Performance page shows: the whole decided book sliced by
    year, RSD, broker, product, industry, state, deal size and effective month.

    Two year bases are returned on purpose, because they answer different
    questions and they disagree:
      effective year  -- which SELLING SEASON the business belongs to. This is
                         what an exec means by "how did 2025 go", and it's the
                         page default.
      created year    -- when the opportunity entered the funnel. Good for "is
                         our funnel improving", but recent years read
                         artificially weak on this basis: a quote created late
                         in a year that will be won often hasn't decided yet.
    Neither is presented as the truer one; both carry in_progress flags so a
    partial year is never mistaken for a finished one.
    """
    h = history.copy()
    h["sold"] = pd.to_numeric(h["sold"], errors="coerce").fillna(0).astype(int)

    open_eff_years, open_created_years, open_quotes = set(), set(), 0
    if pipeline is not None and len(pipeline):
        open_quotes = len(pipeline)
        open_eff_years = set(pd.to_datetime(pipeline["eff_date"], errors="coerce")
                             .dt.year.dropna().astype(int).tolist())
        open_created_years = set(pd.to_datetime(pipeline["created_date"], errors="coerce")
                                 .dt.year.dropna().astype(int).tolist())

    prem = _premium(h)
    won_prem = prem.loc[h.index[h["sold"] == 1]]

    eff_year = pd.to_datetime(h["eff_date"], errors="coerce").dt.year
    closed_years = sorted({int(y) for y in eff_year.dropna().unique()} - open_eff_years)
    latest_closed = closed_years[-1] if closed_years else None
    prior_closed = closed_years[-2] if len(closed_years) > 1 else None

    def _rate(year):
        if year is None:
            return None
        s = h[eff_year == year]
        return float(s["sold"].mean()) if len(s) else None

    lives = pd.to_numeric(h.get("lives"), errors="coerce")
    size_labels = pd.Series(pd.NA, index=h.index, dtype="object")
    for lo, hi, name in SIZE_BANDS:
        size_labels = size_labels.mask((lives > lo) & (lives <= hi), name)

    eff_month = pd.to_datetime(h["eff_date"], errors="coerce").dt.month
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    mok = eff_month.notna()
    seasonality = _agg(h[mok], eff_month[mok].astype(int).map(lambda m: months[m - 1]), "month")
    seasonality.sort(key=lambda r: months.index(r["month"]))

    return {
        "headline": {
            "quotes": int(len(h)),
            "wins": int(h["sold"].sum()),
            "win_rate": float(h["sold"].mean()) if len(h) else None,
            "premium_won": float(won_prem.sum()) if won_prem.notna().any() else None,
            "premium_coverage": float(won_prem.notna().mean()) if len(won_prem) else None,
            "open_quotes": open_quotes,
            "latest_closed_year": latest_closed,
            "latest_closed_win_rate": _rate(latest_closed),
            "prior_closed_year": prior_closed,
            "prior_closed_win_rate": _rate(prior_closed),
            "rsd_count": int(h["rsd"].nunique()),
            "broker_count": int(h["broker"].nunique()),
        },
        "by_effective_year": _year_rows(h, "eff_date", open_eff_years),
        "by_created_year": _year_rows(h, "created_date", open_created_years),
        "leaderboards": {
            "rsd": sorted(_agg(h, h["rsd"], trend=True, open_years=open_eff_years), key=lambda r: -r["quotes"]),
            "broker": sorted(_agg(h, h["broker"], trend=True, open_years=open_eff_years), key=lambda r: -r["quotes"]),
            "product": sorted(_agg(h, h["product"], trend=True, open_years=open_eff_years), key=lambda r: -r["quotes"]),
            "industry": sorted(_agg(h, h["industry"], trend=True, open_years=open_eff_years), key=lambda r: -r["quotes"]),
            "state": sorted(_agg(h, h["billing_state"], trend=True, open_years=open_eff_years), key=lambda r: -r["quotes"]),
            "size": sorted(_agg(h[size_labels.notna()], size_labels[size_labels.notna()], trend=True,
                                open_years=open_eff_years),
                           key=lambda r: [b[2] for b in SIZE_BANDS].index(r["label"])),
        },
        "seasonality": seasonality,
        "min_volume": MIN_VOLUME,
    }
