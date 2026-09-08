"""
ETL for the real Crumdale renewal data drop (/data folder).

Sources processed:
  1. "2025 Non Renewals.xlsx"          -> monthly "X.1.25 Renewals" sheets
       UW detail: NLR, ISL LR, rate increases, lasers, premium, carrier, outcome
  2. "2026 New Retention Analysis.xlsx"
       "HPS Groups" sheet: broker, RSD, AM, product, NLR, rate action, lasers,
       Years w/ CS (tenure), BOR Change, outcome   [Jan-Jun 2026]
       "All <Mon> Renewals" sheets: cost/premium detail to enrich
  3. "AM TEAM RENEWAL TRACKER.xlsx"    -> monthly sheets Jul 2023 - Jul 2026
       broker, AM, RSD, TPA, product, lives, outcome
       + cross-year linkage to derive tenure and broker-change (BOR proxy)

Output:
  backend/data/real_history.csv   unified renewal decisions
  prints a coverage report
"""

from __future__ import annotations

import difflib
import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning)

ROOT = Path(__file__).resolve().parents[1]
# Single canonical source of truth. Previously the ETL read from data/, newData/,
# FillData/ — which drifted out of sync (change logs ended up stranded in
# FillData---DONE/ and never read). All-Data/ holds the complete, newest set.
DATA = ROOT / "All-Data"
SRC_DIRS = (DATA,)  # every extractor globs here; newest file per pattern wins
OUT = ROOT / "backend" / "data" / "real_history.csv"

# 12 verified broker-of-record changes from the manual "BOR change chart" (the
# screenshot in All-Data/). An image can't be parsed in the pipeline, so the confirmed
# groups are encoded here and matched to history by a distinctive name fragment.
SCREENSHOT_BOR_CHANGES = [
    "tandem intermediate", "southwind", "educational service agency 8", "ohio ambulance",
    "borland grover", "nations benefit", "unify energy", "donovan marine",
    "proximity malt", "its logistics", "camden city", "carlson distributing",
]

SUFFIXES = r"\b(incorporated|inc|llc|llp|pllc|ltd|co|corp|corporation|company|pc|pa|group|holdings)\b"


