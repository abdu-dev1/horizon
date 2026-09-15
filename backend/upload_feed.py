"""
The single-file upload feed: one Excel/CSV template that replaces the scattered,
source-specific extractors in etl_real.py for everything going forward.

Two feeds, same field set (see ModelMaintenance.jsx, which documents them to the user):

  Feed 1 "upcoming"  -> renewals to FORECAST. No outcome column.
                        Lands in data/uploaded_upcoming.csv, which build_book.py
                        reads as a feature source (highest priority - it's the
                        freshest human-supplied data) and, for groups Salesforce
                        has no deal for, backfills into the book directly.
  Feed 2 "outcomes"  -> renewals that have DECIDED. Same fields + outcome.
                        Appends to data/real_history.csv (source='uploaded_feed'),
                        i.e. straight into the model's training data.

Nothing here re-implements scoring, name matching or de-dup: parsed rows are handed
to the existing build_book.py / train_real.py path exactly like every other source.

Honesty rules this file enforces (see the data-honesty notes in real_mode.py):
  * A blank cell stays blank. Never zero-filled, never imputed here.
  * Every coercion that CHANGES a typed value (e.g. "62" read as 62% -> 0.62) is
    reported back as a warning in the preview, so an import is never silently
    reinterpreted. Preview is mandatory before commit.
"""

from __future__ import annotations

import io
import re
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# pandas warns about future dtype-inference changes when concatenating an
# all-NaN block onto real_history.csv (e.g. a freshly uploaded row that leaves
# most optional columns blank, by design). Same call etl_real.py already makes.
warnings.filterwarnings("ignore", category=FutureWarning)

from app.paths import backend_dir  # noqa: E402  (writable/persistent root - see app/paths.py)

# BACKEND == the source backend/ dir in dev; redirected by
# HORIZON_RENEWAL_DATA_DIR in a container.
BACKEND = backend_dir()
DATA = BACKEND / "data"
UPLOADED_UPCOMING = DATA / "uploaded_upcoming.csv"
HISTORY = DATA / "real_history.csv"
UPLOAD_ARCHIVE = DATA / "uploads"

FEEDS = ("upcoming", "outcomes")

# (canonical column, friendly header in the template, example, what it is)
# Canonical names match real_history.csv exactly, so a parsed frame drops straight
# into the existing pipeline with no second mapping layer.
FIELDS: list[tuple[str, str, str, str]] = [
    ("group_name", "Group Name", "Acme Manufacturing LLC", "Who is renewing. REQUIRED."),
    ("eff_date", "Renewal Effective Date", "2026-10-01", "When the renewal takes effect. REQUIRED."),
    ("product", "Product", "HPS Level Funded", "Level Funded, Self Funded, or Stop Loss Only."),
    ("lives", "Enrolled Lives", "84", "Current employee count."),
    ("annual_premium", "Annual Premium", "612000", "Annualized premium, in dollars."),
    ("premium_stoploss", "Stop-Loss Premium", "612000", "ISL/stop-loss premium, in dollars."),
    ("nlr", "Net Loss Ratio (decimal)", "0.62", "Claims experience with rebates. 0.62 = 62%."),
    ("isl_loss_ratio", "ISL Loss Ratio (decimal)", "0.55", "Individual stop-loss loss ratio."),
    ("agg_loss_ratio", "Aggregate Loss Ratio (decimal)", "0.48", "From the Experience Table."),
    ("ratio_to_attachment", "Ratio to Attachment (decimal)", "0.93", "From the Experience Table."),
    ("mature_to_attachment", "Mature Claims to Attachment (decimal)", "1.08", "Claims maturity vs funding level."),
    ("total_increase_pct", "Total Renewal Increase %", "24.0", "The #1 predictor. Whole percent: 24 = 24%."),
    ("initial_uw_increase_pct", "Initial UW Increase %", "19.0", "Where underwriting opened, pre-negotiation."),
    ("fixed_increase_pct", "Fixed-Cost Increase %", "12.0", "Fixed-cost portion of the increase."),
    ("lasers_current", "Lasers (current year)", "1", "Count of lasers in the expiring year."),
    ("lasers_renewal", "Lasers (at renewal)", "2", "Count of lasers proposed at renewal."),
    ("laser_liability", "Laser Liability $", "45000", "Dollar exposure above the ISL deductible."),
    ("corridor", "Corridor (decimal)", "1.0", "Aggregate corridor. 1.0 = 100%."),
    ("captive_offer", "Captive Offered? (Y/N)", "N", "Was a captive arrangement offered?"),
    ("tenure_years", "Years with Crumdale", "5", "How long the group has been a client."),
    ("bor_change", "BOR Change? (Y/N)", "N", "Did the broker of record change this year?"),
    ("broker", "Broker", "USI - Cincinnati", "Broker of record."),
    ("rsd", "RSD", "Chris", "Regional sales director."),
    ("am", "AM", "Alyssa", "Account manager."),
    ("carrier", "Carrier", "Zurich", "Stop-loss carrier."),
    ("tpa", "TPA", "Trustmark", "Third-party administrator."),
    ("state", "State", "OH", "Group's state, 2-letter."),
    ("network", "Network", "Aetna", "Provider network."),
    ("broker_years_with_cs", "Broker Years w/ CS", "6", "Broker's tenure with Crumdale."),
    ("broker_groups_with_cs", "Broker Groups w/ CS", "18", "Broker's book size with Crumdale."),
    ("broker_products_sold", "Broker Products Sold", "3", "Products the broker sells with CS."),
    ("broker_preferred", "Preferred Broker? (Y/N)", "Y", "Preferred partner."),
]

