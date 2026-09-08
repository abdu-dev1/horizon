"""
One-command packager for the New Business engine  ->  run:  python package_app.py

Mirrors ForecastEngine/backend/package_app.py's exact reasoning and structure
for the same reason it exists there: a one-file exe serves DATA/models on a
different clock than CODE (code is unpacked fresh every launch; data/models
are copied ONCE into a per-user folder and read from there after), so a build
that forgets to (a) restage the current data or (b) change the seed VERSION
leaves recipients seeing OLD data under NEW code. This script removes both
foot-guns:

  1. restages the CURRENT data/models (never hand-copied, never stale),
  2. stamps a VERSION = build-time + content-hash of everything staged, so it
     ALWAYS changes when the data changes and can never be forgotten,
  3. builds HorizonNewBusinessEngine.exe.

No frontend build step here (unlike the renewal side) -- this backend never
serves one; the gateway's own package script builds the merged frontend once
and the RENEWAL engine bundles it (see gateway/package_all.py).

app/paths.ensure_seeded compares that VERSION on launch and refreshes a
recipient's stale copy whenever it differs -- so every package this script
makes will correctly overwrite old local data. Bump nothing by hand; just
run this.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
STAGE = BACKEND / "_seed_stage"
SPEC = BACKEND / "build" / "HorizonNewBusinessEngine.spec"
DIST = BACKEND / "dist"
EXE_NAME = "HorizonNewBusinessEngine.exe"

# Runtime-read data + models to bundle. Backups (*.bak, *.pre_*) and the raw
# "RSD Scorecard Export*.xlsx" source workbook are deliberately excluded --
# see app/paths.export_search_dir's docstring for why the workbook itself is
# never shipped inside the exe. If a file is added that the app reads at
# runtime, add it here.
DATA_FILES = ["nb_history.csv", "nb_pipeline.csv", "nb_scored_book.csv"]
MODEL_FILES = ["nb_latest.joblib", "registry.json"]


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
    print("== 1/3  stage current data + models ==")
    missing = stage()
    if missing:
        print("   ! not found (skipped):", ", ".join(missing))

    print("== 2/3  stamp seed VERSION (content-hashed) ==")
    ver = stamp_version()
    print("   VERSION =", ver)

    print(f"== 3/3  build {EXE_NAME} (PyInstaller) ==")
    exe = DIST / EXE_NAME
    if exe.exists():
        exe.unlink()
    sh(f'"{sys.executable}" -m PyInstaller "{SPEC}" --noconfirm '
       f'--distpath "{DIST}" --workpath "{BACKEND / "build" / "pyi"}"')

    if not exe.exists():
        sys.exit(f"FAILED: {EXE_NAME} was not produced")
    print(f"\nDONE  ->  {exe}")
    print(f"      size {exe.stat().st_size / 1e6:.0f} MB   |   data version: {ver}")


if __name__ == "__main__":
    main()
