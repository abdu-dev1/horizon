"""
Horizon — Renewal Forecasting API.

Run:  uvicorn app.main:app --reload --port 8000   (from backend/)

Runs on the real renewal book only (etl_real.py + train_real.py + build_book.py).
Startup fails loudly if that data/model isn't available — no synthetic/demo
fallback, so a broken pipeline is never silently masked by fake data.
"""

from __future__ import annotations

import csv
import shutil
import sys
import tempfile
import uuid
import zipfile

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile

from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import bundle, data_quality, export, insights, real_mode, recommend
from .paths import backend_dir, frontend_dist_dir

BACKEND_DIR = backend_dir()
FRONTEND_DIST = frontend_dist_dir()

sys.path.insert(0, str(BACKEND_DIR))
import upload_feed  # noqa: E402  (backend/upload_feed.py — the one-file upload feed)

# No CORS middleware, deliberately. This backend is never called cross-origin:
# in production the gateway serves the SPA and proxies the API from the same
# origin, and in dev Vite proxies both prefixes server-side (see
# frontend/vite.config.js). The previous `allow_origins=["*"]` allowed any
# website to read this book -- client names, premiums, loss ratios -- from a
# signed-in user's browser.
app = FastAPI(title="Horizon — Crumdale Renewal Forecasting", version="1.1")

STATE: dict = {"real": None}


def _cur() -> dict:
    return STATE["real"]


@app.on_event("startup")
def startup():
    if not real_mode.available():
        raise RuntimeError(
            "real_mode data/model artifacts are missing — cannot start "
            "(run etl_real.py + train_real.py + build_book.py first)"
        )
    STATE["real"] = real_mode.build_state()
    _warm_recs()


@app.get("/api/health")
def health():
    cur = _cur()
    return {
        "status": "ok",
        "model_version": cur["model_version"],
        "as_of": insights.AS_OF.date().isoformat(),
    }


def _clear_rec_cache():
    STATE.pop("_rec_cache", None)


def _warm_recs():
    """Precompute the at-risk recommendations so the dashboard's 'Recommended
    Actions' section renders instantly (no cold-start compute when the user
    opens it)."""
    if "_rec_cache" not in STATE:
        try:
            STATE["_rec_cache"] = recommend.recommend_book(_cur()["groups"], _rescore, _rescore_batch)
        except Exception:
            pass


@app.get("/api/summary")
def summary():
    return _cur()["summary"]


@app.get("/api/forecast")
def forecast():
    return _cur()["forecast"]


@app.get("/api/segments")
def segments():
    return _cur()["segments"]


@app.get("/api/groups")
def groups():
    return {"groups": _cur()["groups"]}


@app.get("/api/export/book")
def export_book():
    """Download the current Book of Business (upcoming renewals in the window) as an
    .xlsx with the same columns/tier colours as the dashboard table."""
    cur = _cur()
    buf = export.build_book_xlsx(cur["groups"], cur["summary"].get("as_of"))
    fname = f"Horizon_Upcoming_Renewals_{datetime.now():%Y-%m-%d}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/groups/{group_id}")
def group_detail(group_id: str):
    for g in _cur()["groups"]:
        if g["group_id"] == group_id:
            return g
    raise HTTPException(404, f"group {group_id} not found in the forecast window")


@app.get("/api/data-quality")
def data_quality_report():
    """Training-data health report, measured live from the real renewal
    history (data/real_history.csv). Powers the Data Quality page."""
    return data_quality.build_report()


@app.get("/api/renewal-database")
def renewal_database():
    """Every historical renewal decision, row-level, measured live from the
    real renewal history (data/real_history.csv). Powers the Renewal
    Database page."""
    return data_quality.build_records()


@app.get("/api/model/metrics")
def model_metrics():
    cur = _cur()
    return {"version": cur["model_version"], "metrics": cur["metrics"],
            "wf_metrics": cur.get("wf_metrics"), "history": cur["registry"]}


@app.get("/api/model/importance")
def model_importance():
    return {"importances": _cur()["importances"]}