def norm_name(s) -> str:
    if not isinstance(s, str):
        return ""
    s = s.lower().replace("&", " and ")
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(SUFFIXES, " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def key(s) -> str:
    """Names are truncated ~28 chars in some sheets; match on a stable prefix."""
    return norm_name(s)[:20]


def clean_group_name(s) -> str:
    """Strip embedded working notes from a group name: anything after the first
    line break (TPA tags like 'Personify'/'HEZ', product tags like 'LEVEL FUNDED',
    status notes) plus surrounding punctuation. Keeps the real company name."""
    s = str(s)
    first = s.split("\n")[0].strip()
    if not first:  # name starts with a line break -> fall back to the whole string
        first = s.replace("\n", " ")
    return re.sub(r"\s+", " ", first).strip(" -–,")


# True clean-data max is 857 (Carteret BOE); the AM Team Renewal Tracker's 2026
# sheets have ~23 rows where a premium-sized dollar figure was typed into the
# Lives cell (verified against the raw workbook, not an ETL parsing issue).
# Blank rather than guess, per the standing "blank over fake" data rule.
LIVES_MAX_PLAUSIBLE = 10_000


def sanitize_lives(df: pd.DataFrame) -> pd.DataFrame:
    """Blank implausible `lives` values instead of feeding them to the model."""
    if "lives" not in df.columns:
        return df
    bad = df["lives"] > LIVES_MAX_PLAUSIBLE
    n = int(bad.sum())
    if n:
        print(f"  blanked {n} implausible lives value(s) (>{LIVES_MAX_PLAUSIBLE:,})")
        df.loc[bad, "lives"] = np.nan
    return df


def _to01(v):
    """Normalize any boolean-ish value to a consistent 1 / 0 / <NA>."""
    if v is None or v is pd.NA or (isinstance(v, float) and pd.isna(v)) or v == "":
        return pd.NA
    s = str(v).strip().lower()
    if s in ("true", "1", "1.0", "yes", "y", "t"):
        return 1
    if s in ("false", "0", "0.0", "no", "n", "f"):
        return 0
    return pd.NA


def clean_carrier(v) -> str | None:
    """Standardize a stop-loss carrier value: resolve 'X to Y' mid-year change
    notations to the final carrier Y, collapse spacing. Keeps it leakage-safe
    (still just the carrier, known at renewal)."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return None
    parts = re.split(r"\s+to\s+", s, flags=re.I)  # "Zurich to Pan Am" -> "Pan Am"
    if len(parts) > 1:
        s = parts[-1].strip()
    return re.sub(r"\s+", " ", s).strip() or None


def clean_am(v) -> str | None:
    """Current account manager only — strip transfer notes ('(from Wendy)',
    'supported by ...') and trailing parentheticals, so the same AM isn't read
    as different people across years."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = re.sub(r"\(.*?\)", " ", str(v))
    s = re.split(r"\bsupported by\b", s, flags=re.I)[0]
    s = re.sub(r"\s+", " ", s).strip(" -/,")
    return s or None


def _token_covered(small, large) -> bool:
    """Every token in `small` has an exact or prefix(>=4 chars) partner in
    `large` — so 'auto' matches 'automotive', 'comm' matches 'community'."""
    return all(
        any(s == l or (len(s) >= 4 and len(l) >= 4 and (l.startswith(s) or s.startswith(l)))
            for l in large)
        for s in small
    )


def name_match(ti: frozenset, tj: frozenset, ni: str, nj: str) -> str:
    """How strongly two same-month names refer to the same group.
    'strong' -> merge on the name alone; 'weak' -> merge only with a
    corroborating broker / AM / size; 'no' -> treat as different groups.
    Token coverage handles abbreviations ('Blue Grass Auto' == 'Blue Grass
    Automotive Inc'); the fuzzy ratio catches typos ('Wheelland'/'Wheeland')."""
    small, large = (ti, tj) if len(ti) <= len(tj) else (tj, ti)
    if small and _token_covered(small, large):
        return "strong" if len(small) >= 2 else "weak"
    if min(len(ti), len(tj)) >= 2 and difflib.SequenceMatcher(None, ni, nj).ratio() >= 0.88:
        return "strong"
    return "no"


# words too generic to anchor a match on their own
_GENERIC_TOK = {
    "group", "groups", "holding", "holdings", "company", "companies", "co", "inc",
    "incorporated", "llc", "corp", "corporation", "ltd", "lp", "plc", "the", "of",
    "and", "for", "services", "service", "systems", "system", "enterprises",
    "enterprise", "international", "national", "associates", "partners", "dba",
}


def _distinctive_toks(name) -> set:
    """Name tokens that actually identify the company (drop generic filler)."""
    return {t for t in norm_name(name).split() if t not in _GENERIC_TOK and len(t) >= 3}


def _tok_partner(s, toks) -> bool:
    """`s` has an exact or prefix(>=4) partner in `toks` (handles plural/abbrev:
    alliance~alliances, lab~laboratories aren't prefix-equal, but oak/view/dermatology
    style overlaps are)."""
    return any(s == l or (len(s) >= 4 and len(l) >= 4 and (l.startswith(s) or s.startswith(l)))
               for l in toks)


def resolve_exec_keys(ex: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    """STRICT dynamic name matching for exec-log -> history. When an exec block does
    not key-match a same-month history group, remap it to that group ONLY when the
    names share a distinctive token (exact/prefix, so plural & spacing variants count)
    AND enrolled lives agree within 1. The lives guard is what keeps look-alikes
    apart (e.g. 'Federal Heating' must NOT absorb a different 'Federal Elite')."""
    if not len(ex) or not len(history):
        return ex
    h = history[["k", "eff_month", "group_name", "lives"]].copy()
    h["toks"] = h["group_name"].map(lambda n: frozenset(norm_name(n).split()))
    by_month = {m: g.to_dict("records") for m, g in h.groupby("eff_month")}
    exact = set(zip(history["k"], history["eff_month"]))
    ex = ex.reset_index(drop=True).copy()
    ks = ex["k"].tolist()
    n = 0
    for i in range(len(ex)):
        k, m = ex.at[i, "k"], ex.at[i, "eff_month"]
        if (k, m) in exact:
            continue
        cands = by_month.get(m)
        if not cands:
            continue
        ed = _distinctive_toks(ex.at[i, "group_name"])
        el = ex.at[i, "lives"]
        if not ed or pd.isna(el):
            continue
        for c in cands:
            shared = any(_tok_partner(s, c["toks"]) for s in ed)
            lives_ok = pd.notna(c["lives"]) and abs(float(el) - float(c["lives"])) <= 1
            if shared and lives_ok:
                ks[i] = c["k"]
                n += 1
                break
    ex["k"] = ks
    ex = ex.drop_duplicates(subset=["k", "eff_month"], keep="first")  # avoid fan-out post-remap
    if n:
        print(f"exec dynamic name-match: remapped {n} blocks via shared-token + matching lives")
    return ex


def to_num(v):
    if pd.isna(v):
        return np.nan
    try:
        return float(str(v).replace(",", "").replace("$", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return np.nan


def norm_outcome(v):
    if pd.isna(v):
        return None
    s = str(v).strip().lower()
    if s in ("renewed", "renew", "r", "active", "x") or s.startswith("renew"):
        return 1
    if s in ("termed", "term", "terminated", "t", "lost") or s.startswith("term"):
        return 0
    return None


# ------------------------------------------------------- 1. 2025 Non Renewals

def extract_2025() -> pd.DataFrame:
    xl = pd.ExcelFile(DATA / "2025 Non Renewals.xlsx")
    rows = []
    for sheet in xl.sheet_names:
        m = re.match(r"(\d+)\.1\.25 Renewals", sheet)
        if not m:
            continue
        month = int(m.group(1))
        raw = xl.parse(sheet, header=None, nrows=6)
        hdr = next(
            (i for i in range(6)
             if any("Group Name" in str(v) for v in raw.iloc[i].tolist())),
            2,
        )
        df = xl.parse(sheet, header=hdr)
        df.columns = [str(c).strip() for c in df.columns]
        # 12.1.25 has a second side-table starting at "Group Name.1" - drop it
        if "Group Name.1" in df.columns:
            df = df.loc[:, : "Group Name.1"].iloc[:, :-1]
        df = df[df["Group Name"].notna()]
        df = df[~df["Group Name"].astype(str).str.contains("Group Name|TOTAL", case=False)]

        outcome_col = next(
            (c for c in df.columns
             if re.search(r"renew(ed)?\s*/\s*term|r/t|renewed/ termed", c, re.I)),
            None,
        )
        for _, r in df.iterrows():
            outcome = norm_outcome(r.get(outcome_col)) if outcome_col else None
            if outcome is None:
                # 1.1.25 keeps the outcome in the UW comments column
                for c in df.columns:
                    if "UW Comments" in c:
                        outcome = norm_outcome(r.get(c))
                        break
            rows.append({
                "group_name": str(r["Group Name"]).strip(),
                "eff_date": pd.Timestamp(2025, month, 1),
                "lives": to_num(r.get("Lives")),
                "state": (str(r.get("State") or r.get("ST") or "").strip() or None),
                "nlr": to_num(r.get("NLR")),
                "isl_loss_ratio": to_num(r.get("ISL Loss Ratio")),
                "mature_to_attachment": to_num(r.get("Mature to Attachment")),
                "fixed_increase_pct": to_num(r.get("Total Fixed.4")) * 100 if pd.notna(to_num(r.get("Total Fixed.4"))) else np.nan,
                "total_increase_pct": to_num(r.get("Total Cost.4")) * 100 if pd.notna(to_num(r.get("Total Cost.4"))) else np.nan,
                "annual_premium": to_num(r.get("Total Fixed.3")) or to_num(r.get("Total Renewal Premium")),
                "lasers_current": to_num(r.get("# of Lasers")),
                "lasers_renewal": to_num(r.get("# of Lasers.1")),
                "laser_liability": to_num(r.get("$ Liability above ISL Ded.1")),
                "carrier": (str(r.get("Carrier") or "").strip() or None),
                "renewed": outcome,
                "source": "uw2025",
            })
    return pd.DataFrame(rows)


# -------------------------------------------- 2. 2026 New Retention Analysis

def extract_2026() -> tuple[pd.DataFrame, pd.DataFrame]:
    xl = pd.ExcelFile(DATA / "2026 New Retention Analysis.xlsx")
    hps = xl.parse("HPS Groups")
    hps.columns = [str(c).strip() for c in hps.columns]
    rows = []
    for _, r in hps.iterrows():
        if pd.isna(r.get("Group")):
            continue
        outcome = norm_outcome(r.get("Renewed/ Termed"))
        ren_w_lasers = to_num(r.get("Renewal w/ Lasers"))
        init_uw = to_num(r.get("Initial UW Renewal"))
        rows.append({
            "group_name": str(r["Group"]).strip(),
            "eff_date": pd.to_datetime(r.get("Eff Date"), errors="coerce"),
            "product": (str(r.get("Product") or "").strip() or None),
            "broker": (str(r.get("Broker") or "").strip() or None),
            "rsd": (str(r.get("RSD") or "").strip() or None),
            "am": (str(r.get("AM") or "").strip() or None),
            "lives": to_num(r.get("EE")),
            "carrier": (str(r.get("Carrier") or "").strip() or None),
            "nlr": to_num(r.get("Net Loss Ratio after Rebates")),
            "mature_to_attachment": to_num(r.get("Mature Claims to Attachment")),
            "initial_uw_increase_pct": init_uw * 100 if pd.notna(init_uw) else np.nan,
            "total_increase_pct": ren_w_lasers * 100 if pd.notna(ren_w_lasers) else np.nan,
            "lasers_renewal": to_num(r.get("Lasers")),
            "captive_offer": str(r.get("Captive Offer? Y/N") or "").strip().upper() == "Y",
            "tenure_years": to_num(r.get("Years w/ CS")),
            "bor_change": str(r.get("BOR Change?") or "").strip().upper().startswith("Y"),
            "renewed": outcome,
            "source": "ret2026",
        })
    main = pd.DataFrame(rows).dropna(subset=["eff_date"])

    # premium / cost detail from the "All <Mon> Renewals" sheets
    detail_rows = []
    for sheet in xl.sheet_names:
        if not sheet.startswith("All "):
            continue
        raw = xl.parse(sheet, header=None, nrows=6)
        hdr = next(
            (i for i in range(6)
             if any("Group Name" in str(v) for v in raw.iloc[i].tolist())),
            3,
        )
        df = xl.parse(sheet, header=hdr)
        df.columns = [str(c).strip() for c in df.columns]
        if "RENEWED GROUPS" in df.columns:
            df = df.loc[:, : "RENEWED GROUPS"].iloc[:, :-1]
        df = df[df["Group Name"].notna()]
        df = df[~df["Group Name"].astype(str).str.contains("Group Name|TOTAL", case=False)]
        date_col = "Effective Date" if "Effective Date" in df.columns else "Eff Date"
        for _, r in df.iterrows():
            detail_rows.append({
                "k": key(r["Group Name"]),
                "eff_date": pd.to_datetime(r.get(date_col), errors="coerce"),
                "annual_premium": to_num(r.get("Total Fixed.3")),
                "state": (str(r.get("ST") or "").strip() or None),
                "lasers_current": to_num(r.get("# of Lasers")),
                "fixed_increase_pct": to_num(r.get("Total Fixed.4")) * 100 if pd.notna(to_num(r.get("Total Fixed.4"))) else np.nan,
            })
    detail = pd.DataFrame(detail_rows).dropna(subset=["eff_date"])
    detail["eff_month"] = detail["eff_date"].dt.to_period("M")
    detail = detail.drop_duplicates(subset=["k", "eff_month"])
    return main, detail


# ----------------------------------------------------- 3. AM team tracker

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "june": 6,
    "jul": 7, "july": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}


def extract_tracker() -> pd.DataFrame:
    xl = pd.ExcelFile(DATA / "AM TEAM RENEWAL TRACKER.xlsx")
    rows = []
    for sheet in xl.sheet_names:
        m = re.match(r"^([A-Za-z]{3,4})\.?\s?(20\d\d)$", sheet.strip())
        if not m or "none" in sheet:
            continue
        month = MONTHS.get(m.group(1).lower())
        year = int(m.group(2))
        if month is None:
            continue
        raw = xl.parse(sheet, header=None, nrows=10)
        hdr = next(
            (i for i in range(min(10, len(raw)))
             if any("Opportunity Name" in str(v) for v in raw.iloc[i].tolist())),
            None,
        )
        if hdr is None:
            continue
        df = xl.parse(sheet, header=hdr)
        df.columns = [str(c).replace("\n", " ").strip() for c in df.columns]
        name_col = next(c for c in df.columns if "Opportunity Name" in c)
        df = df[df[name_col].notna()]
        df = df[~df[name_col].astype(str).str.contains("TOTAL|Opportunity", case=False)]

        def col(*subs):
            for c in df.columns:
                for s in subs:
                    if s.lower() == c.lower() or c.lower().startswith(s.lower()):
                        return c
            return None

        c_renewed = col("Renewed")
        c_termed = col("Term'd", "Terminated")
        c_pending = col("Decision Pending")
        c_broker = col("Broker")
        c_am = col("CP AM", "CS AM", "AM")
        c_rsd = col("CP RSD", "RSD")
        c_product = col("HPS PRODUCT", "HPS Product")
        c_tpa = col("TPA")
        c_lives = col("Lives")
        c_revision = col("Date of Revision Request")

        for _, r in df.iterrows():
            renewed = None
            if c_renewed is not None and pd.notna(r.get(c_renewed)) and str(r.get(c_renewed)).strip():
                renewed = 1
            elif c_termed is not None and pd.notna(r.get(c_termed)) and str(r.get(c_termed)).strip():
                renewed = 0
            pending = bool(
                c_pending is not None and pd.notna(r.get(c_pending)) and str(r.get(c_pending)).strip()
            )
            rows.append({
                "group_name": str(r[name_col]).strip(),
                "eff_date": pd.Timestamp(year, month, 1),
                "broker": (str(r.get(c_broker) or "").strip() or None) if c_broker else None,
                "am": (str(r.get(c_am) or "").strip() or None) if c_am else None,
                "rsd": (str(r.get(c_rsd) or "").strip() or None) if c_rsd else None,
                "product": (str(r.get(c_product) or "").strip() or None) if c_product else None,
                "tpa": (str(r.get(c_tpa) or "").strip() or None) if c_tpa else None,
                "lives": to_num(r.get(c_lives)) if c_lives else np.nan,
                "revision_requested": bool(
                    c_revision is not None and pd.notna(r.get(c_revision)) and str(r.get(c_revision)).strip()
                ),
                "renewed": renewed,
                "pending": pending,
                "source": "tracker",
            })
    return pd.DataFrame(rows)


# --------------------------------------- 4. negotiation detail (Sheet2, 1.1.25)

def extract_negotiation() -> pd.DataFrame:
    """'Sheet2' of 2025 Non Renewals: per-group negotiation history for the
    1.1.25 block - initial vs final increase, # of rate revisions, renewal #."""
    df = pd.read_excel(DATA / "2025 Non Renewals.xlsx", sheet_name="Sheet2", header=1)
    df.columns = [str(c).replace("\n", " ").strip() for c in df.columns]
    df = df[df["Group Name"].notna()]
    rows = []
    for _, r in df.iterrows():
        init, final = to_num(r.get("Initial Increase")), to_num(r.get("Final Increase (pre-lasers)"))
        rows.append({
            "k": key(r["Group Name"]),
            "eff_month": pd.Period("2025-01", freq="M"),
            "neg_initial_increase_pct": init * 100 if pd.notna(init) else np.nan,
            "neg_final_increase_pct": final * 100 if pd.notna(final) else np.nan,
            "neg_rate_revisions": to_num(r.get("# of Rate Changes To Broker*")),
            "neg_renewal_number": to_num(r.get("Renewal #")),
        })
    return pd.DataFrame(rows).drop_duplicates(subset=["k", "eff_month"])


# ------------------------------- 5. broker-level attributes (Reasons sheets)

def extract_broker_table() -> pd.DataFrame:
    """Broker-relationship attributes from the analyst-curated 'Broker Relationship
    Data Needed' workbook. The analyst deduped variant broker names (the 'Broker if
    duplicate' column gives the canonical broker) and left rows blank where they had
    no information (the 'red' brokers) — we keep those blank by design. Broker-level,
    so it applies to renewed and termed groups alike (no label leakage).

    'Broker products sold' is free text (e.g. 'HPS, PBM, SL'); we encode it as the
    COUNT of distinct products to match the numeric feature the model expects."""
    f = pd.read_excel(DATA / "Broker Relationship Data Needed.xlsx", header=0)
    f.columns = [str(c).strip() for c in f.columns]

    def _products(v):
        if pd.isna(v):
            return np.nan
        toks = [t.strip() for t in re.split(r"[.,/&]| and ", str(v)) if t.strip()]
        return float(len(toks)) if toks else np.nan

    def _preferred(v):
        s = str(v).strip().upper()
        return 1.0 if s.startswith("Y") else (0.0 if s.startswith("N") else np.nan)

    def _vals(r):
        return {
            "broker_years_with_cs": to_num(r.get("Broker years with CS")),
            "broker_groups_with_cs": to_num(r.get("Broker groups with CS")),
            "broker_products_sold": _products(r.get("Broker products sold")),
            "broker_preferred": _preferred(r.get("Preferred broker (Y/N)")),
        }

    # Key on the broker name AND its canonical (dup) name -> same values, so a
    # group recorded under either spelling resolves. Primary names win on conflict.
    look: dict[str, dict] = {}
    for _, r in f.iterrows():
        bk = norm_name(r.get("Broker"))
        if bk:
            look[bk] = _vals(r)
    for _, r in f.iterrows():
        bk = norm_name(r.get("Broker if duplicate"))
        if bk:
            look.setdefault(bk, _vals(r))
    return pd.DataFrame([{"bk": k, **v} for k, v in look.items()])


_BROKER_ATTR_COLS = ("broker_years_with_cs", "broker_groups_with_cs",
                     "broker_products_sold", "broker_preferred")


def apply_broker_attrs(df: pd.DataFrame, brk: pd.DataFrame) -> pd.DataFrame:
    """Attach broker-relationship attributes to `df` by broker name. Matches the
    normalized name first, then falls back to FLEXIBLE token matching so the many
    spelling variants in our records resolve to the analyst's canonical broker:
    'USI - Cincinnati' -> 'USI Cinci', 'Evolve' -> 'Evolve Insurance Advisors',
    'Strategic Benefits Cincinnati' -> 'Strategic Ben Cinci'.

    A variant matches a file broker when their LEAD distinctive token agrees (so
    'Gallagher' never grabs 'Connor & Gallagher') and the shorter token set is a
    fuzzy subset of the longer one (handles abbreviations like cinci~cincinnati).
    When a name matches several file brokers with CONFLICTING values — a bare 'USI'
    or 'Assured Partners' that could be any office — it is left blank: flexible on
    spelling, but never guessing which office."""
    look = {r.bk: (r.broker_years_with_cs, r.broker_groups_with_cs,
                   r.broker_products_sold, r.broker_preferred)
            for r in brk.itertuples()}
    # Keep 2-char office tokens (WI, PA, TX) here so multi-office brands stay
    # distinguishable — 'USI - WI' must not collapse to a bare 'usi' that matches
    # every USI office. (The shared exec matcher keeps its >=3 floor untouched.)
    def _btoks(s):
        return [t for t in s.split() if t not in _GENERIC_TOK and len(t) >= 2]
    ftoks = {k: _btoks(k) for k in look}

    def _eq(a, b):
        return all((pd.isna(x) and pd.isna(y)) or x == y for x, y in zip(a, b))

    def _variant(a, b):                       # a, b = ordered distinctive-token lists
        short, long = (a, b) if len(a) <= len(b) else (b, a)
        if not short or not long:
            return False
        if not _tok_partner(short[0], {long[0]}):          # lead word must agree
            return False
        return all(_tok_partner(s, set(long)) for s in short)

    cache: dict[str, tuple] = {}

    def _resolve(name):
        h = norm_name(name)
        if not h:
            return (np.nan, np.nan, np.nan, np.nan)
        if h in cache:
            return cache[h]
        if h in look:
            val = look[h]
        else:
            ht = _btoks(h)
            cand = [look[k] for k, kt in ftoks.items() if _variant(ht, kt)]
            val = cand[0] if cand and all(_eq(cand[0], c) for c in cand) \
                else (np.nan, np.nan, np.nan, np.nan)
        cache[h] = val
        return val

    res = df["broker"].map(_resolve)
    for i, col in enumerate(_BROKER_ATTR_COLS):
        df[col] = [t[i] for t in res]
    return df


# ----------------------------------- 6. executive logs (transposed, v2.1 era)

EXEC_FILES = [
    "Executive Log 9.1.25 Block.xlsx", "Executive Log 10.1.25 block.xlsx",
    "Executive Log 11.1.25 renewals.xlsx", "Executive Log 12.1.25-1.1.26 Batch One.xlsx",
    "Executive Log 1.1.26 Batch Two.xlsx", "Executive Log v2.1 - 2.1.26 block.xlsx",
    "3.1.26 Executive Log v2.1.xlsx", "4.1.26 Executive Log v2.1.1.xlsx",
    "Executive Log -5.1.26 block.xlsx", "6.1.26 Block Executive Log.xlsx",
    "Exec Log 7.1.26 block.xlsx", "8.1.26 Executive Log.xlsx", "Exec Log 6.1.25.xlsx",
]


def clean_exec_name(name: str) -> str:
    """Strip working annotations the UW team embeds in exec-log group names."""
    s = re.sub(r"^new\s*[\d.]*\s*:?\s*", "", name.strip(), flags=re.I)
    s = re.sub(r"\((illust\w*|firm)\)", " ", s, flags=re.I)
    s = re.sub(r"\b(illust\w*|firm)\b\s*$", " ", s, flags=re.I)
    s = re.sub(r"[-–]?\s*(sold|renewed|termed?|term)\s*/?\s*(sold|renewed|termed?|term)?\s*$", " ", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip(" -–")


def _label_row(raw: pd.DataFrame, *labels) -> int | None:
    col0 = raw.iloc[:, 0].astype(str).str.strip().str.lower()
    for lab in labels:
        hits = col0[col0.str.startswith(lab.lower())]
        if len(hits):
            return int(hits.index[0])
    return None


def _parse_exec_sheet(raw: pd.DataFrame, r_name: int, fname: str) -> list[dict]:
    """Parse ONE transposed exec-log sheet (fields down column 0, one column per
    group). `r_name` is the row holding the group names — usually 0, but row 1 in the
    Jan-2025 Batch-Two sheets where 'Renewal Date' sits above 'Group Name'."""
    r_date = _label_row(raw, "renewal date")
    r_lives = _label_row(raw, "lives")
    r_rsd = _label_row(raw, "rsd")
    r_am = _label_row(raw, "am")
    r_broker = _label_row(raw, "broker")
    r_nren = _label_row(raw, "# of renewals")
    r_prod = _label_row(raw, "cp product")
    r_carrier = _label_row(raw, "carrier")
    r_mat = _label_row(raw, "mature claims to attach", "mature claims")  # Batch Two label variant
    r_prem = _label_row(raw, "annualized premium")
    r_firm = _label_row(raw, "firm renewal")
    r_firml = _label_row(raw, "firm renewal w/ lasers")
    r_lr = _label_row(raw, "annualized lr")
    r_lr_net = r_lr + 2 if r_lr is not None else None  # 'Net' under the LR header
    r_corridor = _label_row(raw, "corridor")

    by_group: dict[tuple, dict] = {}
    for j in range(1, raw.shape[1]):
        raw_name = raw.iloc[r_name, j]
        if not isinstance(raw_name, str) or not raw_name.strip():
            continue
        decided_marker = bool(re.search(r"sold|renewed|term", raw_name, re.I))
        name = clean_exec_name(raw_name)
        if not name:
            continue

        def cell(i):
            return raw.iloc[i, j] if i is not None and i < len(raw) else np.nan

        eff = pd.to_datetime(cell(r_date), errors="coerce")
        if pd.isna(eff):
            continue
        laser_count = 0
        if r_firm is not None and r_firml is not None:
            for i in range(r_firm + 1, r_firml):
                lab = str(raw.iloc[i, 0]).strip().lower()
                if lab.startswith("laser") and pd.notna(raw.iloc[i, j]) and str(raw.iloc[i, j]).strip():
                    laser_count += 1
        firm = to_num(cell(r_firm))
        firml = to_num(cell(r_firml))
        gk = (key(name), eff.to_period("M"))
        # A group can have several scenario columns side by side (primary + alternates).
        # Keep the FIRST (the headline renewal); skip the what-if alternates.
        if gk in by_group:
            continue
        by_group[gk] = {
            "k": key(name),
            "eff_month": eff.to_period("M"),
            "group_name": name.strip(),
            "eff_date": eff,
            "lives": to_num(cell(r_lives)),
            "rsd": (str(cell(r_rsd)).strip() if isinstance(cell(r_rsd), str) else None),
            "am": (str(cell(r_am)).strip() if isinstance(cell(r_am), str) else None),
            "broker": (str(cell(r_broker)).strip() if isinstance(cell(r_broker), str) else None),
            "n_renewals": to_num(cell(r_nren)),
            "product": (str(cell(r_prod)).strip() if isinstance(cell(r_prod), str) else None),
            "carrier": (str(cell(r_carrier)).strip() if isinstance(cell(r_carrier), str) else None),
            "mature_to_attachment": to_num(cell(r_mat)),
            "annual_premium": to_num(cell(r_prem)),
            "nlr": to_num(cell(r_lr_net)),
            "firm_increase_pct": firm * 100 if pd.notna(firm) and abs(firm) < 5 else np.nan,
            "firm_increase_w_lasers_pct": firml * 100 if pd.notna(firml) and abs(firml) < 5 else np.nan,
            "laser_count_detail": laser_count,
            "corridor": to_num(cell(r_corridor)),
            "decided_marker": decided_marker,
            "exec_file": fname,
        }
    return list(by_group.values())


def extract_exec_logs() -> pd.DataFrame:
    """Parse transposed exec-log sheets (row labels in col 0, one column per group)
    across ALL files in All-Data/exec/, every qualifying sheet per file."""
    rows = []
    # read EVERY exec log in All-Data/exec/ (renamed, date-ordered). Falls back to
    # the legacy hardcoded list if that folder isn't present. Non-conforming files
    # (no "Group Name" in A1) are skipped safely below.
    exec_dir = DATA / "exec"
    exec_paths = (sorted(exec_dir.glob("*.xls*")) if exec_dir.exists()
                  else [DATA / f for f in EXEC_FILES])
    for path in exec_paths:
        if not path.exists() or path.name.startswith("~$"):  # skip Excel lock files
            continue
        fname = path.name
        try:
            xl = pd.ExcelFile(path)
        except Exception:
            continue
        # Process EVERY transposed renewal sheet in this file. Most exec blocks have one
        # data tab ("Group Name" in A1); the Jan-2025 Batch Two spreads its groups across
        # many meeting-dated sheets with "Group Name" in ROW 1 (below "Renewal Date").
        # So: find the group-name row wherever it is and parse each qualifying tab.
        for sheet in xl.sheet_names:
            if any(kw in sheet.lower() for kw in
                   ("illust", "action item", "new business", "summary", "template", "blank", "pbm")):
                continue
            try:
                col0 = xl.parse(sheet, header=None, nrows=4).iloc[:, 0].astype(str).str.strip().str.lower()
            except Exception:
                continue
            r_name = next((i for i in range(len(col0)) if col0.iloc[i] == "group name"), None)
            if r_name is None:
                continue
            try:
                raw = xl.parse(sheet, header=None)
            except Exception:
                continue
            rows.extend(_parse_exec_sheet(raw, r_name, fname))
    df = pd.DataFrame(rows)
    if len(df):
        df = df.drop_duplicates(subset=["k", "eff_month"], keep="first")
    return df


# --------------------- 6a. Executive Renewal Tracker (per-month increase pipeline)

# One tab per renewal-month cohort. Months 2-12 are the current calendar year
# (2026); "1.1" is the FOLLOWING January (2027, the big annual-anniversary batch,
# which is why it alone gets broken into All/SF/LF/Captive/SLO sub-views -- we only
# read the "All Groups" one). Confirmed by cross-matching groups already in the book
# against their known eff_date months (e.g. every "10.1" match lines up with an
# existing 2026-10-01 row; every "1.1 - All Groups" match lines up with 2027-01-01).
_TRACKER_MONTH_YEAR = {
    "2.1": (2026, 2), "3.1": (2026, 3), "4.1": (2026, 4), "5.1": (2026, 5),
    "6.1": (2026, 6), "7.1": (2026, 7), "8.1": (2026, 8), "9.1": (2026, 9),
    "10.1": (2026, 10), "11.1": (2026, 11), "12.1": (2026, 12),
    # NOTE: despite the "1.1" name, this tab is the CLOSED Jan-2026 cycle (every
    # row already Renewed/Termed, confirmation dates Oct-Dec 2025) -- it is not a
    # live 2027 pipeline. Tagging it (2027, 1) let its finalized rate figures leak
    # into the upcoming Jan-2027 book as a fallback fill (e.g. Plumbers &
    # Pipefitters showing last year's 79.3%/78.7% as if it were next year's
    # quote). Tag it as what it is; once a real Jan-2027 tab exists, add it here.
    "1.1 - All Groups": (2026, 1),
}


def extract_renewal_tracker() -> pd.DataFrame:
    """Executive Renewal Tracker: broader per-month pipeline view (every group being
    tracked that month, not just the ones pulled into an exec-log block review). We
    only take the renewal-increase figure from it, on business direction -- every
    other field on this sheet already has a more authoritative source elsewhere in
    this pipeline (exec logs / Experience Table / SF book).
    The increase column is named 'Initial Renewal' on some month tabs and 'Initial UW
    Renewal' on others (same metric, confirmed via Alliance Fire Protection: the 12.1
    tab's 'Initial Renewal' and the 10.1 tab's 'Initial UW Renewal' both match that
    group's exec-log 'Firm Renewal' figure) -- prefer whichever is present. 'Renewal
    w/ Lasers' is the paired post-laser total, same role as the exec log's 'Firm
    Renewal w/ Lasers'.
    """
    files = []
    for d in SRC_DIRS:
        files += list(d.glob("*Executive Renewal Tracker*.xls*"))
    if not files:
        return pd.DataFrame()
    path = max(files, key=lambda p: p.stat().st_mtime)
    xl = pd.ExcelFile(path)
    rows = []
    for sheet, (year, month) in _TRACKER_MONTH_YEAR.items():
        if sheet not in xl.sheet_names:
            continue
        df = xl.parse(sheet, header=0)
        df.columns = [str(c).replace("\n", " ").strip() for c in df.columns]
        if "Group" not in df.columns:
            continue
        df = df[df["Group"].notna()]
        c_inc = ("Initial Renewal" if "Initial Renewal" in df.columns
                 else "Initial UW Renewal" if "Initial UW Renewal" in df.columns else None)
        c_lasers = "Renewal w/ Lasers" if "Renewal w/ Lasers" in df.columns else None
        for _, r in df.iterrows():
            inc = to_num(r.get(c_inc)) if c_inc else np.nan
            incl = to_num(r.get(c_lasers)) if c_lasers else np.nan
            if pd.isna(inc) and pd.isna(incl):
                continue
            rows.append({
                "group_name": str(r["Group"]).strip(),
                "eff_date": pd.Timestamp(year, month, 1),
                "tracker_increase_pct": inc * 100 if pd.notna(inc) else np.nan,
                "tracker_increase_w_lasers_pct": incl * 100 if pd.notna(incl) else np.nan,
            })
    df = pd.DataFrame(rows)
    if len(df):
        df["k"] = df["group_name"].map(key)
        df["eff_month"] = df["eff_date"].dt.to_period("M")
        df = df.drop_duplicates(subset=["k", "eff_month"], keep="first")
    return df


# ----------------------------- 7. Salesforce "Active Book by Broker" export

SF_COLMAP = {
    "State": "sf_state",
    "Associated Broker Account": "sf_broker",
    "TPA (Final Selection)": "sf_tpa",
    "Account Management Lead": "sf_am",
    "Type of Quote Requested": "sf_product",
    "Network (Final Selection)": "sf_network",
}


def extract_salesforce_book() -> tuple[pd.DataFrame, pd.DataFrame]:
    """From the Salesforce 'Active Book by Broker' export, build two tables:
      stable  - one row per group: identity/relationship attrs (state, broker,
                TPA, AM, product, network), matched into history by name.
      timevar - one row per (group, renewal YEAR): premium, lives and laser $
                from that year's real HPS/stop-loss opportunity. PBM-only and
                'PAP' pharmacy rows and $0 placeholders are excluded, and we keep
                the highest-premium opp per group-year. Matched by name + year,
                so a value only lands on the renewal it actually belongs to.
    Picks the most recent file matching '*Active Book*by Broker*'."""
    files = []
    for d in SRC_DIRS:
        files += list(d.glob("*Active Book*by Broker*.xls*"))
    files = sorted(set(files), key=lambda p: p.stat().st_mtime)
    if not files:
        return pd.DataFrame(), pd.DataFrame()
    path = files[-1]
    raw = pd.ExcelFile(path).parse(0, header=None)
    hdr = next((i for i in range(min(25, len(raw)))
                if any(str(v).strip() == "Opportunity Name" for v in raw.iloc[i])), None)
    if hdr is None:
        return pd.DataFrame(), pd.DataFrame()
    df = pd.ExcelFile(path).parse(0, header=hdr)
    if "Opportunity Name" not in df.columns:
        return pd.DataFrame(), pd.DataFrame()
    df = df[df["Opportunity Name"].notna()].copy()
    df["_sk"] = df["Opportunity Name"].map(key)
    df["_eff"] = pd.to_datetime(df.get("Effective Date"), errors="coerce")
    df = df[df["_sk"].str.len() > 0]

    def _num(col):
        return pd.to_numeric(df[col], errors="coerce") if col in df.columns else pd.Series(np.nan, index=df.index)

    # ---- stable identity/relationship attributes (latest, richest row per group)
    stable = pd.DataFrame({"_sk": df["_sk"], "eff": df["_eff"]})
    for src, dst in SF_COLMAP.items():
        stable[dst] = df[src].astype(str).str.strip() if src in df.columns else None
    stable = stable.replace({"nan": None, "": None, "None": None})
    stable["_rich"] = stable.notna().sum(axis=1)
    stable = (stable.sort_values(["eff", "_rich"], ascending=[False, False])
              .drop_duplicates(subset=["_sk"]).drop(columns=["eff", "_rich"]))

    # ---- time-varying values per (group, year) from the real renewal opp
    name = df["Opportunity Name"].astype(str)
    prod = (df["Type of Quote Requested"].astype(str).str.strip()
            if "Type of Quote Requested" in df.columns else pd.Series("", index=df.index))
    prem, mem = _num("RUS: Total Premium"), _num("No. of Total Members")
    laser_cols = [c for c in ("Laser 1: $ Amt", "Laser 2: $ Amt", "Laser 3: $ Amt") if c in df.columns]
    laser = (df[laser_cols].apply(pd.to_numeric, errors="coerce").sum(axis=1, min_count=1)
             if laser_cols else pd.Series(np.nan, index=df.index))
    real = (~name.str.match(r"\s*PAP", case=False) & ~prod.str.lower().eq("pbm only") & (prem > 0))
    tv = pd.DataFrame({
        "_sk": df["_sk"], "_yr": df["_eff"].dt.year,
        "sf_premium": prem, "sf_lives": mem.where(mem > 1), "sf_laser_liab": laser,
    })[real & df["_eff"].notna()]
    tv = tv.sort_values("sf_premium", ascending=False).drop_duplicates(["_sk", "_yr"])
    return stable, tv


# ----------------------- 8. RUS Group Data Export (AM Experience Report)

def extract_experience_report() -> pd.DataFrame:
    """Per (group, year) facts from the visible "Experience Table" sheet of the AM
    Experience Report (NOT the hidden RUSGrpDataExport/SL Premium/etc. backing
    sheets — Experience Table is the one Crumdale's team actually reviews, and its
    loss-ratio figures differ from the hidden sheets' same-named columns because
    they use a different premium denominator; Experience Table is the trusted one).
    Matched by name + year:
        Total Stop Loss Premium         -> stop-loss premium (Spec + Agg)
        Lives, Corridor                 -> lives, corridor
        Total Net Loss Ratio            -> nlr (combined Spec+Agg loss ratio)
        Aggregate Loss Ratio            -> agg_loss_ratio (Agg layer only)
        Ratio to Attachment             -> ratio_to_attachment (how close/over the
                                            aggregate attachment point claims are running)
    The three loss-ratio-family columns are all matured, POST-decision experience
    for the year they're dated, so the merge below only ever uses each group's
    PRIOR-year (expiring-policy) row to predict a renewal — never same-year — to
    stay leakage-safe. Policy Year Count / state aren't on this sheet (they lived
    on the old hidden-sheet source), so true-tenure/state backfill from this report
    is no longer available; tenure/state still come from every other source as before.
    Picks the most recent '*AM Experience Report*' file, searching the raw-data
    folder AND the project root (where a newer dated copy may be saved directly)."""
    files = []
    for d in (DATA, ROOT):
        files += list(d.glob("*AM Experience Report*.xls*"))
    files = sorted(set(files), key=lambda p: p.stat().st_mtime)
    if not files:
        return pd.DataFrame()
    try:
        raw = pd.ExcelFile(files[-1]).parse("Experience Table", header=None)
    except Exception:
        return pd.DataFrame()
    hdr_idx = next(
        (i for i in range(min(40, len(raw)))
         if "Group Name" in raw.iloc[i].astype(str).tolist()
         and any("Loss Ratio" in str(v) for v in raw.iloc[i].tolist())),
        None,
    )
    if hdr_idx is None:
        return pd.DataFrame()
    g = raw.iloc[hdr_idx + 1:].copy()
    g.columns = [str(c).strip() for c in raw.iloc[hdr_idx].tolist()]
    if "Group Name" not in g.columns or "Effective Date" not in g.columns:
        return pd.DataFrame()
    g = g[g["Group Name"].notna()]
    g["_sk"] = g["Group Name"].map(key)
    g["_eff"] = pd.to_datetime(g["Effective Date"], errors="coerce")
    g["_yr"] = g["_eff"].dt.year
    g = g[(g["_sk"].str.len() > 0) & g["_yr"].notna()]
    out = pd.DataFrame({
        "_sk": g["_sk"], "_yr": g["_yr"], "_eff": g["_eff"],
        # "Total Stop Loss Premium" = Specific + Aggregate premium (NOT total renewal
        # premium) -> goes to premium_stoploss, never annual_premium.
        "exp_premium_sl": pd.to_numeric(g.get("Total Stop Loss Premium"), errors="coerce"),
        "exp_lives": pd.to_numeric(g.get("Lives"), errors="coerce"),
        "exp_corridor": pd.to_numeric(g.get("Corridor"), errors="coerce"),
        "exp_nlr": pd.to_numeric(g.get("Total Net Loss Ratio"), errors="coerce"),
        "exp_agg_loss_ratio": pd.to_numeric(g.get("Aggregate Loss Ratio"), errors="coerce"),
        "exp_ratio_to_attachment": pd.to_numeric(g.get("Ratio to Attachment"), errors="coerce"),
    })
    out = out.replace({"nan": None, "": None, "None": None})
    out["_rich"] = out.notna().sum(axis=1)
    out = out.sort_values("_rich", ascending=False).drop_duplicates(["_sk", "_yr"]).drop(columns="_rich")
    return out


# --------------------- 9. Renewal Business 2026 (decided Won/Lost labels)

def extract_renewal_business() -> pd.DataFrame:
    """Decided 2026 renewals from the Salesforce 'Renewal Business 2026' export:
    Stage 'Closed Won' -> renewed, 'Closed Lost' -> not renewed. Open quote stages
    (Quote Request, Firm Quote Released, ...) are upcoming/undecided and skipped
    here. 'Subtotal' grouping rows are dropped. Picks the most recent file."""
    files = []
    for d in SRC_DIRS:
        if d.exists():
            files += list(d.glob("Renewal Business*.xls*"))
    files = sorted(set(files), key=lambda p: p.stat().st_mtime)
    if not files:
        return pd.DataFrame()

    def _ascii(s):  # Salesforce tacks a sort-arrow glyph onto header cells
        return "".join(ch for ch in str(s) if ord(ch) < 128).strip()

    raw = pd.ExcelFile(files[-1]).parse(0, header=None)
    hdr = next((i for i in range(min(25, len(raw)))
                if any(_ascii(v) == "Opportunity Name" for v in raw.iloc[i])), None)
    if hdr is None:
        return pd.DataFrame()
    df = pd.ExcelFile(files[-1]).parse(0, header=hdr)
    df.columns = [_ascii(c) for c in df.columns]
    if "Stage" not in df.columns or "Account Name" not in df.columns:
        return pd.DataFrame()
    # the report is a PIVOT: Effective Date + Stage are group headers, blank on the
    # rows beneath -> forward-fill them so every opportunity carries its date/stage.
    df["Effective Date"] = df["Effective Date"].ffill()
    df["Stage"] = df["Stage"].ffill()
    df = df[df["Account Name"].notna() & (df["Account Name"].astype(str).str.strip() != "Subtotal")].copy()
    stage = df["Stage"].astype(str).str.strip().str.lower()
    df = df[stage.isin(["closed won", "closed lost"])]
    if "Opportunity Record Type" in df.columns:  # keep renewals, drop new business
        rt = df["Opportunity Record Type"].astype(str).str.lower()
        df = df[~rt.str.contains("new business")]
    if df.empty:
        return pd.DataFrame()
    rows = pd.DataFrame({
        "group_name": df["Account Name"].astype(str).str.strip(),  # clean account = the group
        "eff_date": pd.to_datetime(df["Effective Date"], errors="coerce"),
        "renewed": (df["Stage"].astype(str).str.strip().str.lower() == "closed won").astype(float),
        "product": df["Type of Quote Requested"] if "Type of Quote Requested" in df.columns else None,
        "tpa": df["TPA (Final Selection)"] if "TPA (Final Selection)" in df.columns else None,
        "am": df["Account Management Lead"] if "Account Management Lead" in df.columns else None,
        "broker": df["Associated Broker"] if "Associated Broker" in df.columns else None,
        "lives": pd.to_numeric(df["No. of Enrolled Employees"], errors="coerce")
        if "No. of Enrolled Employees" in df.columns else np.nan,
        "source": "renewal2026",
    })
    # collapse quote variants of the same account-renewal (HPC/OC/Renewal) to one
    return rows.dropna(subset=["eff_date"]).drop_duplicates(subset=["group_name", "eff_date"])


def extract_renewal_upcoming() -> pd.DataFrame:
    """UPCOMING (open-stage) renewals from the Renewal Business export -> the Book
    of Business. Open stages = Quote Request / Firm Quote* / Illustrative* /
    Initial Sales Notification (not Closed). Needs a real effective date; 'Test'
    records excluded. No outcome (that's what we forecast)."""
    files = []
    for d in SRC_DIRS:
        if d.exists():
            files += list(d.glob("Renewal Business*.xls*"))
    files = sorted(set(files), key=lambda p: p.stat().st_mtime)
    if not files:
        return pd.DataFrame()

    def _ascii(s):
        return "".join(ch for ch in str(s) if ord(ch) < 128).strip()

    raw = pd.ExcelFile(files[-1]).parse(0, header=None)
    hdr = next((i for i in range(min(25, len(raw)))
                if any(_ascii(v) == "Opportunity Name" for v in raw.iloc[i])), None)
    if hdr is None:
        return pd.DataFrame()
    df = pd.ExcelFile(files[-1]).parse(0, header=hdr)
    df.columns = [_ascii(c) for c in df.columns]
    if "Stage" not in df.columns or "Account Name" not in df.columns:
        return pd.DataFrame()
    df["Effective Date"] = df["Effective Date"].ffill()  # pivot group headers
    df["Stage"] = df["Stage"].ffill()
    df = df[df["Account Name"].notna() & (df["Account Name"].astype(str).str.strip() != "Subtotal")].copy()
    stage = df["Stage"].astype(str).str.strip().str.lower()
    df = df[stage.str.contains("quote|sales notification", na=False) & ~stage.str.startswith("closed")]
    df["eff_date"] = pd.to_datetime(df["Effective Date"], errors="coerce")
    df = df[df["eff_date"].notna() & ~df["Account Name"].astype(str).str.contains("test", case=False)]
    if df.empty:
        return pd.DataFrame()
    out = pd.DataFrame({
        "group_name": df["Account Name"].astype(str).str.strip(),  # clean account = the group
        "eff_date": df["eff_date"],
        "lives": pd.to_numeric(df.get("No. of Enrolled Employees"), errors="coerce"),
        "broker": (df["Associated Broker"] if "Associated Broker" in df.columns else None),
        "am": (df["Account Management Lead"] if "Account Management Lead" in df.columns else None),
        "tpa": (df["TPA (Final Selection)"] if "TPA (Final Selection)" in df.columns else None),
        "product": (df["Type of Quote Requested"] if "Type of Quote Requested" in df.columns else None),
        "source": "renewal_upcoming",
    })
    # collapse quote variants (HPC/OC/Renewal) to one row per account-renewal
    return out.drop_duplicates(subset=["group_name", "eff_date"])


def extract_dec_jan_upcoming() -> pd.DataFrame:
    """'Upcoming Renewals Dec-Jan' Salesforce report — ACTIVE in-force business (Closed
    Won) whose renewal anniversary is coming up in December or January (per Katie's
    email). The file's Effective Date is the CURRENT policy; the upcoming renewal we
    forecast is ONE YEAR LATER. Lives/underwriting aren't in this report -> left blank,
    filled later from SF/experience where available. New-business rows are kept (they
    are first-time renewals coming up)."""
    files = []
    for d in SRC_DIRS:
        if d.exists():
            files += list(d.glob("Upcoming Renewals*.xls*"))
    files = sorted(set(files), key=lambda p: p.stat().st_mtime)
    if not files:
        return pd.DataFrame()

    def _ascii(s):
        return "".join(ch for ch in str(s) if ord(ch) < 128).strip()

    raw = pd.ExcelFile(files[-1]).parse(0, header=None)
    hdr = next((i for i in range(min(25, len(raw)))
                if any(_ascii(v) == "Account Name" for v in raw.iloc[i])), None)
    if hdr is None:
        return pd.DataFrame()
    df = pd.ExcelFile(files[-1]).parse(0, header=hdr)
    df.columns = [_ascii(c) for c in df.columns]
    if "Account Name" not in df.columns or "Effective Date" not in df.columns:
        return pd.DataFrame()
    df = df[df["Account Name"].notna() & (df["Account Name"].astype(str).str.strip() != "Subtotal")].copy()
    eff = pd.to_datetime(df["Effective Date"], errors="coerce")
    df = df[eff.notna() & ~df["Account Name"].astype(str).str.contains("test", case=False)]
    out = pd.DataFrame({
        "group_name": df["Account Name"].astype(str).str.strip(),
        # current policy eff date -> the UPCOMING renewal is the anniversary (+1 year)
        "eff_date": pd.to_datetime(df["Effective Date"], errors="coerce") + pd.DateOffset(years=1),
        "tpa": (df["TPA (Final Selection)"] if "TPA (Final Selection)" in df.columns else None),
        "product": (df["Type of Quote Requested"] if "Type of Quote Requested" in df.columns else None),
        "source": "upcoming_decjan",
    })
    return out.drop_duplicates(subset=["group_name", "eff_date"])


def extract_ihc_sl_renewals() -> pd.DataFrame:
    """Consolidated 'ALL IHC and SL Renewals' master (data team + Salesforce + CUW +
    AM documentation) of UPCOMING renewals, Feb 2026 -> Jan 2027. This is the org's
    authoritative CONTRACT-FACTS source: aggregate corridor, situs/state, carrier,
    TPA, ISL level, enrolled lives, renewal number (tenure), broker, product line and
    network. It carries NO experience (no loss ratio, rate increase or premium), so
    those stay sourced from the exec logs. Feeds the Book of Business ONLY (undecided
    upcoming renewals) — never the training history (no outcomes -> would be leakage)."""
    files = []
    for d in (ROOT, DATA):
        if d.exists():
            files += list(d.glob("ALL IHC and SL Renewals*.xls*"))
    files = [f for f in sorted(set(files), key=lambda p: p.stat().st_mtime)
             if not f.name.startswith("~$")]
    if not files:
        return pd.DataFrame()
    df = pd.read_excel(files[-1], sheet_name=0, header=0)
    df.columns = [str(c).strip() for c in df.columns]
    gcol = df.columns[0]                                    # "Group Name (Captive Request)"
    df = df[df[gcol].notna()]

    def _lives(r):
        v = to_num(r.get("Enrolled"))                       # true enrolled where numeric
        return v if pd.notna(v) else to_num(r.get("Sold Lives"))  # else sold-lives fallback

    def _product(r):
        f = str(r.get("Funding") or "").strip().upper()
        if f.startswith("LF"):
            return "HPS Level Funded"
        if f.startswith("PAYG"):
            return "Self Funded"
        return None

    rows = []
    for _, r in df.iterrows():
        eff = pd.to_datetime(r.get("Renewal Date"), errors="coerce")
        if pd.isna(eff):
            continue
        rows.append({
            "group_name": clean_group_name(r[gcol]),
            "eff_date": eff,
            "state": (str(r.get("Situs") or "").strip() or None),
            "corridor": to_num(r.get("Agg Corridor")),
            "carrier": (str(r.get("Carrier") or "").strip() or None),
            "tpa": (str(r.get("TPA") or "").strip() or None),
            "lives": _lives(r),
            "n_renewals": to_num(r.get("Renew #")),
            "broker": (str(r.get("SF Broker") or "").strip() or None),
            "am": (str(r.get("AM (Updates coming)") or "").strip() or None),
            "rsd": (str(r.get("RSD") or "").strip() or None),
            "product": _product(r),
            "isl_deductible": to_num(r.get("ISL level")),
            "network": (str(r.get("Network") or "").strip() or None),
            "source": "ihc_sl_master",
        })
    return pd.DataFrame(rows)


# ----------------------- 10. Stop Loss Carrier Changes (fills `carrier`)

def extract_carrier_changes() -> pd.DataFrame:
    """Stop-loss carrier per (account, year) from the 'Stop Loss Carrier Changes'
    Salesforce export — fills/standardizes the `carrier` column. Leakage-safe:
    the carrier is selected at renewal, before the decision."""
    files = []
    for d in SRC_DIRS:
        if d.exists():
            files += list(d.glob("Stop Loss Carrier Changes*.xls*"))
    files = sorted(set(files), key=lambda p: p.stat().st_mtime)
    if not files:
        return pd.DataFrame()

    def _ascii(s):
        return "".join(ch for ch in str(s) if ord(ch) < 128).strip()

    raw = pd.ExcelFile(files[-1]).parse(0, header=None)
    hdr = next((i for i in range(min(25, len(raw)))
                if any(_ascii(v) == "Account Name" for v in raw.iloc[i])), None)
    if hdr is None:
        return pd.DataFrame()
    df = pd.ExcelFile(files[-1]).parse(0, header=hdr)
    df.columns = [_ascii(c) for c in df.columns]
    if "Account Name" not in df.columns or "Stop Loss (Final Selection)" not in df.columns:
        return pd.DataFrame()
    df["Account Name"] = df["Account Name"].ffill()  # name blank on continuation rows
    df = df[df["Account Name"].astype(str).str.strip() != "Subtotal"]
    out = pd.DataFrame({
        "_sk": df["Account Name"].map(key),
        "_yr": pd.to_datetime(df.get("Effective Date"), errors="coerce").dt.year,
        "cc_carrier": df["Stop Loss (Final Selection)"].map(clean_carrier),
    })
    out = out[out["cc_carrier"].notna() & out["_yr"].notna() & (out["_sk"].str.len() > 0)]
    return out.drop_duplicates(["_sk", "_yr"])


# ----------------------- 11. Broker of Record Changes (sharpens bor_change)

def extract_bor_changes() -> pd.DataFrame:
    """Broker per (account, year) from the 'Broker of Record Changes' export.
    Used to fill `broker` and, since every row is a real BOR change, to mark
    `bor_change=True` as ground truth (vs our year-over-year inference)."""
    files = []
    for d in SRC_DIRS:
        if d.exists():
            files += list(d.glob("Broker of Record Changes*.xls*"))
    files = sorted(set(files), key=lambda p: p.stat().st_mtime)
    if not files:
        return pd.DataFrame()

    def _ascii(s):
        return "".join(ch for ch in str(s) if ord(ch) < 128).strip()

    raw = pd.ExcelFile(files[-1]).parse(0, header=None)
    hdr = next((i for i in range(min(25, len(raw)))
                if any(_ascii(v) == "Account Name" for v in raw.iloc[i])), None)
    if hdr is None:
        return pd.DataFrame()
    df = pd.ExcelFile(files[-1]).parse(0, header=hdr)
    df.columns = [_ascii(c) for c in df.columns]
    bcol = next((c for c in df.columns if c.startswith("Associated Broker Account")), None)
    if "Account Name" not in df.columns or bcol is None:
        return pd.DataFrame()
    df["Account Name"] = df["Account Name"].ffill()  # name blank on continuation rows
    df = df[df["Account Name"].astype(str).str.strip() != "Subtotal"]
    # the broker sits on the first row of its block; its per-year opportunity rows below
    # are blank -> carry it down WITHIN each account so every year keeps its broker.
    df[bcol] = df[bcol].replace(r"^\s*$", np.nan, regex=True)
    df[bcol] = df.groupby("Account Name")[bcol].ffill()
    out = pd.DataFrame({
        "_sk": df["Account Name"].map(key),
        "_yr": pd.to_datetime(df.get("Effective Date"), errors="coerce").dt.year,
        "bor_broker": df[bcol].astype(str).str.strip(),
    })
    out = out.replace({"nan": None, "": None})
    out = out[out["_yr"].notna() & (out["_sk"].str.len() > 0)]
    return out.drop_duplicates(["_sk", "_yr"])


# ----------------------- 12. AM Changes (fills `am`, derives am_changed)

def extract_am_changes() -> pd.DataFrame:
    """Account manager per (account, year) from the 'AM CHANGES' export -> fills
    `am` and feeds the am_changed derivation. Leakage-safe (the AM is known)."""
    files = []
    for d in SRC_DIRS:
        if d.exists():
            files += list(d.glob("AM CHANGES*.xls*")) + list(d.glob("AM Changes*.xls*"))
    files = sorted(set(files), key=lambda p: p.stat().st_mtime)
    if not files:
        return pd.DataFrame()

    def _ascii(s):
        return "".join(ch for ch in str(s) if ord(ch) < 128).strip()

    raw = pd.ExcelFile(files[-1]).parse(0, header=None)
    hdr = next((i for i in range(min(25, len(raw)))
                if any(_ascii(v) == "Account Name" for v in raw.iloc[i])), None)
    if hdr is None:
        return pd.DataFrame()
    df = pd.ExcelFile(files[-1]).parse(0, header=hdr)
    df.columns = [_ascii(c) for c in df.columns]
    amcol = next((c for c in df.columns if c.startswith("Account Management Lead")), None)
    if "Account Name" not in df.columns or amcol is None:
        return pd.DataFrame()
    df["Account Name"] = df["Account Name"].ffill()  # name blank on continuation rows
    df = df[df["Account Name"].astype(str).str.strip() != "Subtotal"]
    out = pd.DataFrame({
        "_sk": df["Account Name"].map(key),
        "_yr": pd.to_datetime(df.get("Effective Date"), errors="coerce").dt.year,
        "am_file": df[amcol].astype(str).str.strip(),
    })
    out = out.replace({"nan": None, "": None})
    out = out[out["_yr"].notna() & (out["_sk"].str.len() > 0) & out["am_file"].notna()]
    return out.drop_duplicates(["_sk", "_yr"])


# ----------------------- 13. Account History (Associated Broker audit log)

def extract_account_history_broker() -> pd.DataFrame:
    """Broker-of-record change events from the 'Account History Associated Broker'
    audit log (Field/Event = 'Associated Broker', Old Value -> New Value, edit
    date). Each row is a confirmed broker change -> sharpens bor_change and gives
    the current broker (New Value). Matched by account + edit-date year."""
    files = []
    for d in SRC_DIRS:
        if d.exists():
            files += list(d.glob("Account History Associated Broker*.xls*"))
    files = sorted(set(files), key=lambda p: p.stat().st_mtime)
    if not files:
        return pd.DataFrame()

    def _ascii(s):
        return "".join(ch for ch in str(s) if ord(ch) < 128).strip()

    raw = pd.ExcelFile(files[-1]).parse(0, header=None)
    hdr = next((i for i in range(min(25, len(raw)))
                if any(_ascii(v) == "Account Name" for v in raw.iloc[i])), None)
    if hdr is None:
        return pd.DataFrame()
    df = pd.ExcelFile(files[-1]).parse(0, header=hdr)
    df.columns = [_ascii(c) for c in df.columns]
    if "Account Name" not in df.columns or "Field / Event" not in df.columns:
        return pd.DataFrame()
    df["Account Name"] = df["Account Name"].ffill()
    df = df[(df["Account Name"].astype(str).str.strip() != "Subtotal")
            & (df["Field / Event"].astype(str).str.strip() == "Associated Broker")]
    out = pd.DataFrame({
        "_sk": df["Account Name"].map(key),
        "_yr": pd.to_datetime(df.get("Edit Date"), errors="coerce").dt.year,
        "ah_broker": (df["New Value"].astype(str).str.strip() if "New Value" in df.columns else None),
    })
    out = out.replace({"nan": None, "": None})
    out = out[out["_yr"].notna() & (out["_sk"].str.len() > 0)]
    return out.drop_duplicates(["_sk", "_yr"])


# ------------------------------------------------------------------- assembly

def derive_lineage(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Cross-year linkage on normalized name: first-seen year -> tenure floor,
    broker change year-over-year -> BOR proxy."""
    seen = pd.concat(
        [f[["group_name", "eff_date"]].assign(
            broker=f["broker"] if "broker" in f.columns else None)
         for f in frames],
        ignore_index=True,
    )
    seen["k"] = seen["group_name"].map(key)
    seen["year"] = seen["eff_date"].dt.year
    first_seen = seen.groupby("k")["year"].min().rename("first_seen_year")
    brokers = (
        seen.dropna(subset=["broker"])
        .sort_values("eff_date")
        .groupby(["k", "year"])["broker"].last()
        .rename("broker_of_year")
        .reset_index()
    )
    return first_seen, brokers


# state names + legal/organizational tails that are NOT part of a company's identity,
# used ONLY to collapse the same group appearing under two spellings across sources.
_MERGE_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
    "delaware", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa",
    "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan",
    "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada", "ohio",
    "oklahoma", "oregon", "pennsylvania", "tennessee", "texas", "utah", "vermont",
    "virginia", "washington", "wisconsin", "wyoming",
}
_MERGE_SUFFIXES = {
    "incorporated", "inc", "llc", "llp", "pllc", "ltd", "co", "corp", "corporation",
    "company", "pc", "pa", "group", "holdings", "holdco", "trustees",
}
_MERGE_STOP = _MERGE_SUFFIXES | _MERGE_STATES
# EXPERIENCE fields (the model's strongest predictors). A twin that carries these is the
# authoritative one to SCORE on; a twin missing them scores off imputed medians and looks
# falsely benign (e.g. Top Die Casting: exec-log twin 33% vs contract-sheet twin 74%).
_EXPERIENCE = ["nlr", "firm_increase_pct", "firm_increase_w_lasers_pct",
               "mature_to_attachment", "laser_count_detail"]


def _merge_tokens(s) -> set:
    """Identity tokens of a group name: drop parenthized notes, DBA tails, punctuation,
    a leading 'The', and legal/state tails. Two names for the SAME group reduce to
    overlapping token sets even when one carries extra descriptors ('Subaru', 'Trustees')."""
    s = str(s).lower().replace("&", " and ")
    s = re.sub(r"\(.*?\)", " ", s)
    s = s.split(" dba ")[0]
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"^\s*the\b", " ", s)
    return {w for w in s.split() if w not in _MERGE_STOP and len(w) > 1}


def _same_group(a: set, b: set) -> bool:
    """True when two token sets describe one group: one is a subset of the other (a bare
    name vs the same name + descriptors), or they overlap heavily (Jaccard >= 0.8).
    Deliberately conservative — 'Comprehensive Eye Professionals' vs 'Comprehensive
    Eyecare' do NOT merge (neither subset, Jaccard 0.25)."""
    if not a or not b:
        return False
    if a <= b or b <= a:
        return True
    return len(a & b) / len(a | b) >= 0.8


def collapse_active_duplicates(active: pd.DataFrame) -> pd.DataFrame:
    """Same upcoming renewal ingested from two sources under different spellings (exec log
    vs IHC/SL master) survives as two rows with different keys, so the (k, eff_month) merge
    never joins them — and each gets its OWN score. Collapse them per (identity, eff_month):
    keep the twin richest in EXPERIENCE fields (so we score on real underwriting, not
    imputed medians), then fill its blanks from the other twin (recovers contract facts like
    TPA / ISL / network the exec log lacks). Nothing is lost; the group appears once."""
    if not len(active):
        return active
    active = active.reset_index(drop=True)
    tokens = active["group_name"].map(_merge_tokens)
    drop_idx = []
    for _, grp in active.groupby("eff_month"):
        idx = list(grp.index)
        used = set()
        for a in range(len(idx)):
            if idx[a] in used:
                continue
            cluster = [idx[a]]
            for b in range(a + 1, len(idx)):
                if idx[b] in used:
                    continue
                if _same_group(tokens[idx[a]], tokens[idx[b]]):
                    cluster.append(idx[b])
                    used.add(idx[b])
            if len(cluster) == 1:
                continue
            # survivor = most non-null experience fields, tie-break on overall richness
            def _rank(i):
                exp = sum(int(pd.notna(active.at[i, c])) for c in _EXPERIENCE if c in active.columns)
                rich = int(active.loc[i].notna().sum())
                return (exp, rich)
            survivor = max(cluster, key=_rank)
            others = [i for i in cluster if i != survivor]
            for col in active.columns:
                if pd.isna(active.at[survivor, col]):
                    for o in others:
                        if pd.notna(active.at[o, col]):
                            active.at[survivor, col] = active.at[o, col]
                            break
            drop_idx.extend(others)
    if drop_idx:
        print(f"cross-source dedup: collapsed {len(drop_idx)} duplicate rows "
              f"({len(active) - len(drop_idx)} groups remain)")
        active = active.drop(index=drop_idx).reset_index(drop=True)
    return active


def main():
    print("Extracting 2025 UW renewal sheets...")
    uw25 = extract_2025()
    print(f"  rows: {len(uw25)}, outcomes known: {uw25['renewed'].notna().sum()}, "
          f"renewal rate: {uw25['renewed'].mean():.1%}")

    print("Extracting 2026 retention analysis...")
    ret26, detail26 = extract_2026()
    print(f"  HPS Groups rows: {len(ret26)}, outcomes known: {ret26['renewed'].notna().sum()}, "
          f"renewal rate: {ret26['renewed'].mean():.1%}")
    print(f"  cost-detail rows: {len(detail26)}")

    print("Extracting AM tracker...")
    trk = extract_tracker()
    print(f"  rows: {len(trk)}, outcomes known: {trk['renewed'].notna().sum()}, "
          f"pending: {trk['pending'].sum()}")

    # lineage across all sources
    first_seen, brokers = derive_lineage([uw25, ret26, trk])

    # --- enrich 2025 UW rows with tracker team/broker info (same group+month)
    uw25["k"] = uw25["group_name"].map(key)
    uw25["eff_month"] = uw25["eff_date"].dt.to_period("M")
    trk["k"] = trk["group_name"].map(key)
    trk["eff_month"] = trk["eff_date"].dt.to_period("M")
    trk_dedup = trk.drop_duplicates(subset=["k", "eff_month"])
    merged25 = uw25.merge(
        trk_dedup[["k", "eff_month", "broker", "am", "rsd", "product", "tpa",
                   "revision_requested", "renewed"]].rename(columns={"renewed": "renewed_trk"}),
        on=["k", "eff_month"], how="left",
    )
    recovered = (merged25["renewed"].isna() & merged25["renewed_trk"].notna()).sum()
    merged25["renewed"] = merged25["renewed"].fillna(merged25["renewed_trk"])
    merged25 = merged25.drop(columns=["renewed_trk"])
    match_rate = merged25["broker"].notna().mean()
    print(f"\n2025 UW rows matched to tracker (broker/team): {match_rate:.1%}")
    print(f"2025 outcomes recovered from tracker: {recovered}")

    # --- enrich 2026 rows with premium detail
    ret26["k"] = ret26["group_name"].map(key)
    ret26["eff_month"] = ret26["eff_date"].dt.to_period("M")
    merged26 = ret26.merge(
        detail26[["k", "eff_month", "annual_premium", "state", "lasers_current", "fixed_increase_pct"]],
        on=["k", "eff_month"], how="left",
    )
    print(f"2026 rows matched to cost detail (premium): {merged26['annual_premium'].notna().mean():.1%}")

    # --- tracker-only rows (2023-2024 + months not covered elsewhere)
    covered = set(map(tuple, pd.concat([
        merged25[["k", "eff_month"]], merged26[["k", "eff_month"]]
    ]).values))
    trk_only = trk[
        ~trk.apply(lambda r: (r["k"], r["eff_month"]) in covered, axis=1)
        & trk["renewed"].notna()
    ].copy()
    print(f"tracker-only decision rows (not in UW files): {len(trk_only)}")

    # --- decided 2026 renewals from the Renewal Business export (Won/Lost labels)
    rb = extract_renewal_business()
    if len(rb):
        rb["k"] = rb["group_name"].map(key)
        rb["eff_month"] = rb["eff_date"].dt.to_period("M")
        print(f"Renewal Business 2026 decided rows: {len(rb)} "
              f"(won {int((rb['renewed'] == 1).sum())} / lost {int((rb['renewed'] == 0).sum())})")

    # --- unify
    history = pd.concat([merged25, merged26, trk_only, rb], ignore_index=True)
    history = history[history["renewed"].notna()].copy()

    # derived tenure / BOR proxy
    history["year"] = history["eff_date"].dt.year
    history = history.merge(first_seen, on="k", how="left")
    derived_tenure = (history["year"] - history["first_seen_year"] + 1).clip(lower=1)
    if "tenure_years" not in history.columns:
        history["tenure_years"] = np.nan
    history["tenure_years"] = history["tenure_years"].fillna(derived_tenure)
    history["tenure_is_derived"] = history["source"] != "ret2026"

    prev_broker = brokers.copy()
    prev_broker["year"] += 1
    prev_broker = prev_broker.rename(columns={"broker_of_year": "prior_broker"})
    history = history.merge(prev_broker, on=["k", "year"], how="left")
    derived_bor = (
        history["broker"].notna() & history["prior_broker"].notna()
        & (history["broker"].map(norm_name) != history["prior_broker"].map(norm_name))
    )
    if "bor_change" not in history.columns:
        history["bor_change"] = np.nan
    history["bor_change"] = history["bor_change"].where(history["source"] == "ret2026", derived_bor)
    # confirmed = a real recorded change (2026 sheet field, or a change-log event),
    # vs inferred year-over-year. Drives the honest "estimated %" on the DQ page.
    history["bor_change_confirmed"] = history["source"] == "ret2026"

    # --- clean embedded notes from names, then collapse same-month duplicates.
    # Two rows are the same group when, within one renewal month, the normalized
    # name tokens of one are a subset of the other's AND their known outcomes
    # don't conflict. A multi-token core (>=2 words) matches on the name alone;
    # a single-token core ("Engrain") needs a corroborating broker / AM / size so
    # we never merge the several distinct "Buckeye*" companies. Richest row wins.
    history["group_name"] = history["group_name"].map(clean_group_name)
    history["richness"] = history.notna().sum(axis=1)
    history = history.sort_values("richness", ascending=False).reset_index(drop=True)

    nnorm = history["group_name"].map(norm_name).tolist()
    toks = [frozenset(n.split()) for n in nnorm]
    bkey = [norm_name(b) if isinstance(b, str) else "" for b in history["broker"]]
    amkey = [str(a).strip().lower() if pd.notna(a) else "" for a in history["am"]]
    lives = pd.to_numeric(history["lives"], errors="coerce").tolist()
    out = history["renewed"].tolist()
    months = history["eff_month"].tolist()

    parent = list(range(len(history)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)  # lower index = richer row = survivor

    def corroborated(i, j):
        if bkey[i] and bkey[i] == bkey[j]:
            return True
        if amkey[i] and amkey[i] == amkey[j]:
            return True
        li, lj = lives[i], lives[j]
        if pd.notna(li) and pd.notna(lj) and max(li, lj) > 0 and abs(li - lj) / max(li, lj) <= 0.25:
            return True
        return False

    by_month: dict = {}
    for i, m in enumerate(months):
        by_month.setdefault(m, []).append(i)
    for idxs in by_month.values():
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                i, j = idxs[a], idxs[b]
                ti, tj = toks[i], toks[j]
                if not ti or not tj:
                    continue
                if ti == tj:
                    union(i, j)
                    continue
                if pd.notna(out[i]) and pd.notna(out[j]) and out[i] != out[j]:
                    continue  # near-duplicate names with conflicting outcomes -> keep both
                strength = name_match(ti, tj, nnorm[i], nnorm[j])
                if strength == "strong" or (strength == "weak" and corroborated(i, j)):
                    union(i, j)

    history["_cluster"] = [find(i) for i in range(len(history))]
    n_before = len(history)
    history = (
        history.drop_duplicates(subset=["_cluster"])  # richness-sorted -> keeps richest
        .drop(columns=["_cluster", "richness"])
        .sort_values("eff_date")
        .reset_index(drop=True)
    )
    print(f"\nname cleanup: merged {n_before - len(history)} same-month duplicate rows "
          f"-> {len(history)} unique decisions")

    # --- negotiation detail (1.1.25 block)
    neg = extract_negotiation()
    history = history.merge(neg, on=["k", "eff_month"], how="left")
    print(f"\nnegotiation detail rows: {len(neg)}, matched into history: "
          f"{history['neg_final_increase_pct'].notna().sum()}")

    # --- executive logs (transposed v2.1 blocks)
    ex = extract_exec_logs()
    print(f"exec-log group-blocks parsed: {len(ex)} from "
          f"{ex['exec_file'].nunique() if len(ex) else 0} files")
    ex = resolve_exec_keys(ex, history)  # strict dynamic name-match to history groups
    ex_cols = ["n_renewals", "firm_increase_pct", "firm_increase_w_lasers_pct",
               "laser_count_detail", "nlr", "mature_to_attachment", "carrier",
               "broker", "rsd", "am", "product", "lives", "annual_premium",
               "corridor"]  # corridor kept (BoB display); isl_deductible/contract/prior_nlr dropped as useless
    ex_feats = ex[["k", "eff_month"] + ex_cols].rename(
        columns={c: f"ex_{c}" for c in ex_cols})
    history = history.merge(ex_feats, on=["k", "eff_month"], how="left")
    ex_match = history["ex_n_renewals"].notna().sum()
    print(f"history rows enriched from exec logs: {ex_match}")

    # The exec log is the AUTHORITATIVE block review. Where it has a value it WINS
    # for the underwriting fields (tenure, carrier, loss ratio, claims maturity, the
    # firm-renewal increases, lasers) — these are exactly the numbers the UW team
    # signed off on, and other sources (tracker/UW sheets) carried stale or
    # pre-laser versions. For identity/size fields it only fills gaps, so we don't
    # clobber a clean value with the exec's occasional working note.
    history["tenure_is_derived"] = history["ex_n_renewals"].isna() & history["tenure_is_derived"]
    for src, dst in [("ex_n_renewals", "tenure_years"), ("ex_carrier", "carrier"),
                     ("ex_nlr", "nlr"), ("ex_mature_to_attachment", "mature_to_attachment"),
                     ("ex_firm_increase_pct", "initial_uw_increase_pct"),
                     ("ex_firm_increase_w_lasers_pct", "total_increase_pct")]:
        history[dst] = history[src].fillna(history[dst])              # exec wins where present
    history["lasers_renewal"] = history["ex_laser_count_detail"].fillna(history["lasers_renewal"])
    for src, dst in [("ex_broker", "broker"), ("ex_rsd", "rsd"), ("ex_am", "am"),
                     ("ex_product", "product"), ("ex_lives", "lives")]:
        history[dst] = history[dst].fillna(history[src])              # exec fills gaps only
    # The exec's "Annualized Premium" is a STOP-LOSS basis, NOT the total renewal
    # premium in annual_premium. Keep it in its own column so the two never mix.
    history["premium_stoploss"] = history["ex_annual_premium"]
    history["corridor"] = history["ex_corridor"]  # exec-only; displayed in the BoB

    # --- Executive Renewal Tracker: fills the renewal-increase figure for decided
    # groups the exec logs didn't cover (broader monthly cohort, narrower field set --
    # see extract_renewal_tracker docstring). Exec log already handled above wins first;
    # this only fills whatever gap it left.
    rt = extract_renewal_tracker()
    if len(rt):
        history["_k"] = history["group_name"].map(key)
        history["_em"] = history["eff_date"].dt.to_period("M")
        rt_idx = rt.set_index(["k", "eff_month"])
        iuw_before = int(history["initial_uw_increase_pct"].notna().sum())
        tot_before = int(history["total_increase_pct"].notna().sum())
        rt_inc = pd.Series(
            [rt_idx["tracker_increase_pct"].get((k, em), np.nan)
             for k, em in zip(history["_k"], history["_em"])], index=history.index)
        rt_incl = pd.Series(
            [rt_idx["tracker_increase_w_lasers_pct"].get((k, em), np.nan)
             for k, em in zip(history["_k"], history["_em"])], index=history.index)
        history["initial_uw_increase_pct"] = history["initial_uw_increase_pct"].fillna(rt_inc)
        history["total_increase_pct"] = history["total_increase_pct"].fillna(rt_incl)
        history = history.drop(columns=["_k", "_em"])
        print(f"Executive Renewal Tracker: initial_uw_increase_pct "
              f"+{int(history['initial_uw_increase_pct'].notna().sum()) - iuw_before}, "
              f"total_increase_pct +{int(history['total_increase_pct'].notna().sum()) - tot_before}")

    # --- broker relationship attributes (broker-level, leakage-safe)
    brk = extract_broker_table()
    history = apply_broker_attrs(history, brk)
    print(f"broker attributes ({len(brk)} broker keys): history match "
          f"{history['broker_years_with_cs'].notna().mean():.1%}")

    # --- Salesforce Active Book: stable fields by name, time-varying by name+year
    sf_stable, sf_tv = extract_salesforce_book()
    if len(sf_stable) or len(sf_tv):
        cols = ("state", "broker", "tpa", "am", "product", "premium_stoploss", "lives", "laser_liability")
        before = {c: int(history[c].notna().sum()) for c in cols}
        history["_sk"] = history["group_name"].map(key)
        history["_yr"] = history["eff_date"].dt.year
        if len(sf_stable):
            history = history.merge(sf_stable, on="_sk", how="left")
            for src, dst in [("sf_state", "state"), ("sf_broker", "broker"),
                             ("sf_tpa", "tpa"), ("sf_am", "am"), ("sf_product", "product")]:
                history[dst] = history[dst].fillna(history[src])
            history["network"] = history.get("network", np.nan)
            history["network"] = history["network"].fillna(history["sf_network"])
            history = history.drop(columns=list(SF_COLMAP.values()))
        if len(sf_tv):
            history = history.merge(sf_tv, on=["_sk", "_yr"], how="left")
            # SF "RUS Total Premium" is a STOP-LOSS figure (RUS = the stop-loss system),
            # NOT the total renewal premium -> route to premium_stoploss, not annual_premium.
            history["premium_stoploss"] = history["premium_stoploss"].fillna(history["sf_premium"])
            history["lives"] = history["lives"].fillna(history["sf_lives"])
            history["laser_liability"] = history["laser_liability"].fillna(history["sf_laser_liab"])
            history = history.drop(columns=["sf_premium", "sf_lives", "sf_laser_liab"])
        history = history.drop(columns=["_sk", "_yr"])
        gained = {c: int(history[c].notna().sum() - before[c]) for c in cols}
        print(f"Salesforce book: stable {len(sf_stable)} groups, year-matched {len(sf_tv)} opps; "
              f"cells filled -> {gained}")

    # --- AM Experience Report (Experience Table sheet): STOP-LOSS premium/corridor/
    # lives (same-year), and the EXPIRING loss-ratio trio (prior-year, leakage-safe):
    # nlr, agg_loss_ratio, ratio_to_attachment. No tenure/state on this sheet (that
    # came from the old hidden-sheet source) — those still come from other extractors.
    exp = extract_experience_report()
    if len(exp):
        b = {c: int(history[c].notna().sum()) for c in ("premium_stoploss", "corridor", "lives")}
        history["_sk"] = history["group_name"].map(key)
        history["_yr"] = history["eff_date"].dt.year
        # (a) SAME-YEAR merge: facts about THIS renewal — stop-loss premium, corridor,
        #     lives are all known at renewal (not leakage).
        history = history.merge(exp, on=["_sk", "_yr"], how="left")
        history["premium_stoploss"] = history["premium_stoploss"].fillna(history["exp_premium_sl"])
        history["corridor"] = history["corridor"].fillna(history["exp_corridor"])
        history["lives"] = history["lives"].fillna(history["exp_lives"])
        history = history.drop(columns=["_sk", "_yr", "_eff", "exp_premium_sl", "exp_lives", "exp_corridor",
                                        "exp_nlr", "exp_agg_loss_ratio", "exp_ratio_to_attachment"])
        # (b) PRIOR-YEAR merge for the loss-ratio trio: the experience that was KNOWN at
        #     the renewal is the EXPIRING policy's. Match the exp row from (renewal year -
        #     1) so we never feed the model the post-decision experience of the policy
        #     it's predicting. RE-APPLIED 2026-07-22 (per explicit user direction, again):
        #     Experience Table wins outright for nlr too, overwriting exec-log/tracker --
        #     data correctness (Experience Table's rebated figure) over model-importance
        #     metrics. Known cost: this measurably pushes nlr's own permutation importance
        #     toward/below zero -- see model-loss-ratio-fixes/nlr-source-experience-table
        #     memory. agg_loss_ratio/ratio_to_attachment are unaffected either way (no
        #     other source to begin with).
        loss_cols = ["exp_nlr", "exp_agg_loss_ratio", "exp_ratio_to_attachment"]
        prior = exp[["_sk", "_yr"] + loss_cols].copy()
        prior["_yr"] = prior["_yr"] + 1                       # this row predicts NEXT year's renewal
        prior = prior.rename(columns={
            "exp_nlr": "exp_prior_nlr",
            "exp_agg_loss_ratio": "agg_loss_ratio",
            "exp_ratio_to_attachment": "ratio_to_attachment",
        }).drop_duplicates(["_sk", "_yr"])
        history["_sk"] = history["group_name"].map(key)
        history["_yr"] = history["eff_date"].dt.year
        history = history.merge(prior, on=["_sk", "_yr"], how="left")
        nlr_before = int(history["nlr"].notna().sum())
        nlr_overwritten = int((history["exp_prior_nlr"].notna() & history["nlr"].notna()).sum())
        history["nlr"] = history["exp_prior_nlr"].fillna(history["nlr"])
        history = history.drop(columns=["_sk", "_yr", "exp_prior_nlr"])
        gained = {c: int(history[c].notna().sum() - b[c]) for c in b}
        print(f"AM Experience Report (Experience Table): nlr "
              f"+{int(history['nlr'].notna().sum()) - nlr_before} new, {nlr_overwritten} existing "
              f"exec-log/tracker values overwritten (prior-yr, leakage-safe), "
              f"agg_loss_ratio {int(history['agg_loss_ratio'].notna().sum())}/{len(history)}, "
              f"ratio_to_attachment {int(history['ratio_to_attachment'].notna().sum())}/{len(history)}, "
              f"cells filled -> {gained}")

    # --- Stop Loss Carrier Changes: fill `carrier` (name+year), then standardize all
    cc = extract_carrier_changes()
    history["_cc_match"] = False
    if len(cc):
        before = int(history["carrier"].notna().sum())
        history["_sk"] = history["group_name"].map(key)
        history["_yr"] = history["eff_date"].dt.year
        history = history.merge(cc, on=["_sk", "_yr"], how="left")
        history["_cc_match"] = history["cc_carrier"].notna()  # explicit change events (ground truth)
        history["carrier"] = history["carrier"].fillna(history["cc_carrier"])
        history = history.drop(columns=["_sk", "_yr", "cc_carrier"])
        print(f"carrier-changes file: filled {int(history['carrier'].notna().sum()) - before} carrier cells")
    history["carrier"] = history["carrier"].map(clean_carrier)  # standardize (resolve 'X to Y')

    # --- carrier_changed (tri-state): did the stop-loss carrier differ from the
    # prior year? Ground-truth from the changes file, else derived year-over-year.
    # NaN where we can't tell, so the model never sees a forced "no".
    hc = history[["group_name", "eff_date", "carrier"]].copy()
    hc["_pk"] = hc["group_name"].map(key)
    hc["_pyr"] = hc["eff_date"].dt.year + 1  # this row is the prior year for _pyr
    prior_c = (hc[["_pk", "_pyr", "carrier"]].rename(columns={"carrier": "_prior_carrier"})
               .drop_duplicates(["_pk", "_pyr"], keep="last"))  # one prior carrier per group-year (no fan-out)
    history["_pk"] = history["group_name"].map(key)
    history["_pyr"] = history["eff_date"].dt.year
    history = history.merge(prior_c, on=["_pk", "_pyr"], how="left")
    cur_c = history["carrier"].map(lambda v: norm_name(v) if isinstance(v, str) else None)
    pri_c = history["_prior_carrier"].map(lambda v: norm_name(v) if isinstance(v, str) else None)
    both_c = cur_c.notna() & pri_c.notna()
    cc = pd.Series(pd.NA, index=history.index, dtype="object")  # tri-state, no float coercion
    cc[both_c] = cur_c[both_c].ne(pri_c[both_c])
    cc[history["_cc_match"] == True] = True  # explicit = changed
    history["carrier_changed"] = cc
    history = history.drop(columns=["_pk", "_pyr", "_prior_carrier", "_cc_match"])
    print(f"carrier_changed: known for {int(pd.notna(history['carrier_changed']).sum())} rows "
          f"(changed={int((history['carrier_changed'] == True).sum())})")

    # --- Broker of Record Changes: fill broker + mark real BOR changes as ground truth
    bc = extract_bor_changes()
    if len(bc):
        bfill = int(history["broker"].notna().sum())
        bchg = int((history["bor_change"] == True).sum())
        history["_sk"] = history["group_name"].map(key)
        history["_yr"] = history["eff_date"].dt.year
        history = history.merge(bc, on=["_sk", "_yr"], how="left")
        history["broker"] = history["broker"].fillna(history["bor_broker"])
        matched_bc = history["bor_broker"].notna()
        history.loc[matched_bc, "bor_change"] = True            # explicit BOR change
        history.loc[matched_bc, "bor_change_confirmed"] = True
        history = history.drop(columns=["_sk", "_yr", "bor_broker"])
        print(f"BOR-changes file: broker +{int(history['broker'].notna().sum()) - bfill}, "
              f"bor_change=True {bchg} -> {int((history['bor_change'] == True).sum())} (confirmed events)")

    # --- Account History (Associated Broker audit log): more confirmed BOR changes
    ah = extract_account_history_broker()
    if len(ah):
        bchg = int((history["bor_change"] == True).sum())
        bfill = int(history["broker"].notna().sum())
        history["_sk"] = history["group_name"].map(key)
        history["_yr"] = history["eff_date"].dt.year
        history = history.merge(ah, on=["_sk", "_yr"], how="left")
        history["broker"] = history["broker"].fillna(history["ah_broker"])
        matched_ah = history["ah_broker"].notna()
        history.loc[matched_ah, "bor_change"] = True
        history.loc[matched_ah, "bor_change_confirmed"] = True
        history = history.drop(columns=["_sk", "_yr", "ah_broker"])
        print(f"Account History broker log: broker +{int(history['broker'].notna().sum()) - bfill}, "
              f"bor_change=True {bchg} -> {int((history['bor_change'] == True).sum())}")

    # --- Manual "BOR change chart" (screenshot in All-Data): 12 verified broker-of-record
    # changes. Mark each matched group's MOST RECENT renewal as a confirmed BOR change.
    bchg0 = int((history["bor_change"] == True).sum())
    gl = history["group_name"].astype(str).str.lower()
    matched_groups = 0
    for frag in SCREENSHOT_BOR_CHANGES:
        hits = history.index[gl.str.contains(frag, regex=False, na=False)]
        if len(hits):
            latest = history.loc[hits].sort_values("eff_date").index[-1]
            history.loc[latest, "bor_change"] = True
            history.loc[latest, "bor_change_confirmed"] = True
            matched_groups += 1
    print(f"manual BOR-change chart (screenshot): {matched_groups}/{len(SCREENSHOT_BOR_CHANGES)} groups "
          f"matched, bor_change=True {bchg0} -> {int((history['bor_change'] == True).sum())}")

    # --- AM Changes: fill `am` (current account manager) + derive am_changed
    amc = extract_am_changes()
    if len(amc):
        afill = int(history["am"].notna().sum())
        history["_sk"] = history["group_name"].map(key)
        history["_yr"] = history["eff_date"].dt.year
        history = history.merge(amc, on=["_sk", "_yr"], how="left")
        history["am"] = history["am"].fillna(history["am_file"])
        history = history.drop(columns=["_sk", "_yr", "am_file"])
        print(f"AM-changes file: am filled +{int(history['am'].notna().sum()) - afill}")
    # "(from X)" literally means the AM was transferred this year = a confirmed change
    am_marker = history["am"].astype(str).str.contains(r"\(from|supported by|transferred", case=False, na=False)
    history["am"] = history["am"].map(clean_am)  # standardize to the current AM
    # am_changed (tri-state): explicit transfer marker, else cleaned AM differs YoY
    ha = history[["group_name", "eff_date", "am"]].copy()
    ha["_pk"] = ha["group_name"].map(key)
    ha["_pyr"] = ha["eff_date"].dt.year + 1
    prior_a = (ha[["_pk", "_pyr", "am"]].rename(columns={"am": "_prior_am"})
               .drop_duplicates(["_pk", "_pyr"], keep="last"))  # one prior AM per group-year (no fan-out)
    history["_pk"] = history["group_name"].map(key)
    history["_pyr"] = history["eff_date"].dt.year
    history = history.merge(prior_a, on=["_pk", "_pyr"], how="left")
    am_marker = am_marker.reindex(history.index, fill_value=False)  # realign after merge
    cur_a = history["am"].map(lambda v: norm_name(v) if isinstance(v, str) else None)
    pri_a = history["_prior_am"].map(lambda v: norm_name(v) if isinstance(v, str) else None)
    both_a = cur_a.notna() & pri_a.notna()
    ac = pd.Series(pd.NA, index=history.index, dtype="object")  # tri-state, no float coercion
    ac[both_a] = cur_a[both_a].ne(pri_a[both_a])
    ac[am_marker] = True  # explicit transfer = confirmed change
    history["am_changed"] = ac
    history = history.drop(columns=["_pk", "_pyr", "_prior_am"])
    print(f"am_changed: known for {int(pd.notna(history['am_changed']).sum())} rows "
          f"(changed={int((history['am_changed'] == True).sum())})")

    # --- drop PBM-only opportunities: a separate line of business, not HPS
    # medical stop-loss. Bundled "PBM with Stop Loss" / "Stop Loss with PBM" stay.
    pbm_only = history["product"].astype(str).str.strip().str.lower() == "pbm only"
    if pbm_only.any():
        print(f"removing {int(pbm_only.sum())} PBM-only rows (separate line of business)")
        history = history[~pbm_only].reset_index(drop=True)

    keep = [
        "group_name", "eff_date", "source", "renewed", "product", "broker", "rsd", "am",
        "tpa", "carrier", "state", "lives", "annual_premium", "premium_stoploss", "nlr", "isl_loss_ratio",
        "mature_to_attachment", "fixed_increase_pct", "total_increase_pct",
        "initial_uw_increase_pct", "lasers_current", "lasers_renewal", "laser_liability",
        "captive_offer", "tenure_years", "tenure_is_derived", "bor_change",
        "neg_initial_increase_pct", "neg_final_increase_pct",
        "broker_groups_with_cs", "broker_years_with_cs",
        "broker_products_sold", "broker_preferred", "network", "carrier_changed", "am_changed",
        "bor_change_confirmed",
        "corridor",  # displayed in the BoB (premium_stoploss already kept above)
        "agg_loss_ratio", "ratio_to_attachment",
    ]
    for c in keep:
        if c not in history.columns:
            history[c] = np.nan
    history = history[keep]
    history = sanitize_lives(history)

    # Preserve manually-transferred decided groups (source == "manual_transfer") from
    # any existing real_history.csv. A full ETL rebuild has no way to know about them —
    # they're button-driven moves from Upcoming Renewals (real_mode.move_many_to_history),
    # not sourced from any raw file this script reads — so without this, every rebuild
    # would silently erase every group a user has moved into the Renewal Database. Only
    # carry forward rows whose (group_name, eff_date) isn't already in the freshly-built
    # history, so a manual transfer never duplicates or shadows a fresher raw-source row.
    if OUT.exists():
        prev = pd.read_csv(OUT, parse_dates=["eff_date"])
        prev_manual = prev[prev.get("source") == "manual_transfer"].copy()
        if len(prev_manual):
            existing_keys = set(zip(history["group_name"].map(key), history["eff_date"]))
            prev_manual["_key"] = list(zip(prev_manual["group_name"].map(key), prev_manual["eff_date"]))
            prev_manual = prev_manual[~prev_manual["_key"].isin(existing_keys)].drop(columns="_key")
        if len(prev_manual):
            for c in keep:
                if c not in prev_manual.columns:
                    prev_manual[c] = np.nan
            history = pd.concat([history, prev_manual[keep]], ignore_index=True)
            print(f"preserved {len(prev_manual)} manually-transferred group(s) from prior real_history.csv")

    # consistent boolean representation across the file: 1 / 0 / blank
    for c in ("renewed", "bor_change", "captive_offer",
              "tenure_is_derived", "carrier_changed", "am_changed", "broker_preferred",
              "bor_change_confirmed"):
        if c in history.columns:
            history[c] = history[c].map(_to01).astype("Int64")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    history.to_csv(OUT, index=False)

    # --- real active book: exec-log undecided blocks + Renewal Business upcoming
    decided_km = set(zip(history["group_name"].map(key),
                         history["eff_date"].dt.to_period("M")))
    frames = []
    if len(ex):
        a = ex[(ex["eff_date"] >= pd.Timestamp(2026, 7, 1)) & ~ex["decided_marker"]].copy()
        trk_outcomes = trk[trk["renewed"].notna()][["k", "eff_month", "renewed"]]
        a = a.merge(trk_outcomes, on=["k", "eff_month"], how="left")
        a = a[a["renewed"].isna()].drop(columns=["renewed"])
        frames.append(a)
    up = extract_renewal_upcoming()
    if len(up):
        up = up[up["eff_date"] >= pd.Timestamp(2026, 7, 1)].copy()
        up["k"] = up["group_name"].map(key)
        up["eff_month"] = up["eff_date"].dt.to_period("M")
        up = up[~up.apply(lambda r: (r["k"], r["eff_month"]) in decided_km, axis=1)]  # not already decided
        frames.append(up)
        print(f"Renewal Business upcoming (open stage, dated): {len(up)} groups -> Book of Business")
    dj = extract_dec_jan_upcoming()
    if len(dj):
        dj = dj[dj["eff_date"] >= pd.Timestamp(2026, 7, 1)].copy()
        dj["k"] = dj["group_name"].map(key)
        dj["eff_month"] = dj["eff_date"].dt.to_period("M")
        dj = dj[~dj.apply(lambda r: (r["k"], r["eff_month"]) in decided_km, axis=1)]  # not already decided
        frames.append(dj)
        print(f"Dec/Jan active-business renewals (anniversary = eff+1yr): {len(dj)} groups -> Book of Business")
    ihc_facts = pd.DataFrame()
    ihc = extract_ihc_sl_renewals()
    if len(ihc):
        ih = ihc[ihc["eff_date"] >= pd.Timestamp(2026, 7, 1)].copy()
        ih["k"] = ih["group_name"].map(key)
        ih["eff_month"] = ih["eff_date"].dt.to_period("M")
        ih = ih[~ih.apply(lambda r: (r["k"], r["eff_month"]) in decided_km, axis=1)]  # not already decided
        ih["decided_marker"] = False
        ihc_facts = ih                       # retained for the authoritative overwrite below
        frames.append(ih)                    # appended last -> exec/SF win the dedup; IHC adds NEW groups
        print(f"IHC/SL master upcoming (Feb26-Jan27): {len(ih)} groups -> Book of Business")
    if frames:
        active = pd.concat(frames, ignore_index=True)
        active = active.drop_duplicates(subset=["k", "eff_month"], keep="first")  # exec-log preferred

        # --- enrich upcoming renewals with REAL, CURRENT facts so the Book of
        # Business shows VALID numbers (not blanks) wherever the data genuinely exists:
        #   premium <- SF active book (current annual premium), AM exp report fallback
        #   tenure  <- AM Experience Report 'Policy Year Count' (true tenure)
        #   lives   <- exp report fallback;   state <- SF active book / exp report
        # STRICT recency: a year-varying value (premium, tenure, lives) is used ONLY
        # when its source policy year is the renewal year or the one just before it
        # (the expiring policy). Anything older is stale — premiums and tenure drift
        # every year — so we leave it BLANK rather than show an out-of-date figure as
        # if it were current. Loss ratio & rate increase stay blank for not-yet-quoted
        # groups (no model-consistent pre-decision value; they load from the UW sheet).
        active["_sk"] = active["group_name"].map(key)
        active["_ryr"] = active["eff_date"].dt.year

        def _recent(table: pd.DataFrame, col: str) -> pd.Series:
            """Per active-row value from `table` (keyed _sk,_yr) taken ONLY from the
            renewal year or the year before it (the current/expiring policy)."""
            if not len(table) or col not in table.columns:
                return pd.Series(np.nan, index=active.index)
            t = table.dropna(subset=[col]).drop_duplicates(["_sk", "_yr"]).set_index(["_sk", "_yr"])[col]
            vals = []
            for sk, ryr in zip(active["_sk"], active["_ryr"]):
                v = t.get((sk, ryr))                 # renewal-year opp (uncommon)
                if v is None or pd.isna(v):
                    v = t.get((sk, ryr - 1))         # expiring policy (the usual match)
                vals.append(v if (v is not None and pd.notna(v)) else np.nan)
            return pd.Series(vals, index=active.index)

        def _prior(table: pd.DataFrame, col: str) -> pd.Series:
            """STRICT prior-year only — the EXPIRING policy. Used for the loss ratio so
            we never feed the BoB the post-decision experience of the renewal we predict.

            MATURITY GATE: a policy year's loss ratio isn't real until that policy's
            own 12-month term has actually finished — claims take months to be
            reported and paid, so a term that's still mid-flight shows near-zero
            losses simply because they haven't happened/been reported yet, not
            because the group is healthy. Confirmed on Plumbers & Pipefitters Local
            625's Jan-2026 term (2026-08-25): 0.0 nlr/agg vs. 3.3/5.5 and 2.3/4.7 the
            two years before — an immature term, not an improved one. So a row is
            only usable here once `_eff` + 12 months has already passed; otherwise
            it's excluded and the caller gets NaN (blank), never that misleadingly
            low in-flight number."""
            if not len(table) or col not in table.columns:
                return pd.Series(np.nan, index=active.index)
            if "_eff" in table.columns:
                mature = table["_eff"] + pd.DateOffset(months=12) <= pd.Timestamp.now()
                table = table[mature]
            t = table.dropna(subset=[col]).drop_duplicates(["_sk", "_yr"]).set_index(["_sk", "_yr"])[col]
            return pd.Series([t.get((sk, ryr - 1), np.nan) for sk, ryr in zip(active["_sk"], active["_ryr"])],
                             index=active.index)

        # Premium for upcoming renewals = STOP-LOSS premium (no total renewal premium
        # exists pre-quote): SF "RUS Total" + exp "Total Stop Loss Premium".
        active["annual_premium"] = active["annual_premium"].fillna(_recent(sf_tv, "sf_premium"))
        active["annual_premium"] = active["annual_premium"].fillna(_recent(exp, "exp_premium_sl"))
        active["premium_stoploss"] = active["annual_premium"]   # BoB premium is the stop-loss basis
        active["lives"] = active["lives"].fillna(_recent(exp, "exp_lives"))
        # loss-ratio trio <- EXPIRING year's experience (leakage-safe); corridor <- current/expiring.
        # tenure/state no longer come from this report (not on Experience Table) —
        # they still get filled by every other source in this pipeline as before.
        # RE-APPLIED 2026-07-22 (matches history-side re-apply above): Experience Table
        # wins outright for nlr again, overwriting whatever exec-log nlr the row carried.
        if "nlr" not in active.columns:
            active["nlr"] = np.nan
        active["nlr"] = _prior(exp, "exp_nlr").fillna(active["nlr"])
        active["agg_loss_ratio"] = _prior(exp, "exp_agg_loss_ratio")
        active["ratio_to_attachment"] = _prior(exp, "exp_ratio_to_attachment")
        if "corridor" not in active.columns:
            active["corridor"] = np.nan
        active["corridor"] = active["corridor"].fillna(_recent(exp, "exp_corridor"))
        # state/broker/carrier are stable identity (not year-varying) -> safe to keep
        st = (active["_sk"].map(sf_stable.set_index("_sk")["sf_state"])
              if len(sf_stable) else pd.Series(pd.NA, index=active.index))
        active["state"] = st
        active = active.drop(columns=["_sk", "_ryr"])
        print(f"upcoming enriched (STRICT): premium "
              f"{int(active['annual_premium'].notna().sum())}/{len(active)}, "
              f"tenure {int(active['n_renewals'].notna().sum())}/{len(active)}, "
              f"loss-ratio {int(active['nlr'].notna().sum())}/{len(active)}, "
              f"corridor {int(active['corridor'].notna().sum())}/{len(active)}")

        # --- Executive Renewal Tracker: same fallback-fill for the increase figure,
        # for upcoming groups the exec logs don't cover yet (see extract_renewal_tracker).
        if len(rt):
            rt_idx = rt.set_index(["k", "eff_month"])
            rt_inc = pd.Series(
                [rt_idx["tracker_increase_pct"].get((k, em), np.nan)
                 for k, em in zip(active["k"], active["eff_month"])], index=active.index)
            rt_incl = pd.Series(
                [rt_idx["tracker_increase_w_lasers_pct"].get((k, em), np.nan)
                 for k, em in zip(active["k"], active["eff_month"])], index=active.index)
            if "firm_increase_pct" not in active.columns:
                active["firm_increase_pct"] = np.nan
            if "firm_increase_w_lasers_pct" not in active.columns:
                active["firm_increase_w_lasers_pct"] = np.nan
            inc_before = int(active["firm_increase_pct"].notna().sum())
            active["firm_increase_pct"] = active["firm_increase_pct"].fillna(rt_inc)
            active["firm_increase_w_lasers_pct"] = active["firm_increase_w_lasers_pct"].fillna(rt_incl)
            print(f"Executive Renewal Tracker (upcoming): firm_increase_pct "
                  f"+{int(active['firm_increase_pct'].notna().sum()) - inc_before}/{len(active)}")

        # --- IHC/SL master: authoritative CONTRACT FACTS for the Book of Business.
        # The consolidated master wins for the terms it owns (state, corridor, carrier,
        # TPA, ISL, lives, tenure, network); identity fields (broker/AM/RSD/product) only
        # FILL blanks so we never clobber a known-good exec value. Loss ratio / increase /
        # premium are untouched — the master doesn't carry them (they stay from the exec
        # logs). This runs AFTER the state assignment so it restores IHC state on the new
        # groups that the SF/exp state step blanks.
        if len(ihc_facts):
            im = ihc_facts.drop_duplicates(["k", "eff_month"]).set_index(["k", "eff_month"])
            akey = list(zip(active["k"], active["eff_month"]))
            OVERWRITE = ["state", "corridor", "carrier", "tpa", "lives", "n_renewals",
                         "isl_deductible", "network"]
            FILL = ["broker", "am", "rsd", "product"]
            for col in OVERWRITE + FILL:
                if col not in im.columns:
                    continue
                src = im[col].to_dict()
                newv = [src.get(kk, np.nan) for kk in akey]
                if col not in active.columns:
                    active[col] = np.nan
                if col in OVERWRITE:          # master wins where it has a value
                    active[col] = [nv if pd.notna(nv) else old
                                   for nv, old in zip(newv, active[col])]
                else:                         # fill blanks only (keep exec/SF value)
                    active[col] = [old if pd.notna(old) else nv
                                   for nv, old in zip(newv, active[col])]
            active["premium_stoploss"] = active["annual_premium"]  # re-sync (lives may have changed)
            n_hit = sum(1 for kk in akey if kk in im.index)
            print(f"IHC/SL master: contract facts applied to {n_hit} book groups")

        active = apply_broker_attrs(active, brk)
        active = collapse_active_duplicates(active)   # same group, two source spellings -> one scored row
        active = sanitize_lives(active)
        active_out = OUT.parent / "real_active_book.csv"
        active.to_csv(active_out, index=False)
        print(f"\nREAL ACTIVE BOOK (undecided, eff >= Jul 2026): {len(active)} groups "
              f"-> {active_out.name}")

    # --- term reasons reference table (for the dashboard, not for training)
    reasons = pd.read_excel(DATA / "2025 Non Renewals.xlsx", sheet_name="Reasons", header=1)
    reasons.columns = [str(c).strip() for c in reasons.columns]
    reasons = reasons[reasons["Group Name"].notna()]
    reasons_out = OUT.parent / "term_reasons.csv"
    reasons.to_csv(reasons_out, index=False)
    print(f"term reasons reference: {len(reasons)} rows -> {reasons_out.name}")

    print(f"\n{'=' * 60}\nUNIFIED REAL HISTORY: {len(history)} renewal decisions -> {OUT.name}")
    print(f"date range: {history['eff_date'].min().date()} .. {history['eff_date'].max().date()}")
    print(f"overall renewal rate: {history['renewed'].mean():.1%}")
    print(f"\nby source:\n{history['source'].value_counts().to_string()}")
    print("\nfield coverage (% non-null):")
    for c in keep:
        if c in ("group_name", "eff_date", "source", "renewed"):
            continue
        print(f"  {c:<26} {history[c].notna().mean():6.1%}")


if __name__ == "__main__":
    main()
