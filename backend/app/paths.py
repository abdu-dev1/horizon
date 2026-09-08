"""
Resolves where Horizon reads its bundled (read-only) assets from, and where
it persists mutable state (data/, models/) across runs.

Dev mode: both are backend/ and backend/../frontend/dist -- unchanged from
running `python -m uvicorn app.main:app`.

Frozen (PyInstaller onefile) mode: bundled assets extract to a temp folder
that's wiped when the process exits, so mutable state can't live there --
retrains would silently vanish on the next launch. Instead frozen builds
persist data/models to a per-user folder (%LOCALAPPDATA%/Horizon), seeded
once from the bundled copy on first run. That keeps the executable fully
self-contained -- nothing else needs to ship alongside it -- while still
letting retrain/model updates survive between runs.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _backend_source_dir() -> Path:
    """backend/ as laid out in the dev repo (app/paths.py -> app/ -> backend/)."""
    return Path(__file__).resolve().parents[1]


def bundle_dir() -> Path:
    """Root of read-only bundled assets (the temp extraction dir when frozen)."""
    if _frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return _backend_source_dir()


def frontend_dist_dir() -> Path:
    if _frozen():
        return bundle_dir() / "frontend_dist"
    return _backend_source_dir().parent / "frontend" / "dist"


def state_dir() -> Path:
    """Writable, persistent home for data/ and models/."""
    if _frozen():
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Horizon"
        base.mkdir(parents=True, exist_ok=True)
        return base
    return _backend_source_dir()


def backend_dir() -> Path:
    """The data/models root -- writable and persistent."""
    return state_dir()


def _seed_version(base: Path) -> str | None:
    """Build stamp carried inside the bundle (seed/data/VERSION) and mirrored into
    the persistent state dir, so we can tell a fresh distribution from a stale one."""
    f = base / "data" / "VERSION"
    try:
        return f.read_text(encoding="utf-8").strip() if f.exists() else None
    except OSError:
        return None


def ensure_seeded() -> None:
    """Frozen build: seed the persistent state dir from the bundled data/models.

    First run copies everything. On later runs we compare the bundle's VERSION
    stamp to the persisted one: if they differ (i.e. the user installed a NEWER
    build), we REFRESH the bundled files over the stale ones — otherwise a new
    release would silently keep showing a recipient's old data. Files the user
    added that aren't in the bundle are left untouched. No-op in dev, where the
    state dir already IS the source."""
    if not _frozen():
        return
    seed = bundle_dir() / "seed"
    dest = state_dir()
    bundle_ver = _seed_version(seed)
    fresh = bundle_ver is not None and bundle_ver != _seed_version(dest)
    for name in ("data", "models"):
        src, dst = seed / name, dest / name
        if not src.exists():
            continue
        if not dst.exists():
            shutil.copytree(src, dst)
        elif fresh:
            dst.mkdir(parents=True, exist_ok=True)
            for item in src.iterdir():
                if item.is_file():
                    shutil.copy2(item, dst / item.name)
