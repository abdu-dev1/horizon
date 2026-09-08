"""
Resolves where the New Business backend reads its data/models and its raw
source workbooks. Mirrors ForecastEngine/backend/app/paths.py exactly.

One mode, two knobs. In dev everything sits under NewBusiness/backend/ as laid
out in the repo, so running uvicorn or any of the etl_nb.py / train_nb.py /
build_book.py scripts by hand needs no configuration. In a container the paths
are redirected by environment variable:

    HORIZON_NB_DATA_DIR     root holding data/ and models/  (default: NewBusiness/backend/)
    HORIZON_NB_EXPORT_DIR   raw workbook search dir         (default: NewBusiness/)

The export dir only matters where the ETL runs, which is deliberately NOT the
deployed app -- retraining happens on an admin's laptop against the raw
workbooks, and the container is only ever handed the already-built outputs
(see DEPLOYMENT_PLAN.md for why the ETL needs human judgment). The raw
workbooks are real client data and are never baked into an image.

The PyInstaller frozen mode this file used to carry (`_frozen`, `bundle_dir`,
`state_dir`, `ensure_seeded`, `_seed_version` -- seeding %LOCALAPPDATA% from a
temp extraction dir and version-refreshing it) went away with the desktop .exe.
"""

from __future__ import annotations

import os
from pathlib import Path


def _backend_source_dir() -> Path:
    """NewBusiness/backend/ as laid out in the repo (app/paths.py -> app/ -> backend/)."""
    return Path(__file__).resolve().parents[1]


def _from_env(var: str, default: Path) -> Path:
    raw = os.environ.get(var, "").strip()
    return Path(raw).expanduser().resolve() if raw else default


def backend_dir() -> Path:
    """Writable root for data/ and models/."""
    return _from_env("HORIZON_NB_DATA_DIR", _backend_source_dir())


def export_search_dir() -> Path:
    """Where etl_nb.py looks for "RSD Scorecard Export*.xlsx" (and the optional
    "External Market Pricing*.xlsx"). Local-only by design -- see module docstring."""
    return _from_env("HORIZON_NB_EXPORT_DIR", _backend_source_dir().parent)