# Feed 2 only. Deliberately NOT in FIELDS: sending an outcome on an upcoming renewal
# is the "spoiler data" mistake the Model Maintenance page warns about, so the
# upcoming feed rejects this column outright rather than quietly ignoring it.
OUTCOME_FIELD = ("outcome", "Outcome (Renewed / Termed)", "Renewed",
                 "What the group actually did. Feed 2 ONLY - never on an upcoming renewal.")

# Reference-only column shown on the template so a pre-filled sheet (see
# build_template's prefill_outcomes) is easy to eyeball against the app. NOT a
# real model field, NOT in CANONICAL/FIELDS: matching a row to a group is (still,
# deliberately) by name + renewal date, not by typing an id into a spreadsheet -
# a wrong hand-typed id could silently misfile a row onto the wrong company. This
# column is recognized-and-ignored, never parsed into anything.
GROUP_ID_FIELD = ("group_id", "Group ID", "G0042",
                  "Reference only - not required, not used for matching.")

CANONICAL = [c for c, *_ in FIELDS]
REQUIRED = ["group_name", "eff_date"]

# Fractions in the pipeline (0.62 == 62%), not whole percents.
RATIO_FIELDS = {"nlr", "isl_loss_ratio", "agg_loss_ratio", "ratio_to_attachment",
                "mature_to_attachment", "corridor"}
# Whole percents in the pipeline (19.9 == 19.9%).
PERCENT_FIELDS = {"total_increase_pct", "initial_uw_increase_pct", "fixed_increase_pct"}
MONEY_FIELDS = {"annual_premium", "premium_stoploss", "laser_liability"}
COUNT_FIELDS = {"lives", "lasers_current", "lasers_renewal", "tenure_years",
                "broker_years_with_cs", "broker_groups_with_cs", "broker_products_sold"}
BOOL_FIELDS = {"captive_offer", "bor_change", "broker_preferred"}
TEXT_FIELDS = {"group_name", "product", "broker", "rsd", "am", "carrier", "tpa",
               "state", "network"}

# A loss ratio typed as "62" almost certainly means 62%, not 6200%. Convert above
# this threshold and WARN. Chosen at 5.0 (=500%) rather than 1.0 so a genuine
# catastrophic ratio (1.5, 2.4, 3.1 - values that really occur in this book and that
# the model's nlr_severe / nlr_catastrophic_200 features key on) is never mangled.
RATIO_PERCENT_THRESHOLD = 5.0

_TRUE = {"y", "yes", "true", "t", "1", "1.0"}
_FALSE = {"n", "no", "false", "f", "0", "0.0"}
_RENEWED = {"renewed", "renew", "won", "closed won", "yes", "y", "1", "retained"}
_TERMED = {"termed", "term", "terminated", "lost", "closed lost", "no", "n", "0", "churned"}


def _norm_header(h) -> str:
    """Loose header key so 'Net Loss Ratio (decimal)', 'net_loss_ratio' and 'nlr'
    all resolve to the same field."""
    s = str(h).strip().lower()
    s = re.sub(r"\(.*?\)", " ", s)      # drop the parenthetical hint
    s = s.replace("%", " pct ").replace("$", " ")
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


