"""
Resolves where the renewal backend reads its data/models and the built frontend.

One mode, two knobs. In dev everything sits under backend/ exactly as laid out
in the repo, so `python -m uvicorn app.main:app` needs no configuration at all.
In a container the same paths are redirected by environment variable, because
the image is read-only and rebuilt on every deploy while the data has to
outlive it (see DEPLOYMENT_PLAN.md):

    HORIZON_RENEWAL_DATA_DIR   root holding data/ and models/   (default: backend/)
    HORIZON_FRONTEND_DIST      the built SPA                    (default: ../frontend/dist)

This file used to carry a second, much larger mode for PyInstaller onefile
builds -- bundled assets extracted to a temp dir that was wiped on exit, so
mutable state had to be seeded once into %LOCALAPPDATA% and version-refreshed
on later launches. All of that (`_frozen`, `bundle_dir`, `state_dir`,
`ensure_seeded`, `_seed_version`) went away with the desktop .exe: an env var
does the same job for a container in one line, and there is no longer a
read-only-bundle-vs-writable-copy distinction to maintain.
"""

from __future__ import annotations

import os
from pathlib import Path


def _backend_source_dir() -> Path:
    """backend/ as laid out in the repo (app/paths.py -> app/ -> backend/)."""
    return Path(__file__).resolve().parents[1]


def _from_env(var: str, default: Path) -> Path:
    raw = os.environ.get(var, "").strip()
    return Path(raw).expanduser().resolve() if raw else default


def backend_dir() -> Path:
    """Writable root for data/ and models/."""
    return _from_env("HORIZON_RENEWAL_DATA_DIR", _backend_source_dir())


def frontend_dist_dir() -> Path:
    """The built SPA this backend serves at "/" (both dashboards -- the New
    Business pages live in the same bundle behind a product switcher)."""
    return _from_env(
        "HORIZON_FRONTEND_DIST", _backend_source_dir().parent / "frontend" / "dist")
