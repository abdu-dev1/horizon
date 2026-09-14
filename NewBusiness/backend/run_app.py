"""
Standalone launcher for the New Business engine -- the "NB engine" half of
the packaged combined product.

Unlike ForecastEngine/backend/run_app.py, this never opens a browser and
never expects to be the thing a person double-clicks: in the packaged
product it's spawned as a plain internal subprocess by
gateway/run_app_frozen.py (mirroring exactly how gateway/run_app.py already
spawns it via `python -m uvicorn` in dev -- this is that same role, just as
its own self-contained exe instead of a bare Python invocation), and the
gateway is the one thing a user ever launches or sees a browser tab from.

Packaged with PyInstaller into a single, fully self-contained executable.
This project's data/models are bundled inside it as a seed (staged by
package_app.py from publish.py's output) and applied into a writable
per-user folder on first run -- see _ensure_seeded() below, which mirrors
backend/run_app.py's identical mechanism via app/bundle.py (the same code
path a live deployment's Admin > apply-a-bundle flow already exercises).
Stage edits and auto-expires made afterwards persist across launches because
they land in that per-user folder, not inside the exe.

The raw "RSD Scorecard Export*.xlsx" workbook is never bundled (see
app/paths.export_search_dir's docstring) -- Retrain requires one to be
placed by hand in that per-user folder; everything else works fully off the
seeded snapshot without it.

Run directly for a quick manual check outside the full combined product:
    HorizonNewBusinessEngine.exe                        (defaults to port 8011)
    HORIZON_NB_PORT=8500 HorizonNewBusinessEngine.exe   (custom port)
"""

from __future__ import annotations

import multiprocessing
import os
import socket
import sys
from pathlib import Path

HOST = "127.0.0.1"
PORT = int(os.environ.get("HORIZON_NB_PORT", "8011"))

FROZEN = bool(getattr(sys, "frozen", False))


def _bundle_dir() -> Path:
    """Where THIS process's own onefile contents live at runtime -- see
    gateway/run_app_frozen.py's identical helper for the full explanation."""
    return Path(getattr(sys, "_MEIPASS", None) or Path(sys.executable).resolve().parent)


def _ensure_seeded() -> None:
    """Frozen-only: redirect backend_dir() to a persistent per-user folder
    and apply this build's embedded bundle into it if missing or stale."""
    state_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Horizon" / "new_business"
    (state_dir / "data").mkdir(parents=True, exist_ok=True)
    (state_dir / "models").mkdir(parents=True, exist_ok=True)
    os.environ["HORIZON_NB_DATA_DIR"] = str(state_dir)

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
    print(f"  New Business engine: setting up (first run, or an updated "
          f"data/model: {version})...")
    bundle.apply(seed_zip)
    marker.write_text(version, encoding="utf-8")


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex((host, port)) != 0


def main() -> None:
    if not _port_free(HOST, PORT):
        print(f"New Business engine: port {PORT} already in use -- exiting.")
        sys.exit(1)

    if FROZEN:
        _ensure_seeded()

    from app.main import app
    import uvicorn

    print(f"New Business engine ready on http://{HOST}:{PORT} (internal -- "
          "use the gateway's own port for the actual app).")
    try:
        # Same rationale as the renewal engine: plain JSON-over-HTTP, no
        # websockets, so h11 + asyncio is enough and nothing extra needs
        # bundling.
        uvicorn.run(app, host=HOST, port=PORT, log_level="info",
                    http="h11", loop="asyncio", ws="none")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    # sklearn's CalibratedClassifierCV / joblib use n_jobs=-1 in the training
    # scripts, which spawn worker processes. A frozen exe re-launches itself
    # for each worker; without this guard those workers would re-enter
    # main() instead of running the multiprocessing bootstrap. Only matters
    # if Retrain is ever exercised in a packaged build (see the module
    # docstring on why that needs a manually-placed export file) -- harmless
    # no-op otherwise.
    multiprocessing.freeze_support()
    main()