# canonical + friendly + hand-written aliases for headers people actually paste in
_ALIASES: dict[str, str] = {}
for _canon, _friendly, _, _ in FIELDS:
    _ALIASES[_norm_header(_canon)] = _canon
    _ALIASES[_norm_header(_friendly)] = _canon
_ALIASES[_norm_header(OUTCOME_FIELD[0])] = "outcome"
_ALIASES[_norm_header(OUTCOME_FIELD[1])] = "outcome"
_ALIASES[_norm_header(GROUP_ID_FIELD[0])] = "group_id"
_ALIASES[_norm_header(GROUP_ID_FIELD[1])] = "group_id"
_ALIASES.update({
    "id": "group_id",
    "group": "group_name", "account": "account_name_alias", "account_name": "group_name",
    "name": "group_name", "client": "group_name",
    "effective_date": "eff_date", "renewal_date": "eff_date", "eff": "eff_date",
    "renewal_effective_date": "eff_date", "date": "eff_date",
    "employees": "lives", "enrolled": "lives", "members": "lives", "ee": "lives",
    "premium": "annual_premium", "total_premium": "annual_premium",
    "isl_premium": "premium_stoploss", "sl_premium": "premium_stoploss",
    "loss_ratio": "nlr", "net_loss_ratio": "nlr", "nlr_w_rebates": "nlr",
    "agg_lr": "agg_loss_ratio", "aggregate_loss_ratio": "agg_loss_ratio",
    "isl_lr": "isl_loss_ratio",
    "rate_increase": "total_increase_pct", "renewal_increase": "total_increase_pct",
    "total_increase": "total_increase_pct", "increase_w_lasers": "total_increase_pct",
    "firm_increase": "initial_uw_increase_pct",
    "lasers": "lasers_renewal", "laser_count": "lasers_renewal",
    "years_with_cs": "tenure_years", "tenure": "tenure_years", "n_renewals": "tenure_years",
    "bor": "bor_change", "bor_change_y_n": "bor_change",
    "broker_of_record": "broker", "producer": "broker",
    "account_manager": "am", "regional_sales_director": "rsd",
    "result": "outcome", "status": "outcome", "renewed": "outcome",
})
_ALIASES["account_name_alias"] = "group_name"


# --------------------------------------------------------------------------- parse

class ParseError(Exception):
    pass


def _coerce_bool(v):
    s = str(v).strip().lower()
    if s in _TRUE:
        return 1.0
    if s in _FALSE:
        return 0.0
    return None


def _coerce_outcome(v):
    s = str(v).strip().lower()
    if s in _RENEWED:
        return 1.0
    if s in _TERMED:
        return 0.0
    return None


def _coerce_num(v):
    if isinstance(v, str):
        v = v.replace(",", "").replace("$", "").replace("%", "").strip()
        if v in ("", "-", "—", "n/a", "na", "tbd", "unknown"):
            return None
    n = pd.to_numeric(v, errors="coerce")
    return None if pd.isna(n) else float(n)


def coerce_one(canon: str, raw) -> object | None:
    """Interpret ONE hand-typed value for ONE canonical field, with the SAME rules
    the file parser uses (ratio auto-scaling, Y/N, etc.) — so a single-group inline
    edit (Needs Data) and a 500-row upload never disagree about what '62' means for
    the same field. Returns None for blank/unreadable input; caller decides whether
    that's an error or just 'skip this field'."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if canon in BOOL_FIELDS:
        return _coerce_bool(raw)
    if canon in TEXT_FIELDS:
        s = str(raw).strip()
        return s or None
    n = _coerce_num(raw)
    if n is None:
        return None
    if canon in RATIO_FIELDS and abs(n) > RATIO_PERCENT_THRESHOLD:
        n = n / 100.0
    return n


def _read_any(raw: bytes, filename: str) -> dict[str, pd.DataFrame]:
    """{sheet name -> frame}. CSV yields a single pseudo-sheet."""
    name = (filename or "").lower()
    if name.endswith(".csv"):
        return {"csv": pd.read_csv(io.BytesIO(raw), dtype=object)}
    try:
        book = pd.read_excel(io.BytesIO(raw), sheet_name=None, dtype=object)
    except Exception as e:  # noqa: BLE001 - surface the real reason to the user
        raise ParseError(f"Could not read the workbook: {e}") from e
    return book


def _try_pick_sheet(sheets: dict[str, pd.DataFrame], feed: str) -> tuple[str, pd.DataFrame] | None:
    """The sheet whose headers map to the most known fields for this feed, or None
    if nothing in the workbook plausibly matches it. Beats 'first sheet' when the
    template's Instructions sheet (or a stray tab) comes first in the book."""
    want = "outcome" if feed == "outcomes" else "upcoming"
    best, best_score = None, -1
    for sheet, df in sheets.items():
        if df is None or df.empty:
            continue
        known = sum(1 for c in df.columns if _norm_header(c) in _ALIASES)
        if known == 0:
            continue
        score = known + (3 if want in str(sheet).lower() else 0)
        if score > best_score:
            best, best_score = (sheet, df), score
    return best


