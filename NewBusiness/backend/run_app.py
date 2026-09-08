"""
Standalone launcher for the New Business backend -- the "NB engine" half of
the packaged combined product.

Unlike ForecastEngine/backend/run_app.py, this never opens a browser and
never expects to be the thing a person double-clicks: in the packaged
product it's spawned as a plain internal subprocess by gateway/run_app_frozen.py
(mirroring exactly how gateway/run_app.py already spawns it via
`python -m uvicorn` in dev -- this is that same role, just as its own
self-contained exe instead of a bare Python invocation), and the gateway is
the one thing a user ever launches or sees a browser tab from.

Packaged with PyInstaller into a single, fully self-contained executable --
this project's data/models are bundled inside it as a seed, copied into a
writable per-user folder on first run (see app/paths.py) so stage edits,
auto-expires, and retrains persist across launches. The raw "RSD Scorecard
Export*.xlsx" workbook is never bundled (see app/paths.export_search_dir's
docstring) -- Retrain requires one to be placed by hand in that per-user
folder; everything else works fully off the seeded snapshot without it.

Run directly for a quick manual check outside the full combined product:
    HorizonNewBusinessEngine.exe            (defaults to port 8011)
    HORIZON_NB_PORT=8500 HorizonNewBusinessEngine.exe   (custom port)
"""

from __future__ import annotations

import multiprocessing
import os
import socket
import sys

from app.paths import ensure_seeded

HOST = "127.0.0.1"
PORT = int(os.environ.get("HORIZON_NB_PORT", "8011"))


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex((host, port)) != 0


def main() -> None:
    if not _port_free(HOST, PORT):
        print(f"New Business engine: port {PORT} already in use -- exiting.")
        sys.exit(1)

    ensure_seeded()

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
    # sklearn's CalibratedClassifierCV / joblib training use n_jobs=-1, which
    # spawns worker processes. A frozen exe re-launches itself for each
    # worker; without this guard those workers would re-enter main() instead
    # of running the multiprocessing bootstrap. Only matters if Retrain is
    # ever exercised in a packaged build (see the module docstring on why
    # that needs a manually-placed export file) -- harmless no-op otherwise.
    multiprocessing.freeze_support()
    main()
