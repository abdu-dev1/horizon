"""
Real-data mode for the Horizon API.

Loads the real production model (models/real_latest.joblib) and the scored
real active book, adapts them to the display schema the dashboard expects,
and builds the full app state (summary / forecast / segments / groups).
"""

from __future__ import annotations

import difflib
import functools
import re
import sys

import numpy as np
import pandas as pd

from . import insights
from .model import load_registry
from .paths import backend_dir

BACKEND_DIR = backend_dir()
sys.path.insert(0, str(BACKEND_DIR))
from train_real import LABELS, _effective_increase, apply_increase_override, apply_override  # noqa: E402
REAL_MODEL_PATH = BACKEND_DIR / "models" / "real_latest.joblib"
REAL_HISTORY = BACKEND_DIR / "data" / "real_history.csv"
REAL_SCORED = BACKEND_DIR / "data" / "real_scored_book.csv"
PEOPLE_ALIASES = BACKEND_DIR / "data" / "people_aliases.csv"
# Salesforce "Account Management Renewals" deals export — the authoritative, CURRENT
# renewal outcomes (Closed Won / Closed Lost / open). Drives the Actual Outcome column.
REAL_DEALS = BACKEND_DIR / "data" / "sf_deals.csv"
# Manual, user-confirmed outcomes that OVERRIDE the deals export — for cases where the
# AM knows the real decision but Salesforce's opp stage hasn't caught up (e.g. a group
# terming while its base renewal opp still shows an open Quote Request). Highest priority.
REAL_OVERRIDES = BACKEND_DIR / "data" / "outcome_overrides.csv"

_DEALS_SUFFIXES = re.compile(
    r"\b(incorporated|inc|llc|llp|pllc|ltd|co|corp|corporation|company|pc|pa|"
    r"group|holdings|dba|the|of|and)\b"
)


def _deal_norm(s) -> str:
    """Aggressive normalization for matching book groups to SF account names."""
    s = str(s).lower().replace("&", " and ")
    s = re.sub(r"\(.*?\)", " ", s)          # drop parenthetical aliases
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = _DEALS_SUFFIXES.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


# A renewal is annual, so a deal only describes a book renewal if their effective dates
# are the SAME cycle. Match within this many months; anything further (e.g. last year's
# Jan renewal vs this year's Jan renewal, 12 months apart) is a DIFFERENT renewal.
_DEAL_CYCLE_MONTHS = 2


@functools.lru_cache(maxsize=1)
def _overrides() -> dict:
    """{normalized name -> 'Renewed'/'Termed'} from data/outcome_overrides.csv."""
    if not REAL_OVERRIDES.exists():
        return {}
    o = pd.read_csv(REAL_OVERRIDES)
    if "group_name" not in o.columns or "outcome" not in o.columns:
        return {}
    norm = {"won": "Renewed", "renewed": "Renewed", "renew": "Renewed",
            "lost": "Termed", "termed": "Termed", "term": "Termed"}
    out = {}
    for _, r in o.iterrows():
        v = norm.get(str(r["outcome"]).strip().lower())
        if v and str(r["group_name"]).strip():
            out[_deal_norm(r["group_name"])] = v
    return out


def _override_outcome(name) -> str | None:
    m = _overrides()
    if not m:
        return None
    f = _deal_norm(name)
    if not f:
        return None
    if f in m:
        return m[f]
    ft = set(f.split())
    for k, v in m.items():
        kt = set(k.split())
        if ft and kt and (ft <= kt or kt <= ft) and len(ft & kt) >= 2:
            return v
    cm = difflib.get_close_matches(f, list(m), n=1, cutoff=0.88)
    return m[cm[0]] if cm else None


@functools.lru_cache(maxsize=1)
def _deals_by_name() -> dict:
    """{normalized account name -> [(effective Period, 'Renewed'/'Termed'/'Open'), ...]}
    from the SF deals export. Each opportunity is dated by its actual effective date, so
    a group's 2026 renewal is never confused with its 2027 one. Product variants in the
    same month collapse to the best status (WON > OPEN > LOST) -> one Closed Won = renewed."""
    if not REAL_DEALS.exists():
        return {}
    d = pd.read_csv(REAL_DEALS)
    if "Status" not in d.columns or "Account Name" not in d.columns:
        return {}
    d["_eff"] = pd.to_datetime(d.get("Opportunity-Actual_Effective_Date__c"), errors="coerce")
    d = d[d["_eff"].notna()].copy()
    d["_f"] = d["Account Name"].map(_deal_norm)
    d["_p"] = d["_eff"].dt.to_period("M")
    rank = {"WON": 3, "OPEN": 2, "LOST": 1}
    d["_r"] = d["Status"].map(rank).fillna(0)
    d = d[d["_f"] != ""].sort_values("_r").groupby(["_f", "_p"]).tail(1)
    label = {"WON": "Renewed", "LOST": "Termed", "OPEN": "Open"}
    out: dict = {}
    for _, r in d.iterrows():
        out.setdefault(r["_f"], []).append((r["_p"], label.get(str(r["Status"]))))
    return out