def _pick_sheet(sheets: dict[str, pd.DataFrame], feed: str) -> tuple[str, pd.DataFrame]:
    picked = _try_pick_sheet(sheets, feed)
    if picked is None:
        raise ParseError("No sheet in this file has recognizable column headers. "
                         "Download the template and use its header row.")
    return picked


def parse(raw: bytes, filename: str, feed: str) -> dict:
    """Parse + validate ONE feed's sheet out of an uploaded file, without touching
    any pipeline file. Prefer parse_workbook() for a real upload — it tries BOTH
    feeds against the same file, since the template ships them together and a user
    who filled in both sheets expects one upload to catch both, not just whichever
    button they happened to click."""
    if feed not in FEEDS:
        raise ParseError(f"Unknown feed '{feed}'.")
    sheets = _read_any(raw, filename)
    sheet, df = _pick_sheet(sheets, feed)
    return _parse_sheet(sheet, df, filename, feed)


def parse_workbook(raw: bytes, filename: str) -> list[dict]:
    """Try BOTH feeds against one uploaded file and return a report for each sheet
    that's actually present — so uploading a fully filled-in template (both
    'Upcoming Renewals' and 'Past Outcomes' sheets) via EITHER upload button picks
    up both, instead of silently importing only the one sheet that button was
    scoped to."""
    sheets = _read_any(raw, filename)
    reports = []
    for feed in FEEDS:
        picked = _try_pick_sheet(sheets, feed)
        if picked is not None:
            reports.append(_parse_sheet(picked[0], picked[1], filename, feed))
    if not reports:
        raise ParseError("No sheet in this file has recognizable column headers for "
                         "either feed. Download the template and use its header row.")
    return reports


