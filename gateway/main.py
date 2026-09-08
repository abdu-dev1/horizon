"""
Horizon Gateway — the single public-facing process for the combined product.

Why a reverse proxy instead of ASGI sub-mounting: both backends' packages are
literally named `app` (backend/app/ and NewBusiness/backend/app/) — importing
both into one Python process by path would collide in sys.modules, and their
internal code uses relative imports (`from . import ...`) that assume they own
that package name. Rather than fight that, each backend keeps running as its
own ordinary process (exactly like today's dev setup, just on internal-only
ports), completely unmodified, and this gateway is a thin HTTP reverse proxy
in front of both — which also means a crash in one backend's model code can
never take down the other.

Routing:
  /nb-app/*  -> New Business backend (internal port 8011), "/nb-app" stripped
  everything else (incl. /api/*, /, static assets) -> Renewal backend
                (internal port 8010), unprefixed

The renewal backend's own frontend static mount serves the MERGED frontend
build (frontend/dist now contains both dashboards + the product switcher —
see frontend/src/App.jsx), so no separate static serving is needed here.
"""
from __future__ import annotations

import os

from starlette.background import BackgroundTask

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

RENEWAL_BASE = os.environ.get("HORIZON_RENEWAL_BASE", "http://127.0.0.1:8010").rstrip("/")
NB_BASE = os.environ.get("HORIZON_NB_BASE", "http://127.0.0.1:8011").rstrip("/")

# Standard HTTP hop-by-hop headers — never forwarded by a proxy (RFC 7230
# §6.1). Everything else (content-length, content-encoding, content-type...)
# is forwarded as-is since the body bytes are passed through unmodified.
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}

app = FastAPI(title="Horizon Gateway")
_client: httpx.AsyncClient | None = None


@app.on_event("startup")
async def _startup():
    global _client
    _client = httpx.AsyncClient(timeout=120.0)


@app.on_event("shutdown")
async def _shutdown():
    if _client:
        await _client.aclose()


async def _proxy(request: Request, base: str, strip_prefix: str = "") -> StreamingResponse:
    path = request.url.path
    if strip_prefix and path.startswith(strip_prefix):
        path = path[len(strip_prefix):] or "/"
    url = f"{base}{path}"
    body = await request.body()
    headers = [(k, v) for k, v in request.headers.items() if k.lower() != "host"]

    upstream_req = _client.build_request(
        request.method, url, headers=headers,
        params=list(request.query_params.multi_items()), content=body,
    )
    upstream_resp = await _client.send(upstream_req, stream=True)
    resp_headers = [(k, v) for k, v in upstream_resp.headers.items() if k.lower() not in HOP_BY_HOP]
    return StreamingResponse(
        upstream_resp.aiter_raw(),
        status_code=upstream_resp.status_code,
        headers=dict(resp_headers),
        background=BackgroundTask(upstream_resp.aclose),
    )


@app.get("/healthz")
async def healthz():
    """Liveness probe for the platform. Declared BEFORE the catch-all routes
    below, which would otherwise proxy it to the renewal backend.

    Deliberately checks BOTH engines rather than just answering 200 from the
    gateway process: the gateway is a thin proxy that stays perfectly healthy
    while an engine behind it is dead, so a self-only check would report green
    on a dashboard that 502s half its pages. Returning 503 when an engine is
    unreachable is what lets the platform restart the container instead.

    Must be excluded from authentication -- an authenticated health path fails
    the probe, which the platform reads as a dead container and restarts in a
    loop. See DEPLOYMENT_PLAN.md, Phase 3.
    """
    engines = {"renewal": RENEWAL_BASE, "new_business": NB_BASE}
    results: dict[str, str] = {}
    for name, base in engines.items():
        try:
            r = await _client.get(f"{base}/api/health", timeout=5.0)
            results[name] = "up" if r.status_code == 200 else f"http {r.status_code}"
        except Exception as exc:
            results[name] = f"unreachable ({type(exc).__name__})"
    ok = all(v == "up" for v in results.values())
    return JSONResponse({"status": "ok" if ok else "degraded", "engines": results},
                        status_code=200 if ok else 503)


_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]


@app.api_route("/nb-app/{_:path}", methods=_METHODS)
@app.api_route("/nb-app", methods=_METHODS)
async def proxy_new_business(request: Request, _: str = ""):
    return await _proxy(request, NB_BASE, strip_prefix="/nb-app")


@app.api_route("/{_:path}", methods=_METHODS)
@app.api_route("/", methods=_METHODS)
async def proxy_renewals(request: Request, _: str = ""):
    return await _proxy(request, RENEWAL_BASE)
