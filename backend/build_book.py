"""
Rebuild the Upcoming Renewals book from the Salesforce deals export so it contains
EVERY current-cycle renewal (decided + open), each with a model likelihood and — for
settled ones — its real outcome.

Master list  = sf_deals.csv, one row per account+renewal-month in the forecast window,
               group-level status (WON > OPEN > LOST across product variants).
Features     = joined from real_history.csv (decided groups: full underwriting) and
               real_active_book.csv (open groups: enriched), by normalized name, nearest
               month in the same cycle. Uncovered groups fall back to deal basics (model
               imputes the missing underwriting fields).
Scoring      = the production real model (models/real_latest.joblib).
Output       = data/real_scored_book.csv  (what real_mode serves).
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

SRC_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC_DIR))

from app.forward_book import forward_mask  # noqa: E402
from app.insights import AS_OF as DISPLAY_AS_OF, horizon_end  # noqa: E402
from app.paths import backend_dir  # noqa: E402
from app.real_mode import _DEAL_CYCLE_MONTHS, _canon, _deal_norm  # noqa: E402  (name normalizers)
from train_real import FEATURES, _effective_increase, apply_increase_override, apply_override, prepare  # noqa: E402

# Writable/persistent data+models root (== SRC_DIR in dev; a frozen build's per-user
# %LOCALAPPDATA%/Horizon folder, NOT the read-only exe bundle - see app/paths.py).
BACKEND = backend_dir()
DATA = BACKEND / "data"
DEALS = DATA / "sf_deals.csv"
HISTORY = DATA / "real_history.csv"
ACTIVE = DATA / "real_active_book.csv"
# In-app "Upload" feed (see upload_feed.py) - upcoming renewals a human typed into
# the template and imported. Already uses real_history.csv's own column names, so
# it needs no rename step (unlike ACTIVE below, which predates the template).
UPLOADED = DATA / "uploaded_upcoming.csv"
MODEL = BACKEND / "models" / "real_latest.joblib"
OUT = DATA / "real_scored_book.csv"

# Single source of truth: pull exactly the window app/insights.py will display, so the
# pipeline and the page can never drift apart again. No separate hardcoded dates here —
# a fixed calendar constant is exactly what silently hid Apr/May 2026 from the book.
AS_OF = DISPLAY_AS_OF
WIN_END = horizon_end(DISPLAY_AS_OF)

# underwriting/identity columns we try to carry onto each book row
FEAT_COLS = [
    "lives", "annual_premium", "premium_stoploss", "nlr", "isl_loss_ratio",
    "agg_loss_ratio", "ratio_to_attachment",
    "mature_to_attachment", "fixed_increase_pct", "total_increase_pct",
    "initial_uw_increase_pct", "lasers_current", "lasers_renewal", "laser_liability",
    "tenure_years", "corridor", "isl_deductible", "network",
    "broker_groups_with_cs", "broker_years_with_cs", "broker_products_sold",
    "broker_preferred", "bor_change", "captive_offer",
    "product", "carrier", "state", "broker", "am", "rsd", "tpa",
]


_MODEL_CACHE: dict = {}


def _load_model():
    """joblib.load(MODEL) + the n_jobs patch below, cached per process and
    invalidated on the model file's mtime (so a retrain's new file is picked
    up on the very next build()).

    Both steps here are pure overhead a single-row edit (e.g. deleting one
    Upcoming Renewals group) shouldn't have to pay every time:
      * joblib.load of this ensemble is ~1.9s of disk + unpickling — identical
        object every call until the file actually changes, so load it once.
      * the RF/ET members baked into each calibration fold carry n_jobs=-1
        from training. At inference time, on a book of a few hundred rows,
        spinning up a multiprocess (loky) pool costs far more in process
        spawn/IPC than sequential prediction saves — ~2s of pure overhead
        for work that takes tenths of a second single-threaded. Patching
        n_jobs to 1 in place changes nothing about what gets predicted (it's
        a parallelization knob, not a modeling choice) and only needs doing
        once per load, not once per predict_proba call.
    """
    mtime = MODEL.stat().st_mtime
    if _MODEL_CACHE.get("mtime") != mtime:
        payload = joblib.load(MODEL)
        clf = payload["pipeline"].named_steps.get("clf")
        for cc in getattr(clf, "calibrated_classifiers_", []):
            for sub in getattr(cc.estimator, "named_estimators_", {}).values():
                if getattr(sub, "n_jobs", None) is not None:
                    sub.n_jobs = 1
        _MODEL_CACHE["mtime"] = mtime
        _MODEL_CACHE["payload"] = payload
    return _MODEL_CACHE["payload"]


def _feature_source() -> pd.DataFrame:
    """History (decided, full UW) + uploaded feed (open, human-typed) + active book
    (open, enriched), one feature bag per row, keyed by normalized name + month.
    History wins on a tie (it's the confirmed decided record); the uploaded feed
    beats the older active-book extract when both cover the same open renewal,
    since it's the most recently human-confirmed data for that group."""
    frames = []
    if HISTORY.exists():
        h = pd.read_csv(HISTORY)
        h["_eff"] = pd.to_datetime(h["eff_date"], errors="coerce")
        h["_pri"] = 1
        frames.append(h)
    if UPLOADED.exists():
        u = pd.read_csv(UPLOADED)
        u["_eff"] = pd.to_datetime(u["eff_date"], errors="coerce")
        u["_pri"] = 1.5
        frames.append(u)
    if ACTIVE.exists():
        a = pd.read_csv(ACTIVE).rename(columns={
            "n_renewals": "tenure_years",
            "firm_increase_pct": "initial_uw_increase_pct",
            "firm_increase_w_lasers_pct": "total_increase_pct",
            "laser_count_detail": "lasers_renewal",
        })
        a["_eff"] = pd.to_datetime(a["eff_date"], errors="coerce")
        a["_pri"] = 2
        frames.append(a)
    src = pd.concat(frames, ignore_index=True, sort=False)
    src = src[src["_eff"].notna()].copy()
    src["_f"] = src["group_name"].map(_deal_norm)
    src["_c"] = src["group_name"].map(_canon)  # alias-aware fallback key
    for c in FEAT_COLS:
        if c not in src.columns:
            src[c] = np.nan
    return src


def _best_feature_row(src: pd.DataFrame, f: str, month: pd.Period, c: str | None = None):
    """Feature bag for name `f` from the same renewal cycle (nearest month within
    3 months, history preferred), else None. Falls back to the alias-aware canonical
    key `c` when the aggressive deal-normalizer misses a spelling variant (e.g.
    "Wisconsin Converting Inc" vs "Wisconsin Converting, INC. of Green Bay"), but only
    when that canon key maps to a single real group in-window, so a coarse prefix
    collision can never mis-join one company's underwriting onto another."""
    cand = src[src["_f"] == f]
    if cand.empty and c:
        cc = src[src["_c"] == c]
        if cc.empty or cc["_f"].nunique() != 1:
            return None  # no canon match, or an ambiguous prefix collision
        cc = cc.assign(_d=cc["_eff"].dt.to_period("M").apply(lambda p: abs((p - month).n)))
        cc = cc[cc["_d"] <= 3]
        return cc.sort_values(["_d", "_pri"]).iloc[0] if not cc.empty else None
    if cand.empty:
        return None
    cand = cand.assign(_d=cand["_eff"].dt.to_period("M").apply(lambda p: abs((p - month).n)))
    cand = cand[cand["_d"] <= 3]
    if cand.empty:
        return None
    return cand.sort_values(["_d", "_pri"]).iloc[0]


def enrich_from_active_book(frame: pd.DataFrame) -> pd.DataFrame:
    """Backfill blank fields in `frame` (rows about to be committed from an
    upload — see upload_feed.py) from real_active_book.csv's richer underwriting
    for the same group + renewal cycle. Additive only: never touches a value the
    frame already has, only fills genuine blanks.

    Why this has to happen at commit time, not just at build() time: a freshly
    uploaded row lands in real_history.csv or uploaded_upcoming.csv, and BOTH of
    those outrank real_active_book.csv in _feature_source's join priority
    (uploaded/decided data is trusted as the freshest human-confirmed record).
    That's correct when the upload actually has fuller data than what's on file
    — but a human upload (or a CRM export) often only carries a few fields
    (name/date/lives/broker), no loss-ratio/underwriting. Without this, that
    sparse row would still WIN the join and silently blank out real underwriting
    numbers that were already known from an Executive Log, purely because it
    ranks higher — the exact bug this function exists to prevent. See the
    2026-08-05 Renewal Business import cleanup this was written for."""
    if not ACTIVE.exists() or frame.empty:
        return frame
    if "group_name" not in frame.columns or "eff_date" not in frame.columns:
        return frame
    frame = frame.copy()
    ab = pd.read_csv(ACTIVE, parse_dates=["eff_date"]).rename(columns={
        "n_renewals": "tenure_years",
        "firm_increase_pct": "initial_uw_increase_pct",
        "firm_increase_w_lasers_pct": "total_increase_pct",
        "laser_count_detail": "lasers_renewal",
    })
    ab["_eff"] = ab["eff_date"]
    ab["_f"] = ab["group_name"].map(_deal_norm)
    ab["_c"] = ab["group_name"].map(_canon)
    ab["_pri"] = 1
    cols = [c for c in FEAT_COLS if c in ab.columns]
    for c in cols:
        if c not in frame.columns:
            frame[c] = np.nan
    for idx, row in frame.iterrows():
        name, dt = row.get("group_name"), row.get("eff_date")
        if pd.isna(name) or pd.isna(dt):
            continue
        month = pd.to_datetime(dt).to_period("M")
        src = _best_feature_row(ab, _deal_norm(name), month, _canon(name))
        if src is None:
            continue
        for c in cols:
            if pd.isna(row.get(c)) and pd.notna(src.get(c)):
                frame.at[idx, c] = src[c]
    return frame


def build() -> pd.DataFrame:
    d = pd.read_csv(DEALS)
    d["_eff"] = pd.to_datetime(d["Opportunity-Actual_Effective_Date__c"],
                               errors="coerce", utc=True).dt.tz_localize(None)
    d = d[d["_eff"].notna() & (d["_eff"] >= AS_OF) & (d["_eff"] <= WIN_END)].copy()
    d["_f"] = d["Account Name"].map(_deal_norm)
    d["_c"] = d["Account Name"].map(_canon)
    d["_m"] = d["_eff"].dt.to_period("M")
    rank = {"WON": 3, "OPEN": 2, "LOST": 1}
    d["_r"] = d["Status"].map(rank).fillna(0)
    # one representative row per account+month = the best-status opportunity
    reps = d.sort_values("_r").groupby(["_f", "_m"]).tail(1).copy()

    # --- COVERAGE BACKFILL: sf_deals.csv (the master list above) doesn't have every
    # group that's actually in the book — confirmed real gaps (e.g. "City of Kennett",
    # "Lily Hospice LLC") that exist in real_active_book.csv/real_history.csv with real
    # eff_dates but appear nowhere in sf_deals under any name-matching scheme. Add any
    # such group directly from its native source so it isn't silently dropped just
    # because Salesforce doesn't have it. sf_deals stays authoritative when it DOES have
    # the group — it's demonstrably fresher for Won/Lost status than the manually-pushed
    # real_history.csv (a deal can show LOST in sf_deals before anyone clicks "move to
    # history"). history rows are considered before active-book rows (via `_pri`) so a
    # stale active-book row left behind by that same manual-push gap doesn't get
    # backfilled as a second, duplicate "open" copy of an already-decided group.
    src = _feature_source()
    src = src[(src["_eff"] >= AS_OF) & (src["_eff"] <= WIN_END)].copy()
    src["_m"] = src["_eff"].dt.to_period("M")

    def _same_cycle(months: pd.Series, month) -> pd.Series:
        return months.apply(lambda p: abs((p - month).n)) <= _DEAL_CYCLE_MONTHS

    def _covered_by_reps(f: str, c: str, month) -> bool:
        cand = reps[reps["_f"] == f]
        if cand.empty and c:
            cand = reps[reps["_c"] == c]
        return not cand.empty and _same_cycle(cand["_m"], month).any()

    claimed: list[tuple[str, str, pd.Period]] = []

    def _already_claimed(f: str, c: str, month) -> bool:
        return any(abs((m - month).n) <= _DEAL_CYCLE_MONTHS and (f == cf or (c and c == cc))
                   for cf, cc, m in claimed)

    backfill_recs = []
    for _, r in src.sort_values("_pri").iterrows():
        f, c, m = r["_f"], r["_c"], r["_m"]
        if _covered_by_reps(f, c, m) or _already_claimed(f, c, m):
            continue
        claimed.append((f, c, m))
        backfill_recs.append(r)
    backfill = pd.DataFrame(backfill_recs)
    print(f"COVERAGE BACKFILL: {len(backfill)} renewals in real_active_book/real_history "
          f"have no sf_deals.csv row at all — added directly from their native source")

    # --- LEAK FILTER: flag any renewal whose THIS-cycle decision is already in the
    # model's training data (real_history). Scoring those is in-sample/memorized, so
    # they don't belong in a forward book. They remain in the Renewal Database.
    hist = pd.read_csv(HISTORY, parse_dates=["eff_date"])
    hist["_n"] = hist["group_name"].map(_deal_norm)
    hist["_canon"] = hist["group_name"].map(_canon)
    # Forward-book months are held out of TRAINING (see app/forward_book.py), so they
    # must NOT count as "trained on" here — otherwise their own renewals would be
    # flagged leaked and hidden from Upcoming Renewals. Exclude them from train_hist
    # so those decided renewals surface on the page as a leak-free forward test.
    train_hist = hist[~forward_mask(hist["eff_date"])]
    # A renewal is annual — a book row counts as "already decided" only if a training
    # row for the SAME group falls in the SAME renewal cycle (within
    # _DEAL_CYCLE_MONTHS of its eff_date), not just the same calendar year. Match by
    # _deal_norm OR _canon (name spellings vary across sources — e.g. "Cooperative
    # Educational Service Agency 8" vs real_history's "...No CESA 8" only share their
    # first ~20 chars, which _canon's key captures and _deal_norm doesn't) — confirmed
    # 3 such leaks slipping through _deal_norm alone on 2026-07-21. A plain same-YEAR
    # check (the previous logic) had the opposite bug: it mis-flagged a genuinely open
    # Nov-2026 renewal as leaked just because that same group's Jan-2026 renewal (a
    # DIFFERENT decision) was already decided.
    by_norm: dict = {}
    by_canon: dict = {}
    for _, hr in train_hist.iterrows():
        by_norm.setdefault(hr["_n"], []).append(hr["eff_date"])
        by_canon.setdefault(hr["_canon"], []).append(hr["eff_date"])

    def _is_leaked(f: str, c: str, eff: pd.Timestamp) -> bool:
        dates = by_norm.get(f, []) + by_canon.get(c, [])
        return any(abs((eff.to_period("M") - d.to_period("M")).n) <= _DEAL_CYCLE_MONTHS
                   for d in dates)

    # FLAG (don't drop) renewals whose same-cycle decision is in training. The Upcoming
    # Renewals page filters these out (leak-free model view); the Renewal Forecast
    # outlook keeps them so it shows the full business cycle (e.g. July = 33, not 8).
    reps["_leaked"] = reps.apply(
        lambda r: _is_leaked(r["_f"], _canon(r["Account Name"]), r["_eff"]), axis=1)
    # Exception: a group the user explicitly moved with the "Move to Renewal Database"
    # button (source == manual_transfer) stays excluded from Upcoming Renewals even if
    # its decision falls in the forward-book window — that click is a deliberate,
    # per-group override of the default leak-free-forward-test display, and a retrain
    # shouldn't silently undo it.
    manual = hist[hist["source"] == "manual_transfer"]
    manual_keys = set(zip(manual["_n"], pd.to_datetime(manual["eff_date"]).dt.to_period("M")))
    reps["_leaked"] = reps["_leaked"] | [
        (f, m) in manual_keys for f, m in zip(reps["_f"], reps["_eff"].dt.to_period("M"))
    ]
    if len(backfill):
        # backfilled-from-history rows are, by definition, training data (leaked=True
        # unless held out via forward_book.py); backfilled-from-active-book rows are
        # still open (leaked=False) unless already manually pushed to history too.
        backfill["_leaked"] = backfill.apply(
            lambda r: _is_leaked(r["_f"], r["_c"], r["_eff"]), axis=1)
        backfill["_leaked"] = backfill["_leaked"] | [
            (f, m) in manual_keys for f, m in zip(backfill["_f"], backfill["_eff"].dt.to_period("M"))
        ]
    print(f"LEAK FLAG: {int(reps['_leaked'].sum())} of {len(reps)} renewals have their "
          f"2026 decision in training (flagged out of Upcoming Renewals, kept in outlook)")

    outcome_label = {"WON": "Renewed", "LOST": "Termed", "OPEN": None}

    def _num(v):
        v = pd.to_numeric(v, errors="coerce")
        return float(v) if pd.notna(v) else np.nan

    rows = []
    for _, r in reps.iterrows():
        fr = _best_feature_row(src, r["_f"], r["_m"], _canon(r["Account Name"]))
        row = {c: (fr[c] if fr is not None and c in fr and pd.notna(fr[c]) else np.nan)
               for c in FEAT_COLS}
        # identity / basics always from the deal (authoritative & current)
        row["group_name"] = str(r["Account Name"]).strip()
        row["eff_date"] = r["_eff"]
        row["actual_outcome"] = outcome_label.get(str(r["Status"]))
        row["deal_status"] = str(r["Status"])
        row["leaked"] = bool(r["_leaked"])
        # fill basics from the deal where the feature join had nothing
        if pd.isna(row["lives"]):
            row["lives"] = _num(r.get("Opportunity-No_of_Members__c")) or \
                _num(r.get("Opportunity-No_of_Employees__c"))
        slp = _num(r.get("Opportunity-RUS_ISL_Premium__c"))
        if pd.isna(row["premium_stoploss"]) and pd.notna(slp):
            row["premium_stoploss"] = slp
        if pd.isna(row["annual_premium"]) and pd.notna(slp):
            row["annual_premium"] = slp
        # last resort: when there's no separate total-renewal premium, surface the
        # stop-loss premium as the displayed premium (mirrors how most rows already
        # carry annual_premium == premium_stoploss). Real number, not an estimate.
        if pd.isna(row["annual_premium"]) and pd.notna(row["premium_stoploss"]):
            row["annual_premium"] = row["premium_stoploss"]
        if pd.isna(row["product"]) or row["product"] in (None, "", np.nan):
            row["product"] = r.get("Opportunity-Type_of_Quote_RequestedFFG__c")
        rows.append(row)

    # backfilled rows already carry their own real feature columns (no fuzzy
    # name+month join needed — they ARE the feature row) and no Opportunity-* deal
    # fields to fall back on, so they skip the deal-specific fill-ins above.
    for _, r in backfill.iterrows():
        row = {c: (r[c] if c in r and pd.notna(r[c]) else np.nan) for c in FEAT_COLS}
        row["group_name"] = str(r["group_name"]).strip()
        row["eff_date"] = r["_eff"]
        row["leaked"] = bool(r["_leaked"])
        if r["_pri"] == 1:  # sourced from real_history.csv — already decided
            renewed = r.get("renewed")
            if pd.isna(renewed):
                continue  # defensive: history should never carry an undecided row
            row["actual_outcome"] = "Renewed" if float(renewed) == 1 else "Termed"
            row["deal_status"] = "HISTORY"
        else:  # sourced from real_active_book.csv or an uploaded feed — open, no sf_deals coverage
            row["actual_outcome"] = None
            row["deal_status"] = "UPLOADED" if r["_pri"] == 1.5 else "ACTIVE_BOOK"
        rows.append(row)

    book = pd.DataFrame(rows)
    # Line-of-business overrides (data/lob_overrides.csv): human-classified `product`
    # for groups the feature join couldn't type (e.g. deal name "ACS North LLC DBA…"
    # doesn't match history "ACS North"). Keyed by the book's own group_name, so it
    # always lands. Drives the LOB shown on Overview/Book via real_mode._lob.
    lob_path = DATA / "lob_overrides.csv"
    if lob_path.exists():
        ov = pd.read_csv(lob_path)
        omap = {_deal_norm(n): p for n, p in zip(ov["group_name"], ov["product"])}
        book["product"] = [omap.get(_deal_norm(n), p)
                           for n, p in zip(book["group_name"], book["product"])]

    payload = _load_model()
    scored = prepare(book, payload.get("category_map"))
    raw_p = payload["pipeline"].predict_proba(scored[FEATURES])[:, 1]
    # Loss-ratio + renewal-increase overrides: below the real breakpoint the full
    # model stands; above it, blended down to a robust univariate P(renew | ...)
    # curve, so by the extreme end the served number IS that curve — tenure/broker/
    # etc. can no longer pull a catastrophic account's number back up. See train_real.py.
    p = apply_override(raw_p, scored["nlr"], payload["nlr_curve"])
    p = apply_increase_override(p, _effective_increase(scored), payload["increase_curve"])
    scored["renewal_probability"] = np.round(p, 4)
    # prepare() collapses categoricals (product/carrier/state) to the model's frozen
    # vocabulary, mapping anything unseen to "Other" — great for scoring, but it
    # destroys the human values the UI shows (e.g. line-of-business is derived from
    # `product`). Restore the RAW identity columns for display; the model already
    # scored on the collapsed ones; the recommender re-prepares on demand when it needs them.
    for col in ("product", "carrier", "state"):
        scored[col] = book[col].to_numpy()
    scored = scored.sort_values("renewal_probability").reset_index(drop=True)

    # Manual exclusions (data/excluded_groups.csv): a renewal row a user explicitly
    # removed from Upcoming Renewals / Needs Data - entered in error, a duplicate,
    # shouldn't be tracked. Persisted here so it stays gone across every rebuild,
    # unlike removing it from real_scored_book.csv directly (this function
    # regenerates that file from scratch every run, so a direct edit wouldn't
    # survive the next build). Scoped to the specific renewal cycle (name + same-
    # cycle month), NOT the whole company, and NEVER touches real_history.csv - a
    # bad current-cycle row can be removed without erasing that company's actual
    # past decisions.
    excl_path = DATA / "excluded_groups.csv"
    if excl_path.exists():
        excl = pd.read_csv(excl_path)
        if len(excl):
            excl_f = excl["group_name"].map(_deal_norm)
            excl_m = pd.to_datetime(excl["eff_date"], errors="coerce").dt.to_period("M")
            excl_keys = set(zip(excl_f, excl_m))
            scored_f = scored["group_name"].map(_deal_norm)
            scored_m = scored["eff_date"].dt.to_period("M")
            mask = pd.Series(list(zip(scored_f, scored_m)), index=scored.index).isin(excl_keys)
            if mask.any():
                print(f"MANUAL EXCLUSIONS: {int(mask.sum())} renewal(s) removed "
                      f"per data/excluded_groups.csv")
            scored = scored[~mask].reset_index(drop=True)

    scored.to_csv(OUT, index=False)

    n = len(scored)
    dec = scored["actual_outcome"].isin(["Renewed", "Termed"]).sum()
    feat = scored["nlr"].notna().sum()
    print(f"BOOK REBUILT FROM DEALS: {n} renewals -> {OUT.name}")
    print(f"  decided (with real outcome): {dec}  | open: {n - dec}")
    print(f"  underwriting joined (nlr present): {feat}/{n}")
    print(f"  by month:\n{scored['eff_date'].dt.to_period('M').astype(str).value_counts().sort_index().to_string()}")
    return scored


if __name__ == "__main__":
    build()
