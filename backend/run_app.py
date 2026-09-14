"""
Standalone launcher for the renewal engine.

Two run modes:
  - dev / Azure App Service: this file isn't even used there -- local dev
    and the deployed app both start via gateway/run_app.py, which shells out
    to `python -m uvicorn app.main:app` for this backend. Nothing about that
    path changes.
  - frozen (PyInstaller): this IS the entry point
    backend/build/HorizonRenewalEngine.spec targets, built into
    HorizonRenewalEngine.exe by package_app.py. It never
    runs standalone in that form -- gateway/run_app_frozen.py is the one exe
    a recipient actually opens, and it spawns this one as an internal child
    on port 8010 (HORIZON_PORT). See that file's docstring for why the two
    backends stay separate processes/exes rather than one combined build
    (both packages are literally named `app`, exactly like the dev setup).

Frozen-only responsibility this file has that dev mode does not
-----------------------------------------------------------------
A PyInstaller onefile exe's own contents are read-only and re-extracted to a
fresh temp dir on every launch, but this app writes retrain output and
hand-made overrides to disk -- that needs a folder that actually persists.
_ensure_seeded() below points backend_dir() at %LOCALAPPDATA%/Horizon/renewal
(via HORIZON_RENEWAL_DATA_DIR) BEFORE any `app.*` module is imported --
app/bundle.py computes its DATA/MODELS constants at import time, so getting
that env var set first is load-bearing, not just tidy.

Seeding that folder reuses the exact same mechanism as a live deployment's
Admin > "apply a bundle" flow (app/bundle.py) rather than a hand-rolled file
list, so this is provably the same code path already exercised there: this
build's own embedded seed_bundle.zip (staged by package_app.py from
publish.py's output) is applied once, whenever its MANIFEST version differs
from a small marker file already on disk. Files bundle.py doesn't own
(outcome_overrides.csv, lob_overrides.csv, excluded_groups.csv,
group_registry.csv, uploaded_upcoming.csv) are therefore untouched by
construction on every subsequent launch, exactly as they would be after a
real Admin-page apply.

Those same DELTA files are the reason for a SECOND, separate seeding channel:
a bundle may never carry one, but a brand-new laptop needs the curated ones to
exist at all. _seed_delta_files() copies them in only when absent -- see its
docstring.
"""

from __future__ import annotations

import multiprocessing
import os
import shutil
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

HOST = "127.0.0.1"
_PORT_ENV = os.environ.get("HORIZON_PORT")
PORT = int(_PORT_ENV) if _PORT_ENV else 8000
# Standalone (double-clicked directly, no override) opens its own browser tab.
# As the combined product's renewal engine (HORIZON_PORT set by the gateway
# launcher) it's an internal-only process -- the gateway opens the one tab.
OPEN_BROWSER = _PORT_ENV is None

FROZEN = bool(getattr(sys, "frozen", False))


def _bundle_dir() -> Path:
    """Where THIS process's own onefile contents live at runtime. Mirrors
    gateway/run_app_frozen.py's identical helper -- see its docstring for
    why this is not a place to persist anything."""
    return Path(getattr(sys, "_MEIPASS", None) or Path(sys.executable).resolve().parent)


