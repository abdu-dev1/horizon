"""
One-command packager for the renewal engine  ->  run:  python package_app.py

What this produces: dist/HorizonRenewalEngine.exe, one of the two "engine"
halves of the packaged combined product (see ../package_all.py, which is
what an admin actually runs -- this script on its own is mostly useful for
a quick rebuild-and-smoke-test of this engine alone).

Why this reuses publish.py instead of a hand-rolled data-file list
--------------------------------------------------------------------
The desktop build used to stage its own copy of "the files this app reads at
runtime" (see git history before this file was re-added) -- a second list
that could silently drift from app/bundle.py's BUNDLE_LAYOUT, the one the
live deployment's Admin page actually uses to apply data. This script now
calls publish.py instead: the exact same bundle a real deployment would be
handed, embedded as `seed_bundle.zip` inside the exe (see build/
HorizonRenewalEngine.spec) and applied at first launch by run_app.py's
_ensure_seeded() through the SAME app/bundle.py code path. One mechanism,
proven on the Admin page, instead of two that can disagree.

Consequence worth knowing: this packages whatever is CURRENTLY in
backend/data + backend/models. Run the pipeline first if you want this
build to ship fresher numbers than the last publish:

    python etl_real.py && python train_real.py && python build_book.py
    python walk_forward.py            (review before shipping)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
ROOT = BACKEND.parent
FRONTEND = ROOT / "frontend"
EMBED_SEED = BACKEND / "build" / "embed_seed"
SPEC = BACKEND / "build" / "HorizonRenewalEngine.spec"
DIST = BACKEND / "dist"


def sh(cmd: str, cwd: Path | None = None) -> None:
    print(f"  $ {cmd}")
    subprocess.run(cmd, shell=True, cwd=str(cwd) if cwd else None, check=True)


def stage_seed() -> Path:
    """Publish the current data+model as a bundle and stage it for
    embedding. Reuses publish.py so this build's seed is provably the same
    artifact a real deployment would receive -- see the module docstring."""
    sh(f'"{sys.executable}" "{ROOT / "publish.py"}" renewal')
    zips = sorted((ROOT / "dist_bundles").glob("horizon-renewal-*.zip"),
                  key=lambda p: p.stat().st_mtime)
    if not zips:
        sys.exit("FAILED: publish.py did not produce a renewal bundle")
    latest = zips[-1]

    if EMBED_SEED.exists():
        shutil.rmtree(EMBED_SEED)
    EMBED_SEED.mkdir(parents=True)
    dest = EMBED_SEED / "seed_bundle.zip"
    shutil.copy2(latest, dest)
    return dest


def main() -> None:
    print("== 1/3  build frontend ==")
    sh("npm run build", cwd=FRONTEND)

    print("\n== 2/3  publish + stage current data/model as the embedded seed ==")
    seed = stage_seed()
    print(f"   staged {seed}")

    print("\n== 3/3  build HorizonRenewalEngine.exe (PyInstaller) ==")
    exe = DIST / "HorizonRenewalEngine.exe"
    if exe.exists():
        exe.unlink()
    sh(f'"{sys.executable}" -m PyInstaller "{SPEC}" --noconfirm '
       f'--distpath "{DIST}" --workpath "{BACKEND / "build" / "pyi"}"')

    if not exe.exists():
        sys.exit("FAILED: HorizonRenewalEngine.exe was not produced")
    print(f"\nDONE  ->  {exe}")
    print(f"      size {exe.stat().st_size / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