@app.get("/api/model/diagnostics")
def model_diagnostics():
    cur = _cur()
    return {"calibration": cur["calibration"], "roc": cur["roc"],
            "wf_calibration": cur.get("wf_calibration") or [],
            "wf_roc": cur.get("wf_roc") or {"fpr": [], "tpr": []}}


def _rescore(group_id: str, overrides: dict) -> float:
    return real_mode.rescore(STATE["real"], group_id, overrides)


def _rescore_batch(jobs: list) -> list:
    """Score many (group_id, overrides) in one shot — powers the fast recommender."""
    return real_mode.rescore_batch(STATE["real"], jobs)


@app.post("/api/groups/move-decided-to-database")
def move_decided_to_database():
    """Manually transfer EVERY currently-decided group (Renewed/Termed) from Upcoming
    Renewals into the permanent Renewal Database, in one batch. Powers the
    page-level 'Move Decided to Renewal Database' button."""
    moved = real_mode.move_all_decided_to_history(STATE["real"])
    STATE["real"] = real_mode.build_state()
    _clear_rec_cache()
    return {"status": "moved", "count": moved}


@app.get("/api/recommendations")
def recommendations():
    """Model-driven interventions for the most material at-risk groups,
    ranked by expected premium saved."""
    if "_rec_cache" not in STATE:
        STATE["_rec_cache"] = recommend.recommend_book(_cur()["groups"], _rescore, _rescore_batch)
    return {"recommendations": STATE["_rec_cache"]}


@app.get("/api/recommendations/{group_id}")
def recommendation_one(group_id: str):
    cur = _cur()
    g = next((x for x in cur["groups"] if x["group_id"] == group_id), None)
    if g is None:
        raise HTTPException(404, f"group {group_id} not found in the forecast window")
    return recommend.recommend_for(g, _rescore)


@app.post("/api/groups/{group_id}/underwriting")
def add_underwriting(group_id: str, body: dict):
    """Fill in underwriting for ONE Needs Data group in place — every field the
    model uses (see upload_feed.CANONICAL / NeedsData.jsx's editable grid), not
    just loss ratio. This is the one spot in the app where hand-typed data is
    allowed at all; Upcoming Renewals stays upload-only (see real_mode.py notes).

    body = {"fields": {canonical_name: raw_value, ...}} — same field names and
    same "62 means 0.62" coercion as a file upload (upload_feed.coerce_one), so a
    value typed here and the same value typed into the upload template are
    interpreted identically. Writes through the SAME upsert path as a Feed 1 file
    upload — a one-row edit is just a one-row upload — so there is exactly one
    place that knows how to land human-supplied open-renewal data, not two.
    """
    cur = _cur()
    g = next((x for x in cur["groups"] if x["group_id"] == group_id), None)
    if g is None:
        raise HTTPException(404, f"group {group_id} not found in the forecast window")

    fields: dict = {}
    for canon, raw in (body.get("fields") or {}).items():
        if canon not in upload_feed.CANONICAL or canon in ("group_name", "eff_date"):
            continue
        val = upload_feed.coerce_one(canon, raw)
        if val is not None:
            fields[canon] = val
    if not fields:
        raise HTTPException(400, "Nothing to save — enter at least one field.")

    row = {c: np.nan for c in upload_feed.CANONICAL}
    row["group_name"] = g["group_name"]
    row["eff_date"] = g["renewal_date"]
    row.update(fields)
    upload_feed._commit_upcoming(pd.DataFrame([row]), merge=True)

    import build_book
    build_book.build()
    STATE["real"] = real_mode.build_state()
    _clear_rec_cache()
    new_g = next((x for x in STATE["real"]["groups"]
                  if x["group_name"] == g["group_name"] and x["renewal_date"] == g["renewal_date"]),
                 None)
    return {"status": "updated", "group": new_g}