def _deal_outcome(name, eff) -> str | None:
    """Real outcome for THIS renewal (same group, same renewal cycle) from the SF deals
    export. Name match: exact -> token-subset -> close-match; then the deal's effective
    month must be within `_DEAL_CYCLE_MONTHS` of the book renewal (rejects prior/next
    year). Returns None when there's no confident, same-cycle match — never a guess."""
    by_name = _deals_by_name()
    if not by_name:
        return None
    bp = pd.to_datetime(eff, errors="coerce")
    if pd.isna(bp):
        return None
    bp = bp.to_period("M")
    f = _deal_norm(name)
    if not f:
        return None
    cand = None
    if f in by_name:
        cand = f
    else:
        ft = set(f.split())
        for k in by_name:
            kt = set(k.split())
            if ft and kt and (ft <= kt or kt <= ft) and len(ft & kt) >= 2:
                cand = k
                break
        if cand is None:
            cm = difflib.get_close_matches(f, list(by_name), n=1, cutoff=0.9)
            cand = cm[0] if cm else None
    if cand is None:
        return None
    # pick the deal in the same renewal cycle (closest effective month within window)
    best = None
    for per, outcome in by_name[cand]:
        diff = abs((per - bp).n)
        if diff <= _DEAL_CYCLE_MONTHS and (best is None or diff < best[0]):
            best = (diff, outcome)
    return best[1] if best else None


def available() -> bool:
    return REAL_MODEL_PATH.exists() and REAL_SCORED.exists()


def loss_ratio_status(nlr) -> str | None:
    """Neutral, display-only loss-ratio flag shown beside the score so a low renewal
    likelihood is self-explaining. A hot group's non-renewal can be EITHER the group
    leaving on its own OR the renewal not being offered — this labels the loss-ratio
    risk, not who ends the relationship. None when loss ratio isn't loaded."""
    if nlr is None or (isinstance(nlr, float) and np.isnan(nlr)):
        return None
    if nlr >= 1.5:
        return "Severe"      # 150%+ — deep underwriting loss
    if nlr >= 1.0:
        return "Running hot"  # 100-150%
    if nlr >= 0.85:
        return "Elevated"     # 85-100%
    return "Healthy"



# --------------------------------------------------------------------------
# People-name aliases (rsd / am)
#
# Both columns are free text typed by hand over years, so ONE person shows up
# as "Scott" (117 rows), "Scott B" (14) and "Scott Brendamour" (53). Left
# as-is that fragments every per-person view: on Overview's retention charts
# all eight of the worst-retention Account Managers were first-name fragments
# of people who appear again, healthier, further down -- a reader would
# conclude someone is failing when their real book renews fine.
#
# This is a DISPLAY-level fix, applied in _display_frame() only. It is safe to
# do here precisely because neither column is a model feature (train_real.py's
# CATEGORICAL is product/carrier/state), so no alias can shift a prediction or
# oblige a retrain -- it only changes how rows are attributed and grouped.
#
# data/people_aliases.csv is hand-reviewable and locally owned (it is in
# bundle.DELTA_FILES, so a published bundle can never overwrite it). Columns:
#   field     "rsd" or "am"
#   alias     the raw value as typed
#   canonical the name it should be counted as
#   note      why, for the human reading the file later
# Only mechanical cases belong in it: a bare first name or an initialled form
# ("Scott B") that matches exactly one full name in the same column, or a
# one-letter surname misspelling. Cases needing a human judgment about whether
# two names are one person -- nicknames ("Lou Moeller" vs "Louis Moeller"),
# or two different surnames sharing a first name ("Fiona Allen" vs "Fiona
# Burnett") -- are deliberately NOT in it and stay split until someone decides.
# Entries naming two people ("Alyssa Warsh / Buzz Hannum") are left alone too:
# that is a data-entry question, not a spelling one.
# --------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _people_aliases() -> dict[tuple[str, str], str]:
    """{(field, alias_lower): canonical}. Cached; the file only changes when a
    human edits it, and the process is restarted for any data change anyway."""
    if not PEOPLE_ALIASES.exists():
        return {}
    try:
        df = pd.read_csv(PEOPLE_ALIASES, dtype=str).fillna("")
    except (OSError, pd.errors.ParserError):
        return {}
    out = {}
    for _, r in df.iterrows():
        field, alias, canon = (str(r.get(c, "")).strip() for c in ("field", "alias", "canonical"))
        if field and alias and canon:
            out[(field, alias.lower())] = canon
    return out


