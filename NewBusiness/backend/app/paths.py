"""
Resolves where the New Business backend reads its bundled (read-only) assets
from, and where it persists mutable state (data/, models/) across runs.

Mirrors ForecastEngine/backend/app/paths.py's exact split for the same reason:

Dev mode: everything lives directly under NewBusiness/backend/ -- unchanged
from running `python -m uvicorn app.main:app` or any of the etl_nb.py /
train_nb.py / build_book.py scripts by hand.

Frozen (PyInstaller onefile) mode: bundled assets extract to a temp folder
that's wiped when the process exits, so mutable state can't live there --
a stage edit, an auto-expire, or a retrain would silently vanish on the next
launch. Instead frozen builds persist data/models to a per-user folder
(%LOCALAPPDATA%/Horizon/NewBusiness), seeded once from the bundled copy on
first run. Nested under the same top-level "Horizon" folder the renewal
side already uses (not a second sibling folder) purely for tidiness -- the
two apps' data never mix, this is just where a support person would look
for both at once.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _backend_source_dir() -> Path:
    """NewBusiness/backend/ as laid out in the dev repo
    (app/paths.py -> app/ -> backend/)."""
    return Path(__file__).resolve().parents[1]


def bundle_dir() -> Path:
    """Root of read-only bundled assets (the temp extraction dir when frozen)."""
    if _frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return _backend_source_dir()


def state_dir() -> Path:
    """Writable, persistent home for data/ and models/."""
    if _frozen():
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Horizon" / "NewBusiness"
        base.mkdir(parents=True, exist_ok=True)
        return base
    return _backend_source_dir()


def backend_dir() -> Path:
    """The data/models root -- writable and persistent."""
    return state_dir()


def export_search_dir() -> Path:
    """Where etl_nb.py looks for the raw "RSD Scorecard Export*.xlsx" workbook.

    Dev: NewBusiness/ (one level above backend/) -- matches the isolation rule
    in NewBusiness/README.md exactly, unchanged.

    Frozen: the persistent per-user state dir, NOT anywhere inside the exe.
    The export is real client/business data; it is deliberately never bundled
    into a shareable executable (the renewal side makes the same call --
    Horizon.exe ships a pre-trained snapshot, not its raw source spreadsheets
    either). A recipient who wants the Retrain button to work drops a fresh
    export file into %LOCALAPPDATA%/Horizon/NewBusiness/ by hand; everything
    else in the app works fully off the seeded snapshot without it.
    """
    if _frozen():
        return state_dir()
    return _backend_source_dir().parent


def _seed_version(base: Path) -> str | None:
    """Build stamp carried inside the bundle (seed/data/VERSION) and mirrored
    into the persistent state dir, so a fresh distribution can be told apart
    from a stale one."""
    f = base / "data" / "VERSION"
    try:
        return f.read_text(encoding="utf-8").strip() if f.exists() else None
    except OSError:
        return None


def ensure_seeded() -> None:
    """Frozen build: seed the persistent state dir from the bundled data/models.

    First run copies everything. On later runs we compare the bundle's VERSION
    stamp to the persisted one: if they differ (i.e. the user installed a NEWER
    build), we REFRESH the bundled files over the stale ones -- otherwise a new
    release would silently keep showing a recipient's old data. Files the user
    added that aren't in the bundle (e.g. a dropped-in export workbook) are
    left untouched. No-op in dev, where the state dir already IS the source.
    """
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
