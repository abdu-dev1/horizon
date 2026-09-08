"""
Applying a published renewal bundle to a running deployment.

Same hand-off as the New Business side (see NewBusiness/backend/app/bundle.py
and DEPLOYMENT_PLAN.md): retraining happens on an admin's laptop because the
ETL needs human judgment, publish.py packages the result, and the server only
consumes artifacts.

Bundle vs. delta on this side
-----------------------------
The renewal project keeps its mutable, human-curated state in files of its
own rather than mixed into the derived ones:

    BUNDLE (rebuilt by the pipeline, safe to overwrite)
        real_history.csv, real_active_book.csv, real_scored_book.csv,
        term_reasons.csv, sf_deals.csv, real_latest.joblib, registry.json

    DELTA (created or edited in the app -- never in a bundle, never touched)
        outcome_overrides.csv   outcomes corrected by hand
        lob_overrides.csv       line-of-business corrections
        excluded_groups.csv     groups deliberately removed from the book
        group_registry.csv      curated group identity/merge decisions
        uploaded_upcoming.csv   open-renewal data typed or uploaded in-app

That split is what makes this safe, and it is simpler than the New Business
case: because the delta files are separate paths, applying a bundle physically
cannot overwrite them, so no row-level reconciliation is needed here. (On the
NB side the in-app decisions live INSIDE nb_history.csv, which is why that
module has to merge rather than just avoid.)

Consequence worth knowing: a bundle built on a laptop was produced by an ETL
run that did NOT see the server's current overrides. The overrides survive the
apply, but the derived files in the bundle were computed without them. Anything
an override would have changed is re-applied at read time by real_mode, not
baked into the CSVs, so this stays correct -- but it is the reason the override
files must never be moved into the bundle for convenience later.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from pathlib import Path

from .paths import backend_dir

DATA = backend_dir() / "data"
MODELS = backend_dir() / "models"

# Explicit allowlist of what a bundle may write, and where. Not a blind
# extract: an uploaded archive is untrusted input and a member path like
# ../../etc must not be able to escape the data dirs (zip-slip).
BUNDLE_LAYOUT: dict[str, Path] = {
    "data/real_history.csv": DATA / "real_history.csv",
    "data/real_active_book.csv": DATA / "real_active_book.csv",
    "data/real_scored_book.csv": DATA / "real_scored_book.csv",
    "data/term_reasons.csv": DATA / "term_reasons.csv",
    "data/sf_deals.csv": DATA / "sf_deals.csv",
    "models/real_latest.joblib": MODELS / "real_latest.joblib",
    "models/registry.json": MODELS / "registry.json",
}

REQUIRED = ("data/real_history.csv", "data/real_scored_book.csv",
            "models/real_latest.joblib")

# Present here as documentation and as a guard: publish.py must never include
# these, and apply() refuses a bundle that does. A bundle that quietly shipped
# outcome_overrides.csv would wipe every hand-made correction on the server.
DELTA_FILES = (
    "outcome_overrides.csv", "lob_overrides.csv", "excluded_groups.csv",
    "group_registry.csv", "uploaded_upcoming.csv",
)


def inspect(zip_path: Path) -> dict:
    """Validate a bundle and read its manifest without applying it, so the
    Admin view can show what is about to go live and the admin can decline."""
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        missing = [m for m in REQUIRED if m not in names]
        if missing:
            raise ValueError(
                "bundle is missing required member(s): " + ", ".join(missing))

        # Fail loudly rather than silently skipping: a bundle containing a
        # delta file means publish.py was changed incorrectly, and the safe
        # response is to refuse the whole thing, not to half-apply it.
        smuggled = sorted(n for n in names if Path(n).name in DELTA_FILES)
        if smuggled:
            raise ValueError(
                "bundle contains locally-owned file(s) it must never carry: "
                + ", ".join(smuggled)
                + " -- these hold hand-made corrections that only exist on the "
                  "server. Rebuild the bundle with publish.py.")

        manifest = json.loads(zf.read("MANIFEST.json")) if "MANIFEST.json" in names else {}
    if manifest.get("product") not in (None, "renewal"):
        raise ValueError(
            f"this is a {manifest['product']!r} bundle, not a renewal one "
            "-- upload it on the matching product's Admin page")
    return manifest


def apply(zip_path: Path) -> dict:
    """Install a bundle. Delta files are untouched by construction."""
    manifest = inspect(zip_path)

    # Stage first, move second: a corrupt member must not be able to leave the
    # data dir half-updated.
    with tempfile.TemporaryDirectory() as tmp:
        staged: dict[Path, Path] = {}
        with zipfile.ZipFile(zip_path) as zf:
            available = set(zf.namelist())
            for member, dest in BUNDLE_LAYOUT.items():
                if member not in available:
                    continue
                out = Path(tmp) / Path(member).name
                with zf.open(member) as src, out.open("wb") as fh:
                    shutil.copyfileobj(src, fh)
                staged[out] = dest

        for dest in staged.values():
            dest.parent.mkdir(parents=True, exist_ok=True)
        for src, dest in staged.items():
            shutil.move(str(src), str(dest))

    return {
        "version": manifest.get("version"),
        "applied": sorted(p.name for p in staged.values()),
        "delta_preserved": [f for f in DELTA_FILES if (DATA / f).exists()],
        "manifest": manifest,
    }