def _parse_sheet(sheet: str, df: pd.DataFrame, filename: str, feed: str) -> dict:
    """Parse + validate ONE already-picked (sheet, df) as the given feed. Returns a
    report dict (JSON-safe, minus the "_frame" key) describing exactly what would be
    imported: matched/unrecognized columns, per-row errors, value coercions, and a
    sample. The caller commits only after the user sees this."""
    df = df.dropna(how="all").copy()

    matched: dict[str, str] = {}   # canonical -> original header
    unknown: list[str] = []
    for col in df.columns:
        canon = _ALIASES.get(_norm_header(col))
        if canon and canon not in matched:
            matched[canon] = str(col)
        elif canon is None and not str(col).lower().startswith("unnamed"):
            unknown.append(str(col))

    if "group_name" in matched:
        # Drop the template's own hint row ("e.g. Acme Manufacturing LLC - ...")
        # before it's even counted as a row in the file - it's instructional
        # text, not something the user typed.
        is_hint = df[matched["group_name"]].astype(str).str.strip().str.lower().str.startswith("e.g.")
        df = df[~is_hint.fillna(False)]

    errors: list[str] = []
    warnings: list[str] = []
    for req in REQUIRED:
        if req not in matched:
            label = next(f for c, f, *_ in FIELDS if c == req)
            errors.append(f'Required column "{label}" is missing.')
    if feed == "outcomes" and "outcome" not in matched:
        errors.append(f'Required column "{OUTCOME_FIELD[1]}" is missing for the outcomes feed.')
    if feed == "upcoming" and "outcome" in matched:
        errors.append(
            f'This file has an "{matched["outcome"]}" column. Upcoming renewals must NOT '
            "carry an outcome - that is the value the model predicts. Upload it as the "
            "outcomes feed instead, or remove the column.")
    if errors:
        return {"ok": False, "feed": feed, "filename": filename, "sheet": sheet,
                "rows_in_file": int(len(df)), "rows_ready": 0, "rows_pending": 0,
                "matched_columns": matched, "unrecognized_columns": unknown,
                "errors": errors, "warnings": warnings, "sample": []}

    out_cols = CANONICAL + (["renewed"] if feed == "outcomes" else [])
    rows: list[dict] = []
    skipped = 0
    pending = 0
    coercion_notes: dict[str, int] = {}

    for i, src in df.iterrows():
        line = int(i) + 2  # header is row 1 in the user's spreadsheet
        name = src.get(matched.get("group_name", ""), None)
        name = "" if name is None or pd.isna(name) else str(name).strip()
        # The template's own hint row ("e.g. Acme Manufacturing LLC - ...") reads
        # back as a real, non-blank group_name - a group name is basically never
        # going to start with "e.g." for real, so this is a safe, deterministic
        # way to skip it without assuming it's always exactly row 2 (a reordered
        # or hand-copied sheet could move it).
        if name.lower().startswith("e.g."):
            continue
        eff = _parse_date(src.get(matched.get("eff_date", ""), None))
        if not name and eff is None:
            continue                      # genuinely blank row - not an error
        if not name:
            errors.append(f"Row {line}: no group name.")
            skipped += 1
            continue
        if eff is None:
            errors.append(f'Row {line} ("{name}"): renewal effective date is missing or unreadable.')
            skipped += 1
            continue

        row: dict = {c: np.nan for c in out_cols}
        row["group_name"] = name
        row["eff_date"] = eff.date().isoformat()

        for canon, header in matched.items():
            if canon in ("group_name", "eff_date", "outcome", "group_id"):
                continue  # group_id is reference-only - see GROUP_ID_FIELD
            v = src.get(header)
            if v is None or (not isinstance(v, str) and pd.isna(v)):
                continue
            if canon in TEXT_FIELDS:
                s = str(v).strip()
                if s and s.lower() not in ("nan", "n/a", "-", "—"):
                    row[canon] = s
            elif canon in BOOL_FIELDS:
                b = _coerce_bool(v)
                if b is None:
                    warnings.append(f'Row {line} ("{name}"): could not read "{v}" as Yes/No '
                                    f'for {header} - left blank.')
                else:
                    row[canon] = b
            else:
                n = _coerce_num(v)
                if n is None:
                    if str(v).strip():
                        warnings.append(f'Row {line} ("{name}"): "{v}" is not a number '
                                        f'for {header} - left blank.')
                    continue
                if canon in RATIO_FIELDS and abs(n) > RATIO_PERCENT_THRESHOLD:
                    n = n / 100.0
                    coercion_notes[header] = coercion_notes.get(header, 0) + 1
                if canon in MONEY_FIELDS and n < 0:
                    warnings.append(f'Row {line} ("{name}"): negative {header} - left blank.')
                    continue
                row[canon] = n

        if feed == "outcomes":
            raw_outcome = src.get(matched["outcome"])
            blank_outcome = (raw_outcome is None
                             or (not isinstance(raw_outcome, str) and pd.isna(raw_outcome))
                             or str(raw_outcome).strip() == "")
            o = _coerce_outcome(raw_outcome)
            if o is None:
                if blank_outcome:
                    # Not a mistake -- this is exactly what a pre-filled Past
                    # Outcomes sheet looks like before anyone's typed a
                    # decision in yet (see build_template's prefill_outcomes).
                    # Counted separately from real errors so the UI doesn't
                    # paint 149 unremarkable "still open" rows as red X's.
                    pending += 1
                else:
                    errors.append(f'Row {line} ("{name}"): outcome '
                                  f'"{raw_outcome}" is not Renewed or Termed.')
                    skipped += 1
                continue
            row["renewed"] = o

        rows.append(row)

    for header, n in coercion_notes.items():
        warnings.append(f'"{header}": {n} value(s) looked like whole percents and were '
                        f"read as decimals (e.g. 62 -> 0.62). Check these are right.")

    frame = pd.DataFrame(rows, columns=out_cols) if rows else pd.DataFrame(columns=out_cols)
    dupes = 0
    if len(frame):
        k = frame["group_name"].str.lower().str.strip() + "|" + frame["eff_date"].astype(str)
        dupes = int(k.duplicated().sum())
        if dupes:
            warnings.append(f"{dupes} duplicate row(s) (same group + same renewal date) - "
                            "the last one in the file wins.")
            frame = frame[~k.duplicated(keep="last")].reset_index(drop=True)

    sample = []
    for _, r in frame.head(8).iterrows():
        sample.append({k: (None if (not isinstance(v, str) and pd.isna(v)) else v)
                       for k, v in r.items()})

    return {
        "ok": len(frame) > 0,
        "feed": feed,
        "filename": filename,
        "sheet": sheet,
        "rows_in_file": int(len(df)),
        "rows_ready": int(len(frame)),
        "rows_skipped": skipped,
        "rows_pending": pending,
        "matched_columns": matched,
        "unrecognized_columns": unknown,
        "missing_optional": [f for c, f, *_ in FIELDS if c not in matched],
        "errors": errors,
        "warnings": warnings,
        "sample": sample,
        "_frame": frame,
    }


