"""
NewBusiness — Win-Likelihood API.

Run standalone (dev):  uvicorn app.main:app --port 8001   (from NewBusiness/backend/)

Self-contained: runs on NewBusiness/backend/data + models only (etl_nb.py +
train_nb.py + build_book.py). Startup fails loudly if that data/model isn't
available — no synthetic/demo fallback, matching the renewal project's rule.

When served as part of the combined Horizon product, this app is mounted by
the top-level gateway at a sub-path (see ForecastEngine/gateway/main.py) —
this file itself never changes for that; the gateway does the mounting.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile

from fastapi.staticfiles import StaticFiles

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app import bundle, data_quality_nb, nb_mode  # noqa: E402


@contextmanager
def _spooled(file: UploadFile):
    """Land an upload on disk so zipfile can seek it, and always clean up.

    Bundles carry a ~148 MB model, so this is streamed to a temp file rather
    than read into memory.
    """
    tmp = Path(tempfile.mkdtemp()) / "bundle.zip"
    try:
        with tmp.open("wb") as fh:
            shutil.copyfileobj(file.file, fh)
        yield tmp
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)

FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"  # standalone-dev only

# No CORS middleware -- see the matching note in backend/app/main.py. This
# engine is loopback-only and is only ever reached through the gateway.
app = FastAPI(title="Horizon — New Business Win Likelihood", version="0.1")

STATE: dict = {"nb": None}


def _cur() -> dict:
    return STATE["nb"]


@app.on_event("startup")
def startup():
    if not nb_mode.available():
        raise RuntimeError(
            "NewBusiness data/model artifacts are missing — cannot start "
            "(run etl_nb.py + train_nb.py + build_book.py first)"
        )
    STATE["nb"] = nb_mode.build_state()


@app.get("/api/health")
def health():
    cur = _cur()
    return {"status": "ok", "model_version": cur["model_version"]}


@app.get("/api/summary")
def summary():
    return _cur()["summary"]


@app.get("/api/segments")
def segments():
    return _cur()["segments"]


@app.get("/api/groups")
def groups():
    return {"groups": _cur()["groups"]}


@app.get("/api/groups/{quote_id}")
def group_detail(quote_id: str):
    for g in _cur()["groups"]:
        if g["quote_id"] == quote_id:
            return g
    raise HTTPException(404, f"quote {quote_id} not found in the open pipeline")


@app.get("/api/stage-options")
def stage_options():
    """Every stage a quote can be set to, including the two decisions --
    powers the Open Pipeline page's stage-editor dropdown."""
    return {"options": nb_mode.STAGE_OPTIONS}


@app.post("/api/groups/{quote_id}/stage")
def update_stage(quote_id: str, body: dict):
    """Manually correct one quote's stage. Setting it to Closed Won/Lost
    moves the quote into the Win/Loss Database in the same action (see
    nb_mode.update_stage)."""
    stage = body.get("stage")
    if not stage:
        raise HTTPException(400, "body must include 'stage'")
    try:
        result = nb_mode.update_stage(quote_id, stage)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    STATE["nb"] = nb_mode.build_state()
    return {"status": "updated", **result}


@app.get("/api/history")
def history():
    """Every decided quote (Closed Won/Lost), row-level — the New Business
    equivalent of the renewal project's Renewal Database page."""
    return {"records": nb_mode.history_records()}


@app.get("/api/performance")
def performance():
    """Executive scorecard: the whole decided book by year, RSD, broker,
    product, industry, state, deal size and effective month. Pure measured
    history -- no model output anywhere in it (see insights_nb.performance)."""
    return _cur()["performance"]


@app.get("/api/data-quality")
def data_quality():
    """Training-data health report, measured live from nb_history.csv (decided)
    and nb_pipeline.csv (open) — powers the Data Quality page."""
    return data_quality_nb.build_report()


@app.get("/api/model/metrics")
def model_metrics():
    cur = _cur()
    latest = cur["registry"][-1] if cur["registry"] else {}
    return {
        "version": cur["model_version"], "metrics": cur["metrics"],
        "band_reliability": latest.get("band_reliability"),
        "history": cur["registry"],
    }


# There is deliberately no /api/retrain here any more. It used to run
# etl_nb.build() + train_nb.train_and_save() + build_book.build() inline in the
# request, which cannot work in a deployed environment: the pipeline takes
# minutes (measured ~4) against a hard 230-second platform request timeout, it
# would hold the web process's memory while training, and it needs the raw
# client workbooks the server does not and should not have. More importantly
# the ETL needs human judgment -- see DEPLOYMENT_PLAN.md. Retraining happens on
# an admin's laptop; the endpoints below install the result.


@app.get("/api/admin/status")
def admin_status():
    """What is currently live, for the Admin page."""
    cur = _cur()
    latest = cur["registry"][-1] if cur["registry"] else {}
    hist = nb_mode.DATA / "nb_history.csv"
    return {
        "product": "new_business",
        "model_version": cur["model_version"],
        "trained_at": latest.get("trained_at"),
        "metrics": cur["metrics"],
        "band_reliability": latest.get("band_reliability"),
        "history_rows": latest.get("n_history"),
        "open_quotes": len(cur.get("groups") or []),
        "data_updated": (
            datetime.fromtimestamp(hist.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
            if hist.exists() else None),
    }


@app.post("/api/admin/bundle/inspect")
async def admin_bundle_inspect(file: UploadFile = File(...)):
    """Validate an uploaded bundle and report what it WOULD install.

    Separate from apply so promoting a model is an explicit decision rather
    than a side effect of choosing a file -- the admin sees the version, row
    count and metrics first.
    """
    with _spooled(file) as tmp:
        try:
            return {"ok": True, "manifest": bundle.inspect(tmp)}
        except (ValueError, zipfile.BadZipFile) as e:
            raise HTTPException(400, str(e))


@app.post("/api/admin/bundle/apply")
async def admin_bundle_apply(file: UploadFile = File(...)):
    """Install a bundle and reload the served state.

    In-app decisions (manual stage edits, auto-expire) are preserved -- see
    app/bundle.py for why that reconciliation has to happen here, at apply
    time, and not in a plain reload-from-disk endpoint.
    """
    with _spooled(file) as tmp:
        try:
            result = bundle.apply(tmp)
        except (ValueError, zipfile.BadZipFile) as e:
            raise HTTPException(400, str(e))
    STATE["nb"] = nb_mode.build_state()
    return {"status": "applied", **result,
            "model_version": _cur()["model_version"]}


@app.post("/api/admin/reload")
def admin_reload():
    """Rebuild served state from what is on disk, without installing anything.

    For the case where the data changed underneath the process (a volume
    restored, a file replaced out of band) and the app should pick it up
    without a restart.
    """
    STATE["nb"] = nb_mode.build_state()
    return {"status": "reloaded", "model_version": _cur()["model_version"]}


# Serve the built frontend when present (standalone dev only — the combined
# product's gateway serves the merged frontend instead and does not use this).
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
