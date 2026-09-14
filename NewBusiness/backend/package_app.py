"""
One-command packager for the New Business engine  ->  run:  python package_app.py

Mirrors ForecastEngine/backend/package_app.py's exact reasoning: publish.py
(at the repo root) builds the current data+model into a bundle, and that
same bundle -- not a second, hand-rolled file list -- is embedded as
`seed_bundle.zip` inside HorizonNewBusinessEngine.exe (see build/
HorizonNewBusinessEngine.spec) and applied at first launch via app/bundle.py
(run_app.py's _ensure_seeded()). One mechanism, the same one the Admin page
uses, instead of two that can drift apart.

No frontend build step here (unlike the renewal side) -- this backend never
serves one; the renewal engine bundles the merged frontend for the whole
combined product (see ../../../backend/package_app.py).

Consequence worth knowing: this packages whatever is CURRENTLY in
NewBusiness/backend/data + models. Run the pipeline first for fresher
numbers:

    python etl_nb.py && python train_nb.py && python build_book.py
    python experiment_nb.py           (review before shipping)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
ROOT = BACKEND.parent.parent
EMBED_SEED = BACKEND / "build" / "embed_seed"
SPEC = BACKEND / "build" / "HorizonNewBusinessEngine.spec"
DIST = BACKEND / "dist"
EXE_NAME = "HorizonNewBusinessEngine.exe"


def sh(cmd: str, cwd: Path | None = None) -> None:
    print(f"  $ {cmd}")
    subprocess.run(cmd, shell=True, cwd=str(cwd) if cwd else None, check=True)


def stage_seed() -> Path:
    sh(f'"{sys.executable}" "{ROOT / "publish.py"}" nb')
    zips = sorted((ROOT / "dist_bundles").glob("horizon-nb-*.zip"),
                  key=lambda p: p.stat().st_mtime)
    if not zips:
        sys.exit("FAILED: publish.py did not produce a New Business bundle")
    latest = zips[-1]

    if EMBED_SEED.exists():
        shutil.rmtree(EMBED_SEED)
    EMBED_SEED.mkdir(parents=True)
    dest = EMBED_SEED / "seed_bundle.zip"
    shutil.copy2(latest, dest)
    return dest


def main() -> None:
    print("== 1/2  publish + stage current data/model as the embedded seed ==")
    seed = stage_seed()
    print(f"   staged {seed}")

    print(f"\n== 2/2  build {EXE_NAME} (PyInstaller) ==")
    exe = DIST / EXE_NAME
    if exe.exists():
        exe.unlink()
    sh(f'"{sys.executable}" -m PyInstaller "{SPEC}" --noconfirm '
       f'--distpath "{DIST}" --workpath "{BACKEND / "build" / "pyi"}"')

    if not exe.exists():
        sys.exit(f"FAILED: {EXE_NAME} was not produced")
    print(f"\nDONE  ->  {exe}")
    print(f"      size {exe.stat().st_size / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