def _ensure_seeded() -> None:
    """Frozen-only: redirect backend_dir() to a persistent per-user folder,
    point frontend_dist_dir() at this exe's own extracted static assets, and
    apply this build's embedded bundle into the data folder if missing or
    stale.

    The frontend redirect matters for a reason specific to frozen mode:
    frontend_dist_dir()'s un-overridden default is computed from this
    module's own __file__, which is fine in dev (a real path under
    backend/app/) and in a container (still a real path), but PyInstaller's
    onefile bootloader gives frozen modules a SYNTHETIC __file__ that does
    not sit where the frontend was actually extracted to
    (sys._MEIPASS/frontend_dist -- see build/HorizonRenewalEngine.spec's
    datas). Left unset, that default resolves to a directory that doesn't
    exist and every request for "/" 404s.
    """
    os.environ["HORIZON_FRONTEND_DIST"] = str(_bundle_dir() / "frontend_dist")

    state_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Horizon" / "renewal"
    (state_dir / "data").mkdir(parents=True, exist_ok=True)
    (state_dir / "models").mkdir(parents=True, exist_ok=True)
    os.environ["HORIZON_RENEWAL_DATA_DIR"] = str(state_dir)

    # Independent of the bundle below (and of whether this build even has
    # one): locally-owned reference files are copied in when absent.
    _seed_delta_files(state_dir)

    seed_zip = _bundle_dir() / "seed_bundle.zip"
    if not seed_zip.exists():
        return  # this build has no embedded seed -- nothing to apply

    from app import bundle  # noqa: E402  (import AFTER the env var above is
                             # set -- bundle.py's DATA/MODELS are module-level
                             # constants computed at import time)

    manifest = bundle.inspect(seed_zip)
    version = str(manifest.get("version") or "")

    marker = state_dir / ".bundle_version"
    if marker.exists() and marker.read_text(encoding="utf-8").strip() == version:
        return
    print(f"  Setting up (first run, or an updated data/model: {version})...")
    bundle.apply(seed_zip)
    marker.write_text(version, encoding="utf-8")



def _seed_delta_files(state_dir: Path) -> None:
    """Copy this build's locally-owned reference files into a fresh state dir,
    WITHOUT overwriting any the recipient already has.

    These cannot travel in seed_bundle.zip: they are bundle DELTA files
    (app/bundle.py DELTA_FILES), and a bundle carrying one is refused outright
    by design -- the rule that stops a publish from wiping hand-made
    corrections on a server. That rule is right for the server and wrong for a
    brand-new laptop, whose per-user folder starts empty: without this, the
    packaged app would come up with no people_aliases.csv and report one rep
    under three different names on the Overview charts.

    "Copy only if missing" is what reconciles the two: a first launch gets the
    curated file, and every launch after leaves whatever is there alone -- so
    if someone edits their copy, a newer build of this exe will not clobber it.
    """
    src_dir = _bundle_dir() / "seed_delta"
    if not src_dir.is_dir():
        return
    dest_dir = state_dir / "data"
    for src in src_dir.iterdir():
        if not src.is_file():
            continue
        dest = dest_dir / src.name
        if dest.exists():
            continue
        shutil.copy2(src, dest)
        print(f"  Seeded {src.name} (first run).")


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex((host, port)) != 0


def _open_browser_when_ready() -> None:
    for _ in range(120):
        if not _port_free(HOST, PORT):
            webbrowser.open(f"http://{HOST}:{PORT}")
            return
        time.sleep(0.5)


def main() -> None:
    if FROZEN:
        _ensure_seeded()

    if OPEN_BROWSER:
        print("=" * 58)
        print("  Horizon -- Crumdale Renewal Forecasting")
        print("=" * 58)
        print("\n  Starting up... your web browser will open automatically")
        print("  in a few seconds. (First launch can take up to a minute.)")
        print("\n  >> Keep this window open while you use Horizon. <<")
        print("     Closing it shuts the app down.\n")
    else:
        print(f"Renewal engine starting on port {PORT} (internal -- the "
              f"combined product's own window is the gateway's).")

    if not _port_free(HOST, PORT):
        print(f"\nIt looks like Horizon is already running (port {PORT} in use).")
        if OPEN_BROWSER:
            print(f"Open your browser to  http://{HOST}:{PORT}  to use it.")
            print("If not, close the other window and try again.")
            try:
                input("\nPress Enter to close this window...")
            except (EOFError, OSError):
                pass
        sys.exit(1)

    from app.main import app
    import uvicorn

    if OPEN_BROWSER:
        threading.Thread(target=_open_browser_when_ready, daemon=True).start()

    print(f"  Horizon is ready at  http://{HOST}:{PORT}")
    print("  (If the browser didn't open, copy that address into it.)\n")

    try:
        # Explicit http/loop/ws implementations -> no httptools/uvloop/
        # websockets to bundle; this app is plain JSON-over-HTTP.
        uvicorn.run(app, host=HOST, port=PORT, log_level="info",
                    http="h11", loop="asyncio", ws="none")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
