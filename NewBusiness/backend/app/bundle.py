"""
Applying a published New Business bundle to a running deployment.

Why a bundle at all
-------------------
Retraining does not happen in the deployed app; it happens on an admin's
laptop, because the ETL needs human judgment (the September 2026 export
arrived silently filtered to Effective Date >= 2026-01-01 -- rebuilding on it
blindly would have cut training history from 449 wins to 81 and raised no
error). See DEPLOYMENT_PLAN.md. The laptop therefore produces the artifacts and
the server only ever consumes them: a bundle is that hand-off, built by
publish.py at the repo root.

The problem this module exists to solve
---------------------------------------
A bundle carries nb_history.csv, and nb_history.csv is not purely derived -- it
also holds decisions made IN THE APP that the export does not know about yet:
a manual stage edit to Closed Won/Lost, and the past-effective-date
auto-expire (nb_mode.update_stage / auto_expire_lost, tagged with
source=manual_transfer / auto_expired).

Those rows are created on the SERVER. The laptop that built the bundle has
never seen them. So writing a bundle's nb_history.csv over the live one
destroys them -- silently, and with no way to recover the fact that a user
marked a quote lost three weeks ago.

etl_nb._preserve_local_decisions already solves exactly this, but only within
one machine: it reads the PREVIOUS nb_history.csv during a rebuild and
re-appends the local rows. This module applies the same reconciliation at
bundle-apply time, which is the only moment it can work -- once the file is
overwritten the rows are already gone, so a plain "reload from disk" endpoint
would be too late.

The rule, stated once: the bundle is authoritative for everything it can know
about, and local decisions win only where the bundle has no decision for that
opportunity. If the export has since caught up and now carries its own decided
row, the EXPORT wins and the local copy is dropped rather than duplicated --
it has the outcome detail (loss reason, notice-of-sale date) the local
placeholder deliberately left blank.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from pathlib import Path

import pandas as pd

from . import paths

DATA = paths.backend_dir() / "data"
MODELS = paths.backend_dir() / "models"

# Everything a bundle is allowed to contain, and where it lands. An explicit
# allowlist, not "extract the zip": an uploaded archive is untrusted input, and
# a path like ../../etc inside it must not be able to write outside the data
# dirs (zip-slip). Anything else in the archive is ignored.
BUNDLE_LAYOUT: dict[str, Path] = {
    "data/nb_history.csv": DATA / "nb_history.csv",
    "data/nb_pipeline.csv": DATA / "nb_pipeline.csv",
    "data/nb_scored_book.csv": DATA / "nb_scored_book.csv",
    "models/nb_latest.joblib": MODELS / "nb_latest.joblib",
    "models/registry.json": MODELS / "registry.json",
}

REQUIRED = ("data/nb_history.csv", "data/nb_pipeline.csv",
            "data/nb_scored_book.csv", "models/nb_latest.joblib")

# Rows in nb_history.csv that originated in the app, not in the export.
LOCAL_SOURCES = ("manual_transfer", "auto_expired")


def _keys(frame: pd.DataFrame) -> pd.Series:
    """Opportunity identity for reconciliation -- deliberately the same
    (group_name, created_date) pair etl_nb._preserve_local_decisions uses, so
    the two paths agree on what "the same quote" means."""
    return (frame["group_name"].astype(str).str.strip() + "||"
            + pd.to_datetime(frame["created_date"], errors="coerce")
              .dt.strftime("%Y-%m-%d").fillna(""))


def _local_rows() -> pd.DataFrame | None:
    """In-app decisions currently on disk, read BEFORE anything is overwritten."""
    path = DATA / "nb_history.csv"
    if not path.exists():
        return None
    prev = pd.read_csv(path, low_memory=False)
    if prev.empty or "source" not in prev.columns:
        return None
    local = prev[prev["source"].isin(LOCAL_SOURCES)].copy()
    return local if not local.empty else None


def inspect(zip_path: Path) -> dict:
    """Read a bundle's manifest and validate its contents without applying it.

    Separate from apply() so the Admin view can show what is about to happen --
    version, row counts, model metrics -- and the admin can decline. A model
    going live should be a decision, not a side effect of picking a file.
    """
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        missing = [m for m in REQUIRED if m not in names]
        if missing:
            raise ValueError(
                "bundle is missing required member(s): " + ", ".join(missing))
        manifest = json.loads(zf.read("MANIFEST.json")) if "MANIFEST.json" in names else {}
    if manifest.get("product") not in (None, "new_business"):
        raise ValueError(
            f"this is a {manifest['product']!r} bundle, not a New Business one "
            "-- upload it on the matching product's Admin page")
    return manifest


def apply(zip_path: Path) -> dict:
    """Install a bundle, preserving in-app decisions. Returns a summary.

    Order matters and is the whole point: local rows are captured first, the
    bundle is staged to a temp dir and only then moved into place, and the
    local rows are reconciled back afterwards.
    """
    manifest = inspect(zip_path)
    local = _local_rows()

    # Stage everything before touching live files, so a corrupt member cannot
    # leave the data dir half-updated.
    with tempfile.TemporaryDirectory() as tmp:
        staged: dict[Path, Path] = {}
        with zipfile.ZipFile(zip_path) as zf:
            for member, dest in BUNDLE_LAYOUT.items():
                if member not in zf.namelist():
                    continue
                out = Path(tmp) / Path(member).name
                with zf.open(member) as src, out.open("wb") as fh:
                    shutil.copyfileobj(src, fh)
                staged[out] = dest

        for dest in staged.values():
            dest.parent.mkdir(parents=True, exist_ok=True)
        for src, dest in staged.items():
            shutil.move(str(src), str(dest))

    preserved = _reconcile_local(local)
    return {
        "version": manifest.get("version"),
        "applied": sorted(str(p.name) for p in BUNDLE_LAYOUT.values() if p.exists()),
        "local_decisions_preserved": preserved,
        "manifest": manifest,
    }


def _reconcile_local(local: pd.DataFrame | None) -> int:
    """Re-append in-app decisions the newly-installed history doesn't cover.

    Also drops the matching rows from the open pipeline: the bundle still lists
    those opportunities as open (it was built without knowledge of the in-app
    decision), so leaving them would put the same quote in nb_history.csv AND
    nb_pipeline.csv at once -- trained on as decided and scored as open, and
    double-counted in every pipeline total.
    """
    if local is None or local.empty:
        return 0

    hist_path = DATA / "nb_history.csv"
    history = pd.read_csv(hist_path, low_memory=False)

    # The export has caught up on these -- it wins, so drop the local copy
    # rather than duplicating the opportunity.
    keep = local[~_keys(local).isin(set(_keys(history)))]
    if keep.empty:
        return 0

    keep = keep.reindex(columns=history.columns)
    pd.concat([history, keep], ignore_index=True).to_csv(hist_path, index=False)

    pipe_path = DATA / "nb_pipeline.csv"
    if pipe_path.exists():
        pipeline = pd.read_csv(pipe_path, low_memory=False)
        if not pipeline.empty:
            before = len(pipeline)
            pipeline = pipeline[~_keys(pipeline).isin(set(_keys(keep)))]
            if len(pipeline) != before:
                pipeline.to_csv(pipe_path, index=False)

    return int(len(keep))
