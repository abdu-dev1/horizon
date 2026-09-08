"""
One-command packager for the COMBINED Horizon product (Renewals + New
Business) -> run:  python package_all.py   (from the repo root)

What this produces
-------------------
dist_combined/Horizon/
  Horizon.exe        the ONLY file a recipient ever sees -- built from
                      gateway/build/Horizon.spec, with the renewal engine
                      (backend/package_app.py's build) and the New Business
                      engine (NewBusiness/backend/package_app.py's build)
                      embedded INSIDE it as onefile data, not shipped
                      alongside it. Horizon.exe self-extracts both to
                      %LOCALAPPDATA%/Horizon/bin on first launch and runs
                      them from there on every launch after (see
                      gateway/run_app_frozen.py's _ensure_engine()).
  README.txt          one-paragraph usage note for the recipient
dist_combined/Horizon.zip   the same folder, zipped (still just the one exe
                             + readme inside) -- send this, or Horizon.exe
                             directly, either works.

Why two engine BUILDS internally, even though the recipient only ever sees
one exe: both backends' internal code lives in a package literally named
`app` (see gateway/main.py's docstring) -- PyInstaller can no more merge two
different `app` packages into one process's module namespace than Python
could import them at runtime, so each backend still has to run as its own
OS process, exactly mirroring the dev setup. What changed is only how those
two builds get to the recipient's machine: embedded inside the one exe
instead of sitting next to it as visible sibling files. See
gateway/run_app_frozen.py's docstring for the runtime side of this.

Why this script, not three separate manual builds: the same class of
foot-gun package_app.py already guards against on the renewal side (forgot
to restage data, forgot the frontend was stale) is EASIER to hit with three
build steps instead of one, not harder -- this makes "build everything
current" one command instead of remembering four, in the right order, with
the right relative paths.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"
RENEWAL_BACKEND = ROOT / "backend"
NB_BACKEND = ROOT / "NewBusiness" / "backend"
GATEWAY = ROOT / "gateway"

OUT_DIR = ROOT / "dist_combined" / "Horizon"
ZIP_PATH = ROOT / "dist_combined" / "Horizon.zip"

README = """\
Horizon -- Crumdale Specialty Renewal & New Business Forecasting
==================================================================

To run: double-click Horizon.exe. That's the only file you need.

First launch on a new machine: Windows may show a blue "Windows protected
your PC" SmartScreen screen, or your antivirus may pause to scan the file --
this is normal for any app from outside the Microsoft Store, not a sign
anything is wrong. Click "More info" then "Run anyway" to continue. The
very first launch also unpacks some internal setup files, so it can take up
to a minute or two before your browser opens automatically -- every launch
after that is fast. Closing the black "Horizon" console window shuts the
whole app down.

Your data (retrains, stage edits, uploads) is saved to
  %LOCALAPPDATA%\\Horizon\\
and persists across launches and across replacing this exe with a newer
copy. If something goes wrong, the two engines' logs are in that same
folder (renewal_engine.log, NewBusiness\\nb_engine.log).
"""


def sh(cmd: str, cwd: Path | None = None) -> None:
    print(f"  $ {cmd}")
    subprocess.run(cmd, shell=True, cwd=str(cwd) if cwd else None, check=True)


def main() -> None:
    print("== 1/6  build the merged frontend ==")
    sh("npm run build", cwd=FRONTEND)

    print("\n== 2/6  build the renewal engine (stages current renewal data) ==")
    sh(f'"{sys.executable}" package_app.py', cwd=RENEWAL_BACKEND)
    renewal_exe = RENEWAL_BACKEND / "dist" / "Horizon.exe"
    if not renewal_exe.exists():
        sys.exit(f"FAILED: {renewal_exe} was not produced")

    print("\n== 3/6  build the New Business engine (stages current NB data) ==")
    sh(f'"{sys.executable}" package_app.py', cwd=NB_BACKEND)
    nb_exe = NB_BACKEND / "dist" / "HorizonNewBusinessEngine.exe"
    if not nb_exe.exists():
        sys.exit(f"FAILED: {nb_exe} was not produced")

    print("\n== 4/6  stage both engine exes for embedding (renamed, gateway/build/embed) ==")
    embed_dir = GATEWAY / "build" / "embed"
    if embed_dir.exists():
        shutil.rmtree(embed_dir)
    embed_dir.mkdir(parents=True)
    shutil.copy2(renewal_exe, embed_dir / "HorizonRenewalEngine.exe")
    shutil.copy2(nb_exe, embed_dir / "HorizonNewBusinessEngine.exe")

    print("\n== 5/6  build the gateway launcher (embeds both engine exes inside it) ==")
    gateway_dist = GATEWAY / "dist"
    gateway_work = GATEWAY / "build" / "pyi"
    sh(f'"{sys.executable}" -m PyInstaller "{GATEWAY / "build" / "Horizon.spec"}" --noconfirm '
       f'--distpath "{gateway_dist}" --workpath "{gateway_work}"')
    gateway_exe = gateway_dist / "Horizon.exe"
    if not gateway_exe.exists():
        sys.exit(f"FAILED: {gateway_exe} was not produced")

    print("\n== 6/6  assemble + zip ==")
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)
    shutil.copy2(gateway_exe, OUT_DIR / "Horizon.exe")
    (OUT_DIR / "README.txt").write_text(README, encoding="utf-8")

    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in OUT_DIR.iterdir():
            zf.write(f, arcname=f"Horizon/{f.name}")

    total_mb = sum(f.stat().st_size for f in OUT_DIR.iterdir()) / 1e6
    print(f"\nDONE  ->  {OUT_DIR}   ({total_mb:.0f} MB, just Horizon.exe + README.txt now)")
    print(f"      zipped  ->  {ZIP_PATH}   ({ZIP_PATH.stat().st_size / 1e6:.0f} MB)")
    print("Send Horizon.zip (or Horizon.exe on its own -- it's fully self-contained).")


if __name__ == "__main__":
    main()
