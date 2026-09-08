# =====================================================================
# Horizon - Renewals + New Business, one image, three processes.
#
# Three processes rather than one because both backends ship a package
# literally named `app`; they cannot share an interpreter (see
# gateway/main.py). gateway/run_app.py supervises all three and is the
# single entrypoint for local dev and for this container alike.
#
# NO DATA OR MODELS ARE BAKED IN. The image is code only:
#   - data/models arrive on a mounted volume (or are fetched from a
#     published bundle at startup - DEPLOYMENT_PLAN.md Phase 5),
#   - the raw client workbooks are never shipped in an image at all.
# A container started against an empty data volume will report unhealthy
# until a bundle is present. That is deliberate: serving a dashboard of
# zeros would be worse than not serving.
# =====================================================================

# ---------- stage 1: build the SPA (both dashboards, one bundle) ----------
FROM node:24-slim AS frontend

WORKDIR /build
# package.json + lockfile first so `npm ci` is cached independently of source
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


# ---------- stage 2: runtime ----------
FROM python:3.12-slim AS runtime

# Python behaviour suited to containers: no .pyc writes, unbuffered stdout so
# logs reach the platform's log stream as they happen rather than on flush.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Requirements before source: dependency install is the slow layer (~150 MB of
# scikit-learn/scipy/pandas wheels) and must not be invalidated by a code edit.
# All three sets are installed into one interpreter and are pinned to identical
# versions on purpose -- a divergence between the engines would be unresolvable
# here. See the comment at the top of backend/requirements.txt for why these are
# exact pins and not floors (pickled sklearn models).
COPY backend/requirements.txt            /tmp/req-renewal.txt
COPY NewBusiness/backend/requirements.txt /tmp/req-nb.txt
COPY gateway/requirements.txt            /tmp/req-gateway.txt
RUN pip install -r /tmp/req-renewal.txt -r /tmp/req-nb.txt -r /tmp/req-gateway.txt \
 && rm /tmp/req-*.txt

# Application source. .dockerignore keeps data/models/workbooks out of the
# build context entirely, so these COPYs cannot pick them up by accident.
COPY backend/     ./backend/
COPY NewBusiness/ ./NewBusiness/
COPY gateway/     ./gateway/

# The built SPA, served at "/" by the renewal backend's static mount.
COPY --from=frontend /build/dist ./frontend/dist

# Writable roots for data + models. Mount a volume over /var/horizon in the
# platform; these paths are what app/paths.py reads.
RUN mkdir -p /var/horizon/renewal /var/horizon/nb
ENV HORIZON_RENEWAL_DATA_DIR=/var/horizon/renewal \
    HORIZON_NB_DATA_DIR=/var/horizon/nb \
    HORIZON_FRONTEND_DIST=/app/frontend/dist \
    HORIZON_HOST=0.0.0.0 \
    HORIZON_PORT=8000 \
    HORIZON_OPEN_BROWSER=0 \
    HORIZON_AUTH_MODE=easyauth

# Auth is pinned ON in the image, not left to an app setting. gateway/auth.py
# defaults to `dev` so local development needs no configuration, but the
# container is the artifact that actually gets deployed -- so it must not be
# possible to ship this image and accidentally serve the book anonymously.
# In easyauth mode a request without Easy Auth's principal header is rejected
# with 401 rather than treated as a guest: a missing header means Easy Auth is
# not in front of the container, which is an exposure, not a visitor.
#
# HORIZON_ADMIN_EMAILS is intentionally NOT set here -- it is deployment
# config, and an empty allowlist grants no one admin (fail closed).

# Run as a non-root user; it needs write access to the data roots.
RUN useradd --create-home --uid 10001 horizon \
 && chown -R horizon:horizon /var/horizon /app
USER horizon

EXPOSE 8000

# Checks BOTH engines, not just the proxy -- see gateway/main.py::healthz.
# start-period is generous because loading the two models (148 MB + 11 MB) and
# building initial state takes appreciably longer than an empty web app.
HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=8).status==200 else 1)"

CMD ["python", "gateway/run_app.py"]