@app.delete("/api/groups/{group_id}")
def delete_group(group_id: str):
    """Remove ONE renewal row from Upcoming Renewals / Needs Data - entered in
    error, a duplicate, a group that shouldn't be tracked. Persisted in
    data/excluded_groups.csv (see build_book.py), keyed on name + renewal cycle,
    so it stays gone across every future rebuild/retrain - unlike editing
    real_scored_book.csv directly, which build_book.build() would just regenerate
    over. Scoped to this specific renewal, not the whole company: real_history.csv
    is never touched, so a bad current-cycle row can be removed without erasing
    that company's actual past decisions. Reversible by hand (delete its row from
    excluded_groups.csv and rebuild) - same as every other override file here."""
    cur = _cur()
    g = next((x for x in cur["groups"] if x["group_id"] == group_id), None)
    if g is None:
        raise HTTPException(404, f"group {group_id} not found in the forecast window")

    path = BACKEND_DIR / "data" / "excluded_groups.csv"
    new_file = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["group_name", "eff_date", "note", "excluded_at"])
        w.writerow([g["group_name"], g["renewal_date"], "removed via Upcoming Renewals",
                    datetime.now(timezone.utc).isoformat(timespec="seconds")])

    import build_book
    build_book.build()
    STATE["real"] = real_mode.build_state()
    _clear_rec_cache()
    return {"status": "deleted", "group_name": g["group_name"], "renewal_date": g["renewal_date"]}


@app.post("/api/renewal-database/delete")
def delete_history_record(body: dict):
    """Remove ONE historical decision from the Renewal Database — entered in
    error, a stale duplicate, a screenshot mis-transcribed. Distinct from
    delete_group above: that one only ever hides a row from the FORWARD book
    and never touches real_history.csv; this is the one place a past decision
    itself can be removed, so it's a heavier action (it's real training data).

    Two things happen: real_history.csv is edited directly, right now (this
    process cannot rerun the raw-workbook ETL — see the note above where
    /api/retrain used to be, same reason), AND the deletion is persisted to
    data/excluded_history.csv so the next full etl_real.py rebuild — which
    reconstructs real_history.csv from the raw exports — doesn't silently
    bring it back. Does NOT retrain: the live model already learned from this
    row; removing it only takes effect on the next explicit retrain, same as
    every other data-import path in this app.
    """
    group_name = (body.get("group_name") or "").strip()
    eff_date = body.get("eff_date")
    if not group_name or not eff_date:
        raise HTTPException(400, "body must include 'group_name' and 'eff_date'")

    path = BACKEND_DIR / "data" / "real_history.csv"
    df = pd.read_csv(path, parse_dates=["eff_date"])
    target_date = pd.to_datetime(eff_date, errors="coerce")
    mask = ((df["group_name"].astype(str).str.strip().str.lower() == group_name.lower())
            & (df["eff_date"] == target_date))
    if not mask.any():
        raise HTTPException(404, f"No matching Renewal Database record for "
                                  f"{group_name!r} @ {eff_date}")
    removed_count = int(mask.sum())
    df = df[~mask]
    df.to_csv(path, index=False)

    excl_path = BACKEND_DIR / "data" / "excluded_history.csv"
    new_file = not excl_path.exists()
    with open(excl_path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["group_name", "eff_date", "note", "excluded_at"])
        w.writerow([group_name, target_date.date().isoformat(), "removed via Renewal Database",
                    datetime.now(timezone.utc).isoformat(timespec="seconds")])

    import build_book
    build_book.build()
    STATE["real"] = real_mode.build_state()
    _clear_rec_cache()
    return {"status": "deleted", "group_name": group_name, "eff_date": target_date.date().isoformat(),
            "removed_count": removed_count}


_BLANK_TEXT = {None, "", "—", "Unknown"}


def _blank(v):
    return None if v in _BLANK_TEXT else v


def _yn(v):
    """Tri-state (True/False/None) -> 'Y'/'N'/None. None stays None - an unknown
    BOR-change/captive-offer/preferred-broker flag must never round-trip as a
    confident 'N' just because it's being copied into a spreadsheet."""
    return None if v is None else ("Y" if v else "N")