def _person(field: str, value) -> str:
    """One rsd/am value, resolved through the alias table. Blank stays blank --
    an unattributed renewal is a real thing an exec should see, not something
    to fold into someone's book."""
    if value is None or (isinstance(value, float) and pd.isna(value)) or pd.isna(value):
        return "—"
    raw = str(value).strip()
    if not raw:
        return "—"
    return _people_aliases().get((field, raw.lower()), raw)


def _lob(product) -> str:
    s = str(product or "").lower()
    # Checked before every other bucket: a captive product's raw name often also
    # contains "stop loss" (e.g. "Open Captive (Stop Loss with CS PBM)") or "self"
    # (e.g. a self-funded captive arrangement), so checking those buckets first
    # would misfile it there instead. This was silently happening -- Overview's
    # "Retention by Line of Business" had no Captive bucket at all, so those rows
    # fell into "Stop Loss Only" or, when no other keyword matched, the catch-all
    # "HPS (unspecified)" -- even though the raw `product` value the Needs Data
    # page shows for those same rows says "Captive" plainly.
    if "captive" in s:
        return "Captive"
    if "level" in s or s.strip() in ("lf",):
        return "HPS Level Funded"
    if any(t in s for t in ("traditional", "self", "payg", "sf")):
        return "HPS Self Funded"
    if "stop loss" in s or "stop-loss" in s or s.strip() in ("sl", "slo"):
        return "Stop Loss Only"
    return "HPS (unspecified)"


def _broker_quality(row):
    """Display-only 1-5 proxy from the broker relationship attributes.
    Returns None when we have no real basis — we don't invent a rating."""
    if row.get("broker_preferred") == 1:
        return 5
    years = row.get("broker_years_with_cs")
    if pd.notna(years):
        return int(np.clip(round(2 + years / 2), 2, 5))
    return None


