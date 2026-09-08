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

import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException

from fastapi.staticfiles import StaticFiles

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app import data_quality_nb, nb_mode  # noqa: E402

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


@app.post("/api/retrain")
def retrain():
    """Re-run the ETL + train + build pipeline and hot-swap the served state."""
    import build_book
    import etl_nb
    import train_nb

    etl_nb.build()
    payload = train_nb.train_and_save(verbose=False)
    build_book.build()
    STATE["nb"] = nb_mode.build_state()
    return {"status": "retrained", "version": payload["version"], "metrics": payload["metrics"]}


# Serve the built frontend when present (standalone dev only — the combined
# product's gateway serves the merged frontend instead and does not use this).
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
