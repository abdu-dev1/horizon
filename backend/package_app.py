"""
One-command packager for Horizon  ->  run:  python package_app.py

Why this exists
---------------
A one-file exe serves two kinds of content on different clocks: the CODE/frontend
are unpacked fresh from the exe every launch, but DATA/models are copied once into
a per-user folder (%LOCALAPPDATA%/Horizon) and then read from there. If a build
forgets to (a) restage the current data or (b) change the seed VERSION, recipients
keep seeing their OLD data under NEW code. This script removes both foot-guns:

  1. rebuilds the frontend,
  2. restages the CURRENT data/models (never hand-copied, never stale),
  3. stamps a VERSION = build-time + content-hash of everything staged, so it
     ALWAYS changes when the data changes and can never be forgotten,
  4. builds Horizon.exe.

app/paths.ensure_seeded compares that VERSION on launch and refreshes a recipient's
stale copy whenever it differs — so every package this script makes will correctly
overwrite old local data. Bump nothing by hand; just run this.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
ROOT = BACKEND.parent
FRONTEND = ROOT / "frontend"
STAGE = BACKEND / "_seed_stage"
SPEC = BACKEND / "build" / "Horizon.spec"
DIST = BACKEND / "dist"

# Runtime-read data + models to bundle. Backups (*.bak, *.pre_*) are deliberately
# excluded. If a file is added that the app reads at runtime, add it here.
DATA_FILES = [
    "real_scored_book.csv", "real_history.csv", "real_active_book.csv",
    "outcome_overrides.csv", "lob_overrides.csv", "sf_deals.csv",
    "term_reasons.csv", "uploaded_upcoming.csv", "group_registry.csv",
    "excluded_groups.csv",
]
MODEL_FILES = ["real_latest.joblib", "registry.json"]


def sh(cmd: str, cwd: Path | None = None) -> None:
    print(f"  $ {cmd}")
    subprocess.run(cmd, shell=True, cwd=str(cwd) if cwd else None, check=True)


def stage() -> list[str]:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    (STAGE / "data").mkdir(parents=True)
    (STAGE / "models").mkdir(parents=True)
    missing = []
    for f in DATA_FILES:
        src = BACKEND / "data" / f
        if src.exists():
            shutil.copy2(src, STAGE / "data" / f)
        else:
            missing.append(f"data/{f}")
    for m in MODEL_FILES:
        src = BACKEND / "models" / m
        if src.exists():
            shutil.copy2(src, STAGE / "models" / m)
        else:
            missing.append(f"models/{m}")
    return missing


def stamp_version() -> str:
    h = hashlib.sha256()
    for sub in ("data", "models"):
        for p in sorted((STAGE / sub).rglob("*")):
            if p.is_file() and p.name != "VERSION":
                h.update(p.name.encode())
                h.update(p.read_bytes())
    ver = f"{datetime.now():%Y-%m-%d %H:%M}  build-{h.hexdigest()[:12]}"
    (STAGE / "data" / "VERSION").write_text(ver, encoding="utf-8")
    return ver


def main() -> None:
    print("== 1/4  build frontend ==")
    sh("npm run build", cwd=FRONTEND)

    print("== 2/4  stage current data + models ==")
    missing = stage()
    if missing:
        print("   ! not found (skipped):", ", ".join(missing))

    print("== 3/4  stamp seed VERSION (content-hashed) ==")
    ver = stamp_version()
    print("   VERSION =", ver)

    print("== 4/4  build Horizon.exe (PyInstaller) ==")
    if (DIST / "Horizon.exe").exists():
        (DIST / "Horizon.exe").unlink()
    sh(f'"{sys.executable}" -m PyInstaller "{SPEC}" --noconfirm '
       f'--distpath "{DIST}" --workpath "{BACKEND / "build" / "pyi"}"')

    exe = DIST / "Horizon.exe"
    if not exe.exists():
        sys.exit("FAILED: Horizon.exe was not produced")
    print(f"\nDONE  ->  {exe}")
    print(f"      size {exe.stat().st_size / 1e6:.0f} MB   |   data version: {ver}")
    print("Distribute dist/Horizon.exe. Recipients auto-refresh to this data on launch.")


if __name__ == "__main__":
    main()