def _group_to_prefill_row(g: dict) -> dict:
    """Every field we already have for this group, in upload_feed's canonical
    units, for the Past Outcomes template prefill — so filling in an outcome
    doubles as a chance to fill in whatever gap the user notices along the way.
    Anything we don't actually know (including values that are secretly
    fallbacks for display, like group_size defaulting to 50) is left out
    entirely rather than written as if it were real - see the *_known flags."""
    row = {
        "group_id": g["group_id"], "group_name": g["group_name"], "eff_date": g["renewal_date"],
        "product": _blank(g.get("product")),
        "lives": g["group_size"] if g.get("lives_known") else None,
        "annual_premium": g["annual_premium"] if not g.get("premium_estimated") else None,
        "premium_stoploss": g.get("premium_stoploss"),
        "nlr": g["loss_ratio"] if g.get("loss_ratio_known") else None,
        "isl_loss_ratio": g.get("isl_loss_ratio"),
        "agg_loss_ratio": g.get("agg_loss_ratio"),
        "ratio_to_attachment": g.get("ratio_to_attachment"),
        "mature_to_attachment": g.get("mature_to_attachment"),
        "total_increase_pct": g.get("rate_increase_pct"),
        "initial_uw_increase_pct": g.get("initial_uw_increase_pct"),
        "fixed_increase_pct": g.get("fixed_increase_pct"),
        "lasers_current": g.get("lasers_current"),
        "lasers_renewal": g.get("lasers_renewal_count"),
        "laser_liability": g.get("laser_liability"),
        "corridor": g.get("corridor"),
        "captive_offer": _yn(g.get("captive_offer")),
        "tenure_years": g["tenure_years"] if g.get("tenure_known") else None,
        "bor_change": _yn(g.get("recent_bor")),
        "broker": _blank(g.get("broker_name")),
        "rsd": _blank(g.get("rsd")),
        "am": _blank(g.get("am")),
        "carrier": _blank(g.get("carrier")),
        "tpa": _blank(g.get("tpa")),
        "state": _blank(g.get("state")),
        "network": _blank(g.get("network")),
        "broker_years_with_cs": g.get("broker_years_with_cs"),
        "broker_groups_with_cs": g.get("broker_groups_with_cs"),
        "broker_products_sold": g.get("broker_products_sold"),
        "broker_preferred": _yn(g.get("broker_preferred")),
    }
    return {k: v for k, v in row.items() if v is not None}


@app.get("/api/upload/template")
def upload_template(include_upcoming: bool = False):
    """The .xlsx a user fills in and re-uploads — one sheet per feed (Upcoming
    Renewals, Past Outcomes), header rows matching upload_feed.FIELDS exactly so a
    re-upload always round-trips cleanly. Fill in either or both sheets; one
    upload picks up whichever are actually present (see /api/upload/preview).

    include_upcoming=true: pre-fill the Past Outcomes sheet with every group
    currently in Upcoming Renewals / Needs Data — EVERY field we already have for
    it, not just id/name/date, so reporting the outcome is also a chance to catch
    and fill in whatever's still missing. Only real, known values are written;
    see _group_to_prefill_row.
    """
    prefill = None
    if include_upcoming:
        prefill = [_group_to_prefill_row(g) for g in _cur()["groups"]]
    data = upload_feed.build_template(prefill_outcomes=prefill)
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="Horizon_Upload_Template.xlsx"'},
    )


@app.post("/api/upload/preview")
async def upload_preview(file: UploadFile = File(...)):
    """Parse + validate an uploaded file WITHOUT touching any pipeline file. Tries
    BOTH feeds against it (upload_feed.parse_workbook) — a filled-in template has
    an 'Upcoming Renewals' sheet AND a 'Past Outcomes' sheet, and one upload should
    catch whichever are actually present, not just one. Returns one report per
    feed found, each with its own token; the caller reviews all of them and calls
    /api/upload/commit per token to actually import — an upload is never applied
    silently."""
    raw = await file.read()
    try:
        reports = upload_feed.parse_workbook(raw, file.filename)
    except upload_feed.ParseError as e:
        raise HTTPException(400, str(e)) from e
    out = []
    for report in reports:
        token = uuid.uuid4().hex
        STATE.setdefault("_pending_uploads", {})[token] = {
            "raw": raw, "filename": file.filename, "report": report,
        }
        out.append({"token": token, **{k: v for k, v in report.items() if k != "_frame"}})
    return {"reports": out}