def _parse_date(v):
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    d = pd.to_datetime(v, errors="coerce")
    if pd.isna(d):
        return None
    ts = pd.Timestamp(d)
    # A bare month like "Oct-2026" parses to the 1st, which is what a renewal
    # effective date always is in this book - fine. Guard only nonsense years.
    return None if ts.year < 2000 or ts.year > 2100 else ts


# -------------------------------------------------------------------------- commit

def _key(frame: pd.DataFrame) -> pd.Series:
    """Group + renewal MONTH. A renewal is annual, so month granularity is the right
    identity for 'the same renewal' - matches _DEAL_CYCLE_MONTHS thinking elsewhere."""
    return (frame["group_name"].astype(str).str.lower().str.strip() + "|"
            + pd.to_datetime(frame["eff_date"]).dt.to_period("M").astype(str))


def _archive(raw: bytes, filename: str, feed: str) -> str:
    """Keep the original file. An import that turns out wrong needs the source to
    diff against - the rest of this pipeline keeps .bak copies for the same reason."""
    UPLOAD_ARCHIVE.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename or "upload")
    dest = UPLOAD_ARCHIVE / f"{stamp}_{feed}_{safe}"
    dest.write_bytes(raw)
    return dest.name


def _restore_from_exclusions(frame: pd.DataFrame, excl_name: str) -> int:
    """Undo a previous manual delete for any row in `frame` that matches one --
    keyed the same way build_book.py's / etl_real.py's exclusion filters match
    (see there): _deal_norm(group_name) + the renewal's cycle month, not the
    exact date.

    A file upload is a deliberate, explicit action -- if someone re-uploads a
    group by name, that's at least as strong a signal of intent as the
    original delete was. Without this, a re-uploaded row gets silently
    filtered back out by build_book.py/etl_real.py on the very next rebuild
    (the delete is permanent by design, to survive an automated re-pull), so
    it looks like the upload "didn't add up" with no indication why -- the fix
    is to treat an explicit re-upload as an implicit undo of that delete,
    rather than requiring someone to know to hand-edit the exclusion file."""
    excl_path = DATA / excl_name
    if not excl_path.exists() or frame.empty:
        return 0
    excl = pd.read_csv(excl_path)
    if excl.empty:
        return 0
    from app.real_mode import _deal_norm
    excl_key = pd.Series(list(zip(
        excl["group_name"].map(_deal_norm),
        pd.to_datetime(excl["eff_date"], errors="coerce").dt.to_period("M"))))
    frame_key = set(zip(
        frame["group_name"].map(_deal_norm),
        pd.to_datetime(frame["eff_date"], errors="coerce").dt.to_period("M")))
    mask = excl_key.isin(frame_key)
    if not mask.any():
        return 0
    excl[~mask.to_numpy()].to_csv(excl_path, index=False)
    return int(mask.sum())


def commit(report: dict, raw: bytes | None = None) -> dict:
    """Write a parsed feed into the pipeline's own files. Does NOT rescore - the
    caller runs build_book/retrain, same as every other ETL path."""
    frame: pd.DataFrame = report.get("_frame")
    if frame is None or frame.empty:
        raise ParseError("Nothing to import.")
    feed = report["feed"]
    archived = _archive(raw, report.get("filename", ""), feed) if raw else None

    if feed == "upcoming":
        restored = _restore_from_exclusions(frame, "excluded_groups.csv")
        # A full file upload always fully replaces a matching row (merge=False) -
        # see _commit_upcoming's docstring. Only the Needs Data single-field edit
        # (app/main.py) uses merge=True, calling _commit_upcoming directly.
        added, updated = _commit_upcoming(frame)
        target = UPLOADED_UPCOMING.name
    else:
        restored = _restore_from_exclusions(frame, "excluded_history.csv")
        added, updated = _commit_outcomes(frame)
        target = HISTORY.name

    result = {"status": "imported", "feed": feed, "target": target,
              "rows_added": added, "rows_updated": updated, "archived_as": archived}
    if restored:
        result["rows_restored"] = restored
    return result


