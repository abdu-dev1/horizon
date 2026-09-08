"""
Horizon launcher — starts BOTH backends + the gateway and blocks in the
foreground. This is the single entrypoint for the whole product: it is what
you run locally (`python run_app.py`) and what the container runs as its
CMD. Closing it stops everything.

Configuration (all optional; defaults are the local-dev setup):

    HORIZON_HOST           address the gateway binds      (default 127.0.0.1)
    HORIZON_PORT           port the gateway listens on    (default 8000)
    HORIZON_OPEN_BROWSER   "1"/"0"; default is on only when bound to loopback

The two engines are always bound to loopback on fixed internal ports (8010
renewal, 8011 New Business) and are never reachable from outside the host or
container -- only the gateway is. That is why HORIZON_HOST applies to the
gateway alone.

Why three processes rather than one: both backends' code lives in a package
literally named `app` (backend/app/, NewBusiness/backend/app/), so importing
both into a single interpreter would collide in sys.modules. Each therefore
runs as its own ordinary process and the gateway reverse-proxies to them,
which also means a crash in one backend's model code cannot take down the
other. See gateway/main.py's docstring.

Child supervision: if either engine exits, this process tears the other down
and exits non-zero rather than continuing to serve a half-broken app. In a
container that surfaces as a failed health check and a restart, instead of a
dashboard that silently 502s one of its two products.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

GATEWAY_DIR = Path(__file__).resolve().parent
ROOT = GATEWAY_DIR.parent
RENEWAL_DIR = ROOT / "backend"
NB_DIR = ROOT / "NewBusiness" / "backend"

# The gateway's public bind. 0.0.0.0 in a container; loopback for local dev.
HOST = os.environ.get("HORIZON_HOST", "127.0.0.1").strip() or "127.0.0.1"
GATEWAY_PORT = int(os.environ.get("HORIZON_PORT", "8000"))

# Internal only -- deliberately not configurable, and never bound off-host.
INTERNAL_HOST = "127.0.0.1"
RENEWAL_PORT = 8010
NB_PORT = 8011

_browser_default = "1" if HOST in ("127.0.0.1", "localhost") else "0"
OPEN_BROWSER = os.environ.get("HORIZON_OPEN_BROWSER", _browser_default) == "1"


def _port_free(port: int, host: str = INTERNAL_HOST) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex((host, port)) != 0


def _wait_for_port(port: int, timeout: float = 120.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _port_free(port):
            return True
        time.sleep(0.3)
    return False


def _spawn_uvicorn(cwd: Path, port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--port", str(port), "--host", INTERNAL_HOST],
        cwd=str(cwd),
    )


def _open_browser_when_ready() -> None:
    if _wait_for_port(GATEWAY_PORT, timeout=180.0):
        webbrowser.open(f"http://127.0.0.1:{GATEWAY_PORT}")


def main() -> None:
    print("=" * 58)
    print("  Horizon - Renewals + New Business")
    print("=" * 58)

    for port, label in ((GATEWAY_PORT, "gateway"),
                        (RENEWAL_PORT, "renewal backend"),
                        (NB_PORT, "NB backend")):
        if not _port_free(port):
            sys.exit(f"Port {port} ({label}) is already in use - close whatever's "
                     f"running there and try again.")

    print(f"\n  Starting renewal backend on :{RENEWAL_PORT} ...")
    renewal = _spawn_uvicorn(RENEWAL_DIR, RENEWAL_PORT)
    print(f"  Starting New Business backend on :{NB_PORT} ...")
    nb = _spawn_uvicorn(NB_DIR, NB_PORT)
    engines = ((renewal, "renewal"), (nb, "NB"))

    # A backend that dies during startup leaves its port free forever, so the
    # wait below would burn the full timeout before reporting a useless
    # "failed to start in time". Check liveness as well as the port.
    for proc, label in engines:
        port = RENEWAL_PORT if label == "renewal" else NB_PORT
        while proc.poll() is None and _port_free(port):
            time.sleep(0.3)
        if proc.poll() is not None:
            for other, _ in engines:
                if other.poll() is None:
                    other.terminate()
            sys.exit(f"The {label} backend exited during startup "
                     f"(code {proc.returncode}) - see its traceback above.")

    print(f"  Starting gateway on {HOST}:{GATEWAY_PORT} ...")
    if OPEN_BROWSER:
        threading.Thread(target=_open_browser_when_ready, daemon=True).start()

    def _watch_engines() -> None:
        """Exit the whole app if either engine dies after startup."""
        while True:
            for proc, label in engines:
                if proc.poll() is not None:
                    print(f"\n  !! the {label} backend exited (code "
                          f"{proc.returncode}) - shutting down", flush=True)
                    os._exit(1)
            time.sleep(2.0)

    threading.Thread(target=_watch_engines, daemon=True).start()

    import uvicorn
    sys.path.insert(0, str(GATEWAY_DIR))
    try:
        uvicorn.run("main:app", host=HOST, port=GATEWAY_PORT, app_dir=str(GATEWAY_DIR))
    finally:
        for proc, label in engines:
            if proc.poll() is None:
                print(f"  Stopping {label} backend...")
                proc.terminate()
        for proc, _ in engines:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    main()
