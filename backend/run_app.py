"""
Standalone launcher for Horizon.

Starts the API (which also serves the built frontend), opens the browser,
and blocks in the foreground. Closing this window stops the server -- the
process is never daemonized/detached, so there is nothing left running
after the console closes.

Packaged with PyInstaller into a single, fully self-contained executable --
the built frontend and a seed copy of the real data/models are bundled
inside it, so it needs no other files alongside it. On first run it copies
that seed into a writable per-user folder (see app/paths.py) so retrain
results persist across runs.

PORT is overridable via HORIZON_PORT so this same exe can run as the
"renewal engine" of the combined Renewals+New Business product (internal
port 8010, no browser tab of its own -- gateway/run_app_frozen.py owns the
one browser tab) as well as its original standalone role (port 8000, opens
its own browser tab). Nothing else about this launcher changes between the
two roles; whether a browser opens is inferred from whether the default port
was overridden.
"""

from __future__ import annotations

import multiprocessing
import os
import socket
import sys
import threading
import time
import webbrowser

from app.paths import backend_dir, ensure_seeded

HOST = "127.0.0.1"
_PORT_ENV = os.environ.get("HORIZON_PORT")
PORT = int(_PORT_ENV) if _PORT_ENV else 8000
# Standalone (double-clicked directly, no override) opens its own browser tab.
# As the combined product's renewal engine (HORIZON_PORT set by the gateway
# launcher) it's an internal-only process -- the gateway opens the one tab.
OPEN_BROWSER = _PORT_ENV is None


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex((host, port)) != 0


def _open_browser_when_ready() -> None:
    for _ in range(60):
        if not _port_free(HOST, PORT):
            webbrowser.open(f"http://{HOST}:{PORT}")
            return
        time.sleep(0.5)


def main() -> None:
    if OPEN_BROWSER:
        print("=" * 58)
        print("  Horizon -- Crumdale Renewal Forecasting")
        print("=" * 58)
        print("\n  Starting up... your web browser will open automatically")
        print("  in a few seconds. (First launch can take up to a minute.)")
        print("\n  >> Keep this black window open while you use Horizon. <<")
        print("     Closing it shuts the app down.\n")
    else:
        print(f"Renewal engine starting on port {PORT} (internal -- the "
              f"combined product's own window is the gateway's).")

    if not _port_free(HOST, PORT):
        print(f"\nIt looks like Horizon is already running (port {PORT} in use).")
        if OPEN_BROWSER:
            print(f"Open your browser to  http://{HOST}:{PORT}  to use it.")
            print("If not, close the other window and try again.")
            # Only meaningful when a human is actually attached to this
            # console (the standalone double-click case) -- as an internal
            # engine spawned headlessly by the gateway launcher there is no
            # one to press Enter, and no console to read it from. Guarded
            # against EOFError/OSError too: even in the standalone case,
            # stdin might not be a real console (e.g. launched from
            # something that doesn't attach one), and that must fail clean
            # rather than crash with an unhandled traceback on top of the
            # message it was trying to show.
            try:
                input("\nPress Enter to close this window...")
            except (EOFError, OSError):
                pass
        sys.exit(1)

    ensure_seeded()

    from app.main import app
    import uvicorn

    if OPEN_BROWSER:
        threading.Thread(target=_open_browser_when_ready, daemon=True).start()

    print(f"  Horizon is ready at  http://{HOST}:{PORT}")
    print("  (If the browser didn't open, copy that address into it.)\n")

    try:
        # explicit http/loop/ws implementations -> no httptools/uvloop/websockets
        # to bundle; this app is plain JSON-over-HTTP, so h11 + asyncio is enough.
        uvicorn.run(app, host=HOST, port=PORT, log_level="info",
                    http="h11", loop="asyncio", ws="none")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    # Retrain uses joblib/sklearn with n_jobs=-1, which spawns worker processes.
    # A frozen exe re-launches itself for each worker; without this guard those
    # workers would re-enter main() instead of running the multiprocessing
    # bootstrap, recursing instead of training.
    multiprocessing.freeze_support()
    main()