def _commit_upcoming(frame: pd.DataFrame, merge: bool = False) -> tuple[int, int]:
    """Upsert into data/uploaded_upcoming.csv, keyed on group + renewal month.

    merge=False (a file upload): a re-upload of the same group REPLACES its whole
    row rather than duplicating it — the file is a complete statement of that
    group's data as of this upload, so a full replace is the right, idempotent
    behaviour for a corrected re-send.

    merge=True (one field typed inline, e.g. Needs Data): only the columns the
    caller actually SET (non-NaN) overwrite the existing row; every other column
    already on file is left untouched. Without this, saving just "loss ratio" on
    a group that already had "tenure" saved from an earlier edit would blank the
    tenure back out, since a bare replace can't tell "not provided this time"
    apart from "provided as blank"."""
    import build_book
    frame = build_book.enrich_from_active_book(frame)
    frame = frame.copy()
    frame["uploaded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not UPLOADED_UPCOMING.exists():
        frame.to_csv(UPLOADED_UPCOMING, index=False)
        return len(frame), 0

    prev = pd.read_csv(UPLOADED_UPCOMING)
    for c in frame.columns:
        if c not in prev.columns:
            prev[c] = np.nan
    for c in prev.columns:
        if c not in frame.columns:
            frame[c] = np.nan

    if not merge:
        prev_keys = set(_key(prev))
        updated = int(_key(frame).isin(prev_keys).sum())
        merged = pd.concat([prev, frame[prev.columns]], ignore_index=True)
        merged = merged[~_key(merged).duplicated(keep="last")].reset_index(drop=True)
        merged.to_csv(UPLOADED_UPCOMING, index=False)
        return len(frame) - updated, updated

    prev_key = _key(prev)
    added = updated = 0
    for i in range(len(frame)):
        row = frame.iloc[i]
        match = prev.index[prev_key == _key(frame.iloc[[i]]).iloc[0]]
        if len(match):
            idx = match[0]
            for c in frame.columns:
                if pd.notna(row[c]):
                    prev.at[idx, c] = row[c]
            updated += 1
        else:
            prev = pd.concat([prev, frame.iloc[[i]]], ignore_index=True)
            prev_key = _key(prev)
            added += 1
    prev.to_csv(UPLOADED_UPCOMING, index=False)
    return added, updated


def _commit_outcomes(frame: pd.DataFrame) -> tuple[int, int]:
    """Append decided renewals to real_history.csv (the training set), skipping any
    group+month already recorded there. Skipping rather than overwriting is
    deliberate: history is what the model has already learned from, and a silent
    relabel of an existing training row is not something an upload should be able
    to do by accident."""
    hist = pd.read_csv(HISTORY)
    existing = set(_key(hist))
    incoming = frame[~_key(frame).isin(existing)].copy()
    skipped = len(frame) - len(incoming)
    if incoming.empty:
        return 0, skipped
    import build_book
    incoming = build_book.enrich_from_active_book(incoming)
    new = pd.DataFrame({c: np.nan for c in hist.columns}, index=range(len(incoming)))
    for c in hist.columns:
        if c in incoming.columns:
            new[c] = incoming[c].to_numpy()
    new["source"] = "uploaded_feed"
    pd.concat([hist, new], ignore_index=True).to_csv(HISTORY, index=False)
    return len(incoming), skipped


# ------------------------------------------------------------------------ template

def build_template(prefill_outcomes: list[dict] | None = None) -> bytes:
    """The .xlsx users fill in: an Instructions sheet plus one sheet per feed.

    prefill_outcomes: current Upcoming Renewals groups, written as starter ROWS
    on the Past Outcomes sheet only. Each dict may carry ANY subset of the
    canonical fields (plus group_id) - whatever the caller already knows and
    trusts; a key that's genuinely unknown should simply be absent, not None,
    since a value passed here is written into the cell as-is, no further checks.
    The Outcome column itself is always left blank for the user to add - that's
    the one thing this can never pre-fill. A still-open group's row can just be
    deleted. No prefill on Upcoming Renewals - a pre-filled row THERE is a row
    somebody could re-import unchanged by accident.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head_fill = PatternFill("solid", fgColor="1F2937")
    head_font = Font(color="FFFFFF", bold=True, size=10)
    req_fill = PatternFill("solid", fgColor="7F1D1D")
    ref_fill = PatternFill("solid", fgColor="374151")
    prefill_fill = PatternFill("solid", fgColor="EFF6FF")

    info = wb.active
    info.title = "Instructions"
    info["A1"] = "Horizon - renewal upload template"
    info["A1"].font = Font(bold=True, size=14)
    notes = [
        "",
        "Fill in ONE row per renewal (per group, per year) on the sheet that matches your feed:",
        "  • 'Upcoming Renewals'  - renewals still to come. These get SCORED. No outcome column.",
        "  • 'Past Outcomes'      - renewals that have decided. Same fields PLUS the outcome.",
        "",
        "CRITICAL - send only what was known BEFORE the decision.",
        "Every value must reflect what was known at the time of the renewal quote (e.g. the loss",
        "ratio as of ~90 days out, the quoted increase). Do not include final, settled or",
        "after-the-fact figures. Test: 'Would we have known this the day we quoted it?'",
        "If not, leave it blank.",
        "",
        "Leave a cell BLANK when you don't have the value. Blank is always better than a guess -",
        "the model handles missing data, but it cannot detect a made-up number.",
        "",
        "Group Name and Renewal Effective Date are required on every row. Everything else is",
        "optional and sharpens the score.",
        "",
        "Loss ratios and corridor are DECIMALS (0.62 = 62%). Increases are WHOLE PERCENTS (24 = 24%).",
        "",
        "Column order doesn't matter and headers don't have to match exactly - common variants are",
        "recognised. Anything unrecognised is listed for you in the upload preview before import.",
        "",
        "'Group ID' is shown for reference only (so you can cross-check against the app) - it is",
        "NOT required and NOT used for matching. A row is matched to a group by name + renewal",
        "date, same as everywhere else in Horizon.",
    ]
    if prefill_outcomes:
        notes += [
            "",
            f"This copy is pre-filled: the Past Outcomes sheet already has all {len(prefill_outcomes)} "
            "groups currently in Upcoming Renewals (highlighted rows) - group, ID and renewal date "
            "filled in. Add the Outcome for whichever have decided, and delete the rows for the ones "
            "still open.",
        ]
    for i, line in enumerate(notes, start=2):
        info[f"A{i}"] = line
    info.column_dimensions["A"].width = 100

    for sheet_name, feed in (("Upcoming Renewals", "upcoming"), ("Past Outcomes", "outcomes")):
        ws = wb.create_sheet(sheet_name)
        cols = [GROUP_ID_FIELD] + list(FIELDS) + \
            ([("outcome", *OUTCOME_FIELD[1:])] if feed == "outcomes" else [])
        col_index = {c[0]: j for j, c in enumerate(cols, start=1)}
        for j, (canon, friendly, example, note) in enumerate(cols, start=1):
            cell = ws.cell(row=1, column=j, value=friendly)
            cell.fill = req_fill if canon in REQUIRED or canon == "outcome" \
                else ref_fill if canon == "group_id" else head_fill
            cell.font = head_font
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            hint = ws.cell(row=2, column=j, value=f"e.g. {example} - {note}")
            hint.font = Font(italic=True, size=8, color="6B7280")
            hint.alignment = Alignment(vertical="top", wrap_text=True)
            ws.column_dimensions[get_column_letter(j)].width = max(14, min(30, len(friendly) + 4))
        ws.row_dimensions[1].height = 30
        ws.row_dimensions[2].height = 42
        ws.freeze_panes = "A3"
        # Row 2 is the hint row; the parser ignores it because neither a group name
        # nor a readable date parses out of "e.g. ..." text.

        if feed == "outcomes" and prefill_outcomes:
            # Write EVERY field the caller actually has for this group (not just
            # id/name/date) - a dict only carries keys it's confident about (see
            # app/main.py's _group_to_prefill_row), so "key present" already means
            # "known and safe to show", nothing extra to check here.
            for r, item in enumerate(prefill_outcomes, start=3):
                for key, value in item.items():
                    if value is None or key not in col_index:
                        continue
                    cell = ws.cell(row=r, column=col_index[key], value=value)
                    cell.fill = prefill_fill

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def field_guide() -> list[dict]:
    """The template's field list, for the UI to render without duplicating it."""
    return [{"key": c, "label": f, "example": e, "note": n, "required": c in REQUIRED}
            for c, f, e, n in FIELDS] + [
        {"key": OUTCOME_FIELD[0], "label": OUTCOME_FIELD[1], "example": OUTCOME_FIELD[2],
         "note": OUTCOME_FIELD[3], "required": False, "outcomes_only": True}]
