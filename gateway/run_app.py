"""
Combined Horizon launcher (dev) — starts BOTH backends + the gateway with one
command, opens the browser once everything's ready, and blocks in the
foreground. Closing this window stops everything (all three are child
processes of this one).

Run:  python run_app.py   (from gateway/, with all three requirements.txt
                            installed: backend/, NewBusiness/backend/, gateway/)

This is the DEV-mode launcher: each backend is started the same way you'd
start it by hand (`uvicorn app.main:app --port ...`, with the right cwd), just
automated into one command instead of three terminals. It does NOT yet handle
PyInstaller-frozen mode (a onefile exe can't shell out to a system Python/
uvicorn — that needs the self-relaunch dispatch pattern backend/run_app.py
already uses for its multiprocessing workers, extended here to dispatch to
each backend). That's the next step before this can be packaged into a single
.exe like Horizon.exe today; this script is the foundation for it, not the
finished packaging.
"""
from __future__ import annotations

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

HOST = "127.0.0.1"
GATEWAY_PORT = 8000
RENEWAL_PORT = 8010
NB_PORT = 8011


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex((HOST, port)) != 0


def _wait_for_port(port: int, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _port_free(port):
            return True
        time.sleep(0.3)
    return False


def _spawn_uvicorn(cwd: Path, port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--host", HOST],
        cwd=str(cwd),
    )


def _open_browser_when_ready() -> None:
    if _wait_for_port(GATEWAY_PORT):
        webbrowser.open(f"http://{HOST}:{GATEWAY_PORT}")


def main() -> None:
    print("=" * 58)
    print("  Horizon — Renewals + New Business (combined)")
    print("=" * 58)

    for port, label in ((GATEWAY_PORT, "gateway"), (RENEWAL_PORT, "renewal backend"), (NB_PORT, "NB backend")):
        if not _port_free(port):
            sys.exit(f"Port {port} ({label}) is already in use — close whatever's "
                      f"running there and try again.")

    print(f"\n  Starting renewal backend on :{RENEWAL_PORT} ...")
    renewal = _spawn_uvicorn(RENEWAL_DIR, RENEWAL_PORT)
    print(f"  Starting New Business backend on :{NB_PORT} ...")
    nb = _spawn_uvicorn(NB_DIR, NB_PORT)

    if not _wait_for_port(RENEWAL_PORT) or not _wait_for_port(NB_PORT):
        sys.exit("A backend failed to start in time — check its output above.")

    print(f"  Starting gateway on :{GATEWAY_PORT} ...")
    threading.Thread(target=_open_browser_when_ready, daemon=True).start()

    import uvicorn
    sys.path.insert(0, str(GATEWAY_DIR))
    try:
        uvicorn.run("main:app", host=HOST, port=GATEWAY_PORT, app_dir=str(GATEWAY_DIR))
    finally:
        for p, label in ((renewal, "renewal"), (nb, "NB")):
            print(f"  Stopping {label} backend...")
            p.terminate()
        for p in (renewal, nb):
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