def _display_frame(scored: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    # premium per life from real history, for groups missing premium
    h = history.dropna(subset=["annual_premium", "lives"])
    h = h[(h["lives"] > 0) & (h["annual_premium"] > 0)]
    ppl = float((h["annual_premium"] / h["lives"]).median()) if len(h) else 9000.0

    # real recorded outcome for THIS exact renewal (same group + same renewal month),
    # so we compare the model's likelihood against what actually happened. Keyed on
    # (canonical name, eff month) to avoid matching a prior year's decision.
    hd = history[history["renewed"].notna()].copy()
    hd["_km"] = list(zip(hd["group_name"].map(_canon),
                         pd.to_datetime(hd["eff_date"]).dt.to_period("M")))
    outcome_map = dict(zip(hd["_km"], hd["renewed"]))

    def _actual(name, eff):
        # 1) user's won/lost screenshots (outcome_overrides.csv) win. 2) fall back to the
        # Renewal Database (real_history) for THIS renewal cycle (same group + month) — so
        # the outlook's confirmed layer is complete. Leak-free page groups aren't in
        # history, so they only ever get an override or blank. Never the deals status.
        ov = _override_outcome(name)
        if ov in ("Renewed", "Termed"):
            return ov
        ren = outcome_map.get((_canon(name), pd.to_datetime(eff).to_period("M")))
        if ren is None or pd.isna(ren):
            return None
        return "Renewed" if float(ren) == 1 else "Termed"

    rows = []
    for i, r in scored.reset_index(drop=True).iterrows():
        lives = r.get("lives") if pd.notna(r.get("lives")) else 50
        # Honesty rule: never show a fabricated value as if it were real. If a field
        # wasn't actually loaded for this group, leave it blank (NaN -> null/"—") so it
        # reads as "to be filled", not "confirmed". Only lives/state/broker/carrier are
        # real for pipeline-list groups; premium/tenure/loss ratio/rate are not.
        raw_premium = r.get("annual_premium")
        premium_estimated = pd.isna(raw_premium) or raw_premium <= 0
        premium = np.nan if premium_estimated else float(raw_premium)  # blank, don't estimate
        nlr = r.get("nlr")
        loss_ratio_known = bool(pd.notna(nlr))
        loss_ratio = float(np.clip(nlr, 0, 3)) if loss_ratio_known else np.nan
        tenure_known = bool(pd.notna(r.get("tenure_years")))
        confirmed = bool(r.get("confirmed", True))
        # "underwriting" = real loss-ratio/premium loaded (from a UW/exec block);
        # "estimate" = pipeline-list group with only name/lives/team and no UW yet.
        data_basis = "underwriting" if loss_ratio_known else "estimate"
        rate_inc = r.get("total_increase_pct")
        if pd.isna(rate_inc):
            rate_inc = r.get("initial_uw_increase_pct")
        rate_val = round(float(rate_inc), 1) if pd.notna(rate_inc) else np.nan  # blank when not quoted
        lasers = r.get("lasers_renewal") or 0
        actual = _actual(r["group_name"], r["eff_date"])
        rows.append({
            # Stable per-company id (build_state assigns it via get_or_assign_group_id
            # before this function runs); fallback only guards a row that somehow
            # slipped through without one.
            "group_id": r["group_id"] if pd.notna(r.get("group_id")) and r.get("group_id") else get_or_assign_group_id(r["group_name"]),
            "group_name": r["group_name"],
            "line_of_business": _lob(r.get("product")),
            "industry": "—",
            "state": r.get("state") if pd.notna(r.get("state")) and str(r.get("state")) != "Unknown" else "—",
            "group_size": int(lives),
            "annual_premium": round(float(premium), 0) if not premium_estimated else np.nan,
            "inception_date": None,
            "renewal_date": pd.to_datetime(r["eff_date"]),
            "tenure_years": int(r.get("tenure_years")) if tenure_known else np.nan,
            "broker_name": r.get("broker") if pd.notna(r.get("broker")) else "Unknown",
            "broker_quality": _broker_quality(r),
            # tri-state: True / False / None(unknown) — absence of data isn't "No"
            "recent_bor": (None if pd.isna(r.get("bor_change"))
                           else bool(r.get("bor_change") in (1, 1.0, True, "True"))),
            "rsd": _person("rsd", r.get("rsd")),
            "am": _person("am", r.get("am")),
            "carrier": r.get("carrier") if pd.notna(r.get("carrier")) else None,
            "tpa": r.get("tpa") if pd.notna(r.get("tpa")) else None,
            "corridor": float(r["corridor"]) if pd.notna(r.get("corridor")) else None,
            "premium_stoploss": float(r["premium_stoploss"]) if pd.notna(r.get("premium_stoploss")) else None,
            "loss_ratio": round(loss_ratio, 3) if loss_ratio_known else np.nan,
            "loss_ratio_prior": round(loss_ratio, 3) if loss_ratio_known else np.nan,
            "loss_ratio_status": loss_ratio_status(loss_ratio if loss_ratio_known else np.nan),
            "agg_loss_ratio": round(float(r["agg_loss_ratio"]), 3) if pd.notna(r.get("agg_loss_ratio")) else np.nan,
            "ratio_to_attachment": round(float(r["ratio_to_attachment"]), 3) if pd.notna(r.get("ratio_to_attachment")) else np.nan,
            "large_claim_flag": bool((lasers or 0) > 0),
            "laser_at_initial_sale": False,
            "laser_at_renewal": bool((lasers or 0) > 0),
            "rate_increase_pct": rate_val,
            "uw_rtm_initial": np.nan,  # real data has no initial RTM — show n/a, don't fake 1.00
            "service_escalations": 0,
            "plan_changes": 0,
            "renewal_probability": float(r["renewal_probability"]),
            "actual_outcome": actual,
            # Same-year-in-training vs. genuine leak-free forward test (Jul/Aug 2026
            # holdout) is nuanced logic build_book.py already gets right from sf_deals +
            # forward_book.py. Trust its "leaked" column rather than re-deriving a
            # cruder version here (tried: "decided => leaked" wrongly hid the forward
            # test window too, collapsing Upcoming Renewals from ~100 to 45).
            "leaked": bool(r.get("leaked", False)),
            "confirmed": confirmed,
            "premium_estimated": bool(premium_estimated),
            "loss_ratio_known": loss_ratio_known,
            "tenure_known": tenure_known,
            # group_size above defaults unknown lives to 50 for display/scoring - a
            # consumer that would WRITE group_size back out somewhere (e.g. the
            # upload-template prefill) needs to know that 50 might not be real.
            "lives_known": bool(pd.notna(r.get("lives"))),
            "data_basis": data_basis,
            # The rest of the model's raw UW block, exposed as-is (same canonical
            # names as upload_feed.py) so the Needs Data grid can show every field
            # the model actually uses, not just the handful already surfaced above
            # for display. Not reused elsewhere - purely for the editable grid.
            "product": r.get("product") if pd.notna(r.get("product")) else None,
            "isl_loss_ratio": float(r["isl_loss_ratio"]) if pd.notna(r.get("isl_loss_ratio")) else None,
            "mature_to_attachment": float(r["mature_to_attachment"]) if pd.notna(r.get("mature_to_attachment")) else None,
            "initial_uw_increase_pct": float(r["initial_uw_increase_pct"]) if pd.notna(r.get("initial_uw_increase_pct")) else None,
            "fixed_increase_pct": float(r["fixed_increase_pct"]) if pd.notna(r.get("fixed_increase_pct")) else None,
            "lasers_current": float(r["lasers_current"]) if pd.notna(r.get("lasers_current")) else None,
            "lasers_renewal_count": float(r["lasers_renewal"]) if pd.notna(r.get("lasers_renewal")) else None,
            "laser_liability": float(r["laser_liability"]) if pd.notna(r.get("laser_liability")) else None,
            "captive_offer": (None if pd.isna(r.get("captive_offer"))
                              else bool(r.get("captive_offer") in (1, 1.0, True, "True"))),
            "network": r.get("network") if pd.notna(r.get("network")) else None,
            "broker_years_with_cs": float(r["broker_years_with_cs"]) if pd.notna(r.get("broker_years_with_cs")) else None,
            "broker_groups_with_cs": float(r["broker_groups_with_cs"]) if pd.notna(r.get("broker_groups_with_cs")) else None,
            "broker_products_sold": float(r["broker_products_sold"]) if pd.notna(r.get("broker_products_sold")) else None,
            "broker_preferred": (None if pd.isna(r.get("broker_preferred"))
                                 else bool(r.get("broker_preferred") in (1, 1.0, True, "True"))),
        })
    df = pd.DataFrame(rows)
    df["risk_tier"] = df["renewal_probability"].apply(insights.tier)
    df["premium_at_risk"] = np.round(df["annual_premium"] * (1 - df["renewal_probability"]), 0)
    return df


def _norm_key(name) -> str:
    import sys
    sys.path.insert(0, str(BACKEND_DIR))
    from etl_real import key
    return key(name)


# Same group, different spelling across source files. The ETL matches on a 20-char
# normalized prefix, which misses these — so decided renewals were leaking into the
# "open" book under an alternate name. Map each variant's prefix-key to the canonical
# one so the outlook counts every group ONCE with its real decided/open status.
_CANON_ALIASES = {
    "bawac new dba proudw": "bawac",                  # "Bawac dba Proudworks" == "Bawac, Inc."
    "environmental monito": "sterling labs",          # "Environmental Monitoring... (Sterling Labs)"
    "school district of a": "ashland school distr",   # "School District of Ashland" == "Ashland School District"
    "combined nsdt ashlan": "northern school dist",   # combo of two already-decided groups
    "hartford union high": "hartford union schoo",    # "Hartford Union High School District"
    "standard forwarding": "standard forward fre",    # "Standard Forwarding Freight" == "Standard Forward Freight"
    "acs north dba advanc": "acs north",              # "ACS North LLC DBA Advanced Concrete Systems" == "ACS North"
    "germain auto": "blue grass auto germ",           # "Germain Auto Company (Blue Grass Automotive)" == "Blue Grass Auto - Germain Motor"
    "ckf addiction treatm": "ckf addiciton treatm",   # "CKF Addiction Treatment" == "CKF Addiciton Treatment" (typo in source)
    "le norman management": "lenorman management",    # "Le Norman Management LLC" == sf_deals' "LeNorman Management" (already LOST there)
    "bawac proundworks": "bawac",                     # "Bawac, Inc. Proundworks" (typo) == "Bawac, Inc. NEW: dba Proudworks" == "bawac"
    "kingman county": "kingman county kansa",         # "Kingman County" == sf_deals' "Kingman County, Kansas"
    "howland township": "howland township tru",       # "Howland Township" == sf_deals' "Howland Township Trustees"
    "dugger investments": "dugger investments d",     # "Dugger Investments Company, Inc." == sf_deals' "...DBA Haven Building Products"
    "wayne highlands scho": "wayne highlands area",    # "Wayne Highlands School District" == sf_deals' "Wayne Highlands Area School District"
    "capstone health": "capstone health dba",         # bare name == sf_deals' "Capstone Health LLC DBA Capstone Vital Care"
    "air around the clock": "around the clock ac",    # "Air Around The Clock" == sf_deals' "Around the Clock AC Service" (same eff_date/outcome)
    "flood and peterson i": "flood and peterson",     # "Flood and Peterson Insurance, Inc" == sf_deals' "Flood & Peterson" (same eff_date/outcome)
    "capital otolaryngolo": "capital ent",            # "Capital Otolaryngology Head and Neck Surgeons, PA" == sf_deals' "Capital ENT" (same eff_date/broker/AM)
    "lloyd and mcdaniel": "lloyd and mcdaniel p",     # "Lloyd & McDaniel" == sf_deals' "Lloyd & McDaniel, PLC" (same eff_date, 126 lives both sides)
    "montgomery kansas": "montgomery county",         # "Montgomery Co. Kansas" == "Montgomery County" (same eff_date/broker, dup within real_history itself)
    "element medical bill": "elements medical bil",   # "Element Medical Billing" == sf_deals' "Elements Medical Billing LLC" (same eff_date, ~46-49 lives)
    "ericson manufacturin": "the ericson manufact", # "Ericson Manufacturing Co." == "The Ericson Manufacturing Company" (2027-01, 60 lives, USI-Cleveland)
    "revolucion holding d": "revolucion holding",   # "Revolucion Holding, LLC. DBA Condado Tacos" == "Revolucion Holding, LLC" (2027-01, 205 lives)
    "swve management dba": "swve management hold",  # "SWVE Management DBA Southwind Enterprises" == "SWVE Management Holdco LLC" (2027-01, 527 lives)
    "valley nissan": "valley nissan subaru",         # bare name == "Valley Nissan Subaru, LLC" (2027-01, 383 lives)
    "schaeffer brush": "schaefer brush manuf",      # duplicate row within real_history.csv itself (22 lives/R&R Insurance, both sides, 2026-05-01)
    "environmental and sa": "essi",                 # "Environmental & Safety System International. Inc." == sf_deals'/history's "ESSI Corporation" (same eff_date/broker/AM/TPA/state)
}


def _canon(name) -> str:
    """Canonical match key = ETL prefix-key, then collapse known spelling variants."""
    # rstrip: key() truncates to 20 chars and can leave a trailing space
    # ("standard forwarding "), which made trailing-space-free alias keys
    # ("standard forwarding") silently never fire. Both sides route through
    # _canon, so stripping stays consistent.
    k = _norm_key(name).rstrip()
    return _CANON_ALIASES.get(k, k)


# A stable, permanent ID for every unique GROUP (company) in the whole project —
# not to be confused with the display sequence numbers used elsewhere ("R0042" on
# Upcoming Renewals, "H0042" in the Renewal Database), which are just row
# positions re-assigned every time a book gets re-sorted or rebuilt. This ID is
# the same for a given company FOREVER: across retrains, across rebuilds, across
# every year that company renews, and on every page that shows it (Upcoming
# Renewals, Needs Data, the Renewal Database). Keyed on the SAME canonical
# identity (_canon, above) the rest of this pipeline already uses to decide "is
# this the same group" — leak detection, outcome matching, the alias table — so
# assigning IDs can never disagree with matching that's already tuned; it's a
# permanent label on top of that key, not a second matching scheme.
GROUP_REGISTRY = BACKEND_DIR / "data" / "group_registry.csv"

_registry: dict[str, str] | None = None
_registry_next = 1


def _load_registry() -> dict[str, str]:
    global _registry, _registry_next
    if _registry is not None:
        return _registry
    _registry = {}
    if GROUP_REGISTRY.exists():
        df = pd.read_csv(GROUP_REGISTRY, dtype=str)
        for _, r in df.iterrows():
            _registry[r["canonical_key"]] = r["group_id"]
    _registry_next = 1 + max(
        (int(v[1:]) for v in _registry.values() if v and v[1:].isdigit()), default=0)
    return _registry


def _append_registry_row(gid: str, key: str, display_name: str) -> None:
    import csv
    from datetime import datetime, timezone
    GROUP_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    new_file = not GROUP_REGISTRY.exists()
    with open(GROUP_REGISTRY, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["group_id", "canonical_key", "display_name", "first_seen"])
        w.writerow([gid, key, display_name,
                    datetime.now(timezone.utc).isoformat(timespec="seconds")])


def get_or_assign_group_id(name) -> str:
    """The stable group_id for `name` — looked up if this canonical identity has
    been seen before, assigned + persisted to data/group_registry.csv on first
    sight. Empty name -> empty id (never assign an id to nothing)."""
    global _registry_next
    registry = _load_registry()
    key = _canon(name)
    if not key:
        return ""
    if key not in registry:
        gid = f"G{_registry_next:04d}"
        _registry_next += 1
        registry[key] = gid
        _append_registry_row(gid, key, str(name).strip())
    return registry[key]


def build_state() -> dict:
    # Reuse build_book's cached, n_jobs-patched load rather than joblib.load()-ing
    # this ensemble a second time — build_book.build() (called right before this on
    # every mutation: delete, upload commit, retrain) already paid that cost once
    # this process; a second cold unpickle here just doubled it for nothing. See
    # build_book._load_model for why the patch matters too — this pipeline gets
    # reused for the recommender's predict_proba calls below.
    import build_book
    payload = build_book._load_model()
    pipeline, features = payload["pipeline"], payload.get("features")
    scored_raw = pd.read_csv(REAL_SCORED, parse_dates=["eff_date"])
    scored_raw = scored_raw.sort_values("renewal_probability").reset_index(drop=True)
    scored_raw["group_id"] = scored_raw["group_name"].map(get_or_assign_group_id)
    scored_raw["confirmed"] = True
    history = pd.read_csv(REAL_HISTORY, parse_dates=["eff_date"])

    confirmed_display = _display_frame(scored_raw, history)

    # Decided renewals (real outcomes) so the Monthly Renewal Outlook shows the FULL
    # book — settled groups counted by actual Won/Lost, not a predicted score.
    decided = history.rename(columns={"eff_date": "renewal_date"}).copy()
    decided = decided[decided["renewed"].notna()]
    decided["_canon"] = decided["group_name"].map(_canon)

    registry = [e for e in load_registry() if str(e.get("kind", "")).startswith("real")]
    # the frontend's month-over-month chart filters on kind == "monthly_backfill";
    # the real walk-forward entries live in the model payload
    history_entries = [
        {**e, "kind": "monthly_backfill"} for e in payload.get("backfill", [])
    ] + [e for e in registry if e.get("kind") == "real"]

    state = {
        "mode": "real",
        "model_version": payload["version"],
        "metrics": payload["metrics"],
        # LABELS.get fallback: older model payloads baked in a raw feature name as
        # the label whenever a feature was added to train_real.py without a matching
        # LABELS entry (e.g. premium_stoploss shipped unlabeled) — patch it here so
        # already-trained models display correctly without forcing a retrain.
        "importances": [
            {**row, "label": LABELS.get(row["feature"], row["label"])}
            for row in payload["importances"]
        ],
        "calibration": payload["calibration"],
        "roc": payload["roc"],
        # pooled leak-free walk-forward (honest headline numbers); None on older models
        "wf_metrics": payload.get("wf_metrics"),
        "wf_calibration": payload.get("wf_calibration") or [],
        "wf_roc": payload.get("wf_roc") or {"fpr": [], "tpr": []},
        "registry": history_entries,
        "n_history": int(len(history)),
        "_pipeline": pipeline,
        "_raw_book": scored_raw,
        "_features": features,
        "_nlr_curve": payload.get("nlr_curve"),
        "_increase_curve": payload.get("increase_curve"),
    }
    state.update(_bundle(confirmed_display, decided))
    return state


def _bundle(display: pd.DataFrame, decided: pd.DataFrame | None = None) -> dict:
    # canonical key on the open book so it de-dupes cleanly against decided history
    display = display.assign(_canon=display["group_name"].map(_canon))
    return {
        "summary": insights.summary(display),
        "forecast": insights.forecast(display, confirmed=decided),
        "segments": insights.segments(display),
        "groups": insights.group_rows(display),
    }


def rescore(state: dict, group_id: str, overrides: dict) -> float:
    """Re-score one group with hypothetical lever changes (real model). Used by
    the recommender to test intervention scenarios for one group.
    Levers map: rate_increase_pct -> total_increase_pct,
                laser_at_renewal  -> lasers_renewal (0/1)."""
    raw = state["_raw_book"]
    features = state["_features"]
    sub = raw[raw["group_id"] == group_id]
    if sub.empty:
        raise KeyError(group_id)
    row = sub.iloc[[0]].copy()
    if "rate_increase_pct" in overrides:
        inc = float(overrides["rate_increase_pct"])
        row["total_increase_pct"] = inc
        row["rate_increase_penalty"] = max(inc - 30.0, 0.0)
    if "laser_at_renewal" in overrides:
        row["lasers_renewal"] = 1.0 if overrides["laser_at_renewal"] else 0.0
    raw_p = state["_pipeline"].predict_proba(row[features])[:, 1]
    p = apply_override(raw_p, row["nlr"], state["_nlr_curve"])
    p = apply_increase_override(p, _effective_increase(row), state["_increase_curve"])
    return float(p[0])


def rescore_batch(state: dict, jobs: list[tuple[str, dict]]) -> list[float | None]:
    """Score MANY (group_id, overrides) at once with a single model call. Used by
    the recommender so the whole book's optimal-price sweep is one predict instead
    of hundreds (sub-second instead of timing out)."""
    raw = state["_raw_book"]
    features = state["_features"]
    base = {gid: raw[raw["group_id"] == gid].iloc[0]
            for gid in {g for g, _ in jobs} if (raw["group_id"] == gid).any()}
    built, pos = [], []
    for i, (gid, ov) in enumerate(jobs):
        if gid not in base:
            continue
        r = base[gid].copy()
        if "rate_increase_pct" in ov:
            inc = float(ov["rate_increase_pct"])
            r["total_increase_pct"] = inc
            r["rate_increase_penalty"] = max(inc - 30.0, 0.0)
        if "laser_at_renewal" in ov:
            r["lasers_renewal"] = 1.0 if ov["laser_at_renewal"] else 0.0
        built.append(r)
        pos.append(i)
    out: list[float | None] = [None] * len(jobs)
    if built:
        built_df = pd.DataFrame(built)
        raw_probs = state["_pipeline"].predict_proba(built_df[features])[:, 1]
        probs = apply_override(raw_probs, built_df["nlr"], state["_nlr_curve"])
        probs = apply_increase_override(probs, _effective_increase(built_df), state["_increase_curve"])
        for k, i in enumerate(pos):
            out[i] = float(probs[k])
    return out


# Columns real_history.csv carries that also exist (same name) on the scored/deals
# book, so a manual transfer can copy them straight across. Everything else in
# history (source, renewed, tenure_is_derived, neg_*_increase_pct, carrier_changed,
# am_changed, bor_change_confirmed) is set explicitly below or left blank — never
# guessed.
_HISTORY_COPY_COLS = [
    "product", "broker", "rsd", "am", "tpa", "carrier", "state", "lives",
    "annual_premium", "premium_stoploss", "nlr", "isl_loss_ratio",
    "mature_to_attachment", "fixed_increase_pct", "total_increase_pct",
    "initial_uw_increase_pct", "lasers_current", "lasers_renewal", "laser_liability",
    "captive_offer", "tenure_years", "bor_change", "broker_groups_with_cs",
    "broker_years_with_cs", "broker_products_sold", "broker_preferred", "network",
    "corridor",
]


def _history_row(hist_columns, r, outcome: str) -> dict:
    """Build one real_history.csv row from a raw scored-book row, using the given
    RESOLVED outcome (from state["groups"], so a manual override wins the same way it
    does in the UI). Never guesses a field: only copies columns that exist on both
    sides and are actually populated."""
    new_row = {c: np.nan for c in hist_columns}
    new_row["group_name"] = r["group_name"]
    new_row["eff_date"] = pd.to_datetime(r["eff_date"]).date().isoformat()
    new_row["source"] = "manual_transfer"
    new_row["renewed"] = 1.0 if outcome == "Renewed" else 0.0
    for c in _HISTORY_COPY_COLS:
        if c in hist_columns and c in r.index and pd.notna(r[c]):
            new_row[c] = r[c]
    return new_row


def move_many_to_history(state: dict, outcomes: dict[str, str]) -> int:
    """Transfer every given group (group_id -> 'Renewed'/'Termed') from Upcoming
    Renewals into the permanent Renewal Database in one batch: appends their rows to
    real_history.csv (source='manual_transfer') and flags them 'leaked' in
    real_scored_book.csv so due_leakfree() stops surfacing them on Upcoming Renewals.
    Both writes persist to disk, once, regardless of batch size. Button-driven, not
    automatic — "decided in training" vs "decided in the live forward-test window" is
    a real distinction (see forward_book.py) that a blanket always-on rule got wrong
    twice already; this stays an explicit action the user triggers.
    Returns the number of groups actually moved."""
    if not outcomes:
        return 0
    raw = state["_raw_book"]
    sub = raw[raw["group_id"].isin(outcomes)]
    if sub.empty:
        return 0

    hist = pd.read_csv(REAL_HISTORY)
    new_rows = [_history_row(hist.columns, r, outcomes[r["group_id"]]) for _, r in sub.iterrows()]
    hist = pd.concat([hist, pd.DataFrame(new_rows)], ignore_index=True)
    hist.to_csv(REAL_HISTORY, index=False)

    scored = pd.read_csv(REAL_SCORED, parse_dates=["eff_date"])
    if "leaked" not in scored.columns:
        scored["leaked"] = False
    keys = set(zip(sub["group_name"], pd.to_datetime(sub["eff_date"])))
    mask = pd.Series(list(zip(scored["group_name"], scored["eff_date"])), index=scored.index)
    scored.loc[mask.apply(lambda k: k in keys), "leaked"] = True
    scored.to_csv(REAL_SCORED, index=False)
    return len(sub)


def move_to_history(state: dict, group_id: str) -> None:
    """Manually transfer ONE decided group from Upcoming Renewals into the permanent
    Renewal Database. See move_all_decided_to_history — same mechanics, single row."""
    g = next((x for x in state["groups"] if x["group_id"] == group_id), None)
    if g is None:
        raise KeyError(f"group {group_id} not found")
    outcome = g.get("actual_outcome")
    if outcome not in ("Renewed", "Termed"):
        raise ValueError(f"group {group_id} has no decided outcome yet")
    move_many_to_history(state, {group_id: outcome})


def move_all_decided_to_history(state: dict) -> int:
    """Transfer EVERY currently-decided group in Upcoming Renewals to the Renewal
    Database in one shot. Powers the page-level 'Move Decided to Renewal Database'
    button (as opposed to moving one group at a time). Eligibility AND the outcome
    written are both read from state["groups"] — the SAME decided/open split and
    resolved outcome already shown in the Upcoming Renewals table (post due_leakfree,
    post override matching) — so the button only ever moves rows the user can actually
    see marked Renewed/Termed there, with the outcome they see."""
    outcomes = {g["group_id"]: g["actual_outcome"] for g in state["groups"]
                if g.get("actual_outcome") in ("Renewed", "Termed")}
    return move_many_to_history(state, outcomes)