@app.post("/api/upload/commit")
def upload_commit(body: dict):
    """Commit a previously-previewed upload (see /api/upload/preview) into the
    pipeline's own files, then rebuild the served book. Does NOT retrain — new
    outcomes become training data on the next explicit Monthly Retrain, same as
    every other import path in this app."""
    token = body.get("token")
    pending = STATE.get("_pending_uploads", {}).pop(token, None)
    if pending is None:
        raise HTTPException(404, "No matching preview — upload again (previews expire "
                                  "once committed or after a server restart).")
    report = pending["report"]
    if not report.get("ok"):
        raise HTTPException(400, "That preview had errors — fix the file and re-upload.")
    result = upload_feed.commit(report, pending["raw"])

    import build_book
    build_book.build()
    STATE["real"] = real_mode.build_state()
    _clear_rec_cache()
    _warm_recs()
    return result


# No /api/retrain any more -- see the matching note in
# NewBusiness/backend/app/main.py. It trained inline in the request, which a
# deployed environment cannot support (minutes of work against a 230-second
# request timeout, training memory inside the web process, and it needs raw
# client workbooks the server does not have). Retraining runs on an admin's
# laptop; these endpoints install the result.


@contextmanager
def _spooled(file: UploadFile):
    """Land an upload on disk so zipfile can seek it, and always clean up.
    Streamed rather than read into memory -- a bundle carries the model."""
    tmp = Path(tempfile.mkdtemp()) / "bundle.zip"
    try:
        with tmp.open("wb") as fh:
            shutil.copyfileobj(file.file, fh)
        yield tmp
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)


@app.get("/api/admin/status")
def admin_status():
    """What is currently live, for the Admin page."""
    cur = _cur()
    latest = cur["registry"][-1] if cur["registry"] else {}
    hist = BACKEND_DIR / "data" / "real_history.csv"
    return {
        "product": "renewal",
        "model_version": cur["model_version"],
        "trained_at": latest.get("trained_at"),
        "metrics": cur["metrics"],
        "wf_metrics": cur.get("wf_metrics"),
        "groups_in_window": len(cur.get("groups") or []),
        "data_updated": (
            datetime.fromtimestamp(hist.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
            if hist.exists() else None),
        # Named so the Admin page can show that hand-made corrections survive a
        # publish -- see app/bundle.py DELTA_FILES.
        "local_files": [f for f in bundle.DELTA_FILES
                        if (BACKEND_DIR / "data" / f).exists()],
    }


@app.post("/api/admin/bundle/inspect")
async def admin_bundle_inspect(file: UploadFile = File(...)):
    """Validate an uploaded bundle and report what it WOULD install, so
    promoting a model is a decision rather than a side effect of picking a
    file."""
    with _spooled(file) as tmp:
        try:
            return {"ok": True, "manifest": bundle.inspect(tmp)}
        except (ValueError, zipfile.BadZipFile) as e:
            raise HTTPException(400, str(e))


@app.post("/api/admin/bundle/apply")
async def admin_bundle_apply(file: UploadFile = File(...)):
    """Install a bundle and reload the served state. The locally-owned
    override/registry CSVs are untouched by construction (app/bundle.py)."""
    with _spooled(file) as tmp:
        try:
            result = bundle.apply(tmp)
        except (ValueError, zipfile.BadZipFile) as e:
            raise HTTPException(400, str(e))
    _clear_rec_cache()
    STATE["real"] = real_mode.build_state()
    _warm_recs()
    return {"status": "applied", **result,
            "model_version": _cur()["model_version"]}


@app.post("/api/admin/reload")
def admin_reload():
    """Rebuild served state from what is on disk, installing nothing -- for
    when the data changed underneath the process."""
    _clear_rec_cache()
    STATE["real"] = real_mode.build_state()
    _warm_recs()
    return {"status": "reloaded", "model_version": _cur()["model_version"]}


# Serve the built frontend when present (single-process deployment)
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
