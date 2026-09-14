"""
Frozen (PyInstaller) launcher for the combined Horizon product -- the ONLY
exe a recipient ever sees or double-clicks.

Dev mode already has gateway/run_app.py, which shells out to
`python -m uvicorn` for both backends because a real Python interpreter and
this repo's source tree are both present. Neither exists on a recipient's
laptop: there is no system Python to shell out to, and the two backends'
code instead lives inside two OTHER exes -- HorizonRenewalEngine.exe and
HorizonNewBusinessEngine.exe (built by backend/package_app.py and
NewBusiness/backend/package_app.py) -- EMBEDDED inside this one as onefile
data (see gateway/build/Horizon.spec) rather than shipped as visible sibling
files. Why two engine processes instead of one fat combined process: both
backends' internal code lives in a package literally named `app` (see
gateway/main.py's own docstring on why that already rules out importing both
into one process) -- PyInstaller can no more bundle two different `app`
packages into one process's module namespace than Python could import them
at runtime, so each backend has to stay its own separate executable/process,
exactly mirroring how the dev setup already keeps them as separate OS
processes. This file is the frozen-mode equivalent of gateway/run_app.py's
spawn/wait/proxy dance, substituting "launch that engine exe" for "shell out
to python -m uvicorn" -- with an extra step _ensure_engine() handles first:
getting that engine exe out of this process's own onefile bundle and onto
disk somewhere it can actually be launched from (see that function's
docstring).

Auth: nothing to configure here. auth.py's HORIZON_AUTH_MODE defaults to
"dev" (no sign-in, admin by default) unless something has set it to
"easyauth" in this environment -- which is exactly the mode a laptop with no
Entra ID / Easy Auth in front of it needs, and is already the module's own
default for a plain `python gateway/run_app.py`. Nothing frozen-specific
about that; it would be true of any local run.

Data seeding: also nothing to do here. Each engine exe seeds its OWN
persistent per-user folder from its OWN embedded bundle on first launch --
see backend/run_app.py's and NewBusiness/backend/run_app.py's
_ensure_seeded(). This launcher's only job is getting those two exes onto
disk and talking to each other behind one browser tab.

Both engine subprocesses are spawned with CREATE_NO_WINDOW (Windows-only) so
the recipient sees exactly ONE window -- this one -- no matter how many
internal processes are actually running; their stdout/stderr are redirected
to log files under %LOCALAPPDATA%/Horizon/ (the same per-user folder their
own app/paths.py already persists data/models to), so a support conversation
has something concrete to point at ("check %LOCALAPPDATA%/Horizon/
renewal_engine.log") instead of three consoles cluttering the desktop.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from ctypes import wintypes
from pathlib import Path

HOST = "127.0.0.1"
GATEWAY_PORT = 8000
RENEWAL_PORT = 8010
NB_PORT = 8011

CREATE_NO_WINDOW = 0x08000000  # Windows-only subprocess creation flag

# Populated with the two engine subprocesses right after they're spawned, so
# both the console-control handler below and the normal `finally:` shutdown
# path can clean up through the same list.
_children: list[subprocess.Popen] = []

# Kept alive for the process lifetime so the OS-level cleanup in
# _make_kill_on_close_job() actually fires -- see that function's docstring
# for why a hard kill needs this in addition to the console-control handler.
_job_handle: int | None = None


def _make_kill_on_close_job() -> int | None:
    """A Windows Job Object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: every
    process assigned to it dies automatically the moment the job's last
    handle closes -- which Windows guarantees happens when THIS process
    exits, for ANY reason, including a hard TerminateProcess (Task Manager's
    default "End Task", `taskkill /F`, a crash). Verified directly: a plain
    `taskkill /F` on just the gateway process left both engine subprocesses
    running and their ports (8010/8011) held indefinitely -- the console
    control handler above only covers the close-event path (Ctrl+C, the
    window's X button), not a hard kill, and a hard kill is a completely
    ordinary way for this app to end up closed. This is the belt to that
    handler's suspenders: kernel-enforced, needs no cooperation from this
    process's own code at exit time, and covers the case the handler cannot.

    Returns the job handle (kept alive via the module-level _job_handle so it
    isn't garbage-collected/closed early) or None on non-Windows / on any
    failure -- if a job object can't be created, subprocesses still run
    fine, they just lose this specific safety net, so failure here is
    logged, not fatal.
    """
    if sys.platform != "win32":
        return None
    try:
        kernel32 = ctypes.windll.kernel32
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None

        JobObjectExtendedLimitInformation = 9
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(f, ctypes.c_uint64) for f in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                         "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            job, JobObjectExtendedLimitInformation,
            ctypes.byref(info), ctypes.sizeof(info),
        )
        if not ok:
            kernel32.CloseHandle(job)
            return None
        return job
    except OSError:
        return None


def _assign_to_job(job: int | None, proc: subprocess.Popen) -> None:
    if job is None:
        return
    try:
        # Popen._handle is the Win32 process HANDLE on Windows -- undocumented
        # but stable and the standard way to reach it; there is no public
        # equivalent, and this whole function is a no-op everywhere else.
        ctypes.windll.kernel32.AssignProcessToJobObject(job, proc._handle)
    except (AttributeError, OSError):
        pass


def _cleanup_children() -> None:
    for p in _children:
        try:
            p.terminate()
        except OSError:
            pass
    for p in _children:
        try:
            p.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                p.kill()
            except OSError:
                pass


def _install_console_ctrl_handler() -> None:
    """Closing the console window (the X button), a logoff, or a shutdown
    deliver CTRL_CLOSE/LOGOFF/SHUTDOWN_EVENT to a Windows console app -- NONE
    of which reliably raise a catchable KeyboardInterrupt the way Ctrl+C
    does, so the `except KeyboardInterrupt` / `finally:` cleanup in main()
    can be skipped entirely on exactly the path a recipient is most likely to
    use (clicking the X). Without this, the two engine subprocesses -- spawned
    with CREATE_NO_WINDOW, so invisible -- would be orphaned: still running,
    still holding ports 8010/8011, with no window for the recipient to even
    notice or close. Registering a real Win32 console control handler is the
    only way to catch a window-close event and actually get code to run
    before the process dies."""
    if sys.platform != "win32":
        return
    handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

    def _handler(ctrl_type: int) -> bool:
        _cleanup_children()
        return False  # let Windows' own default handler still run after ours

    # Keep a reference alive for the process lifetime -- ctypes callbacks
    # with no surviving Python reference can be garbage-collected out from
    # under the C side, which would crash the handler call, not skip it.
    global _ctrl_handler_ref
    _ctrl_handler_ref = handler_type(_handler)
    ctypes.windll.kernel32.SetConsoleCtrlHandler(_ctrl_handler_ref, True)


def _bundle_dir() -> Path:
    """Where THIS process's own onefile contents live at runtime.
    PyInstaller's onefile bootloader sets sys._MEIPASS to a fresh temp
    directory it extracts everything (this exe's datas/binaries/pyz) into
    on every launch, and cleans back up when this process exits -- so the
    two embedded engine exes are readable here for exactly this process's
    lifetime, but this is NOT a place to launch a long-lived subprocess
    from directly (see _ensure_engine()). Falls back to this exe's own
    folder when not frozen (e.g. run directly with a dev interpreter for
    testing), where sys._MEIPASS doesn't exist."""
    return Path(getattr(sys, "_MEIPASS", None) or Path(sys.executable).resolve().parent)


def _horizon_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Horizon"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _log_dir() -> Path:
    return _horizon_dir()


def _ensure_engine(name: str) -> Path:
    """Get one embedded engine exe (see gateway/build/Horizon.spec's datas)
    out of this process's onefile bundle and onto a STABLE path it can
    actually be spawned from and that survives after this process exits --
    unlike _bundle_dir(), which is wiped the moment this process does, and
    which PyInstaller re-extracts from scratch on every single launch (both
    engine exes together are ~300MB; doing that copy on every launch would
    make every startup slow, not just the first one).

    Persists to %LOCALAPPDATA%/Horizon/bin/<name>, reusing that copy on
    later launches instead of re-copying, UNLESS the embedded copy's size
    differs from what's already there -- a cheap stand-in for "this build
    shipped a newer engine exe than what's currently extracted" without
    needing a separate version file. Copies to a temp name first and
    os.replace()s it into place so a launch interrupted mid-copy (killed,
    crashed, disk full) can never leave a half-written exe sitting at the
    real path for the next launch to try to run.
    """
    src = _bundle_dir() / name
    if not src.exists():
        _fail(f"Internal error: {name} is missing from this build of Horizon.exe.\n"
              "This copy of Horizon.exe appears to be corrupted -- please re-download it.")
    dest_dir = _horizon_dir() / "bin"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    if not dest.exists() or dest.stat().st_size != src.stat().st_size:
        tmp = dest_dir / f"{name}.new"
        shutil.copy2(src, tmp)
        os.replace(tmp, dest)
    return dest


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex((HOST, port)) != 0


def _wait_for_port(port: int, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _port_free(port):
            return True
        time.sleep(0.3)
    return False


def _spawn_engine(exe_path: Path, port_env: str, port: int, log_path: Path) -> subprocess.Popen:
    log = open(log_path, "w", encoding="utf-8")
    env = {**os.environ, port_env: str(port)}
    return subprocess.Popen(
        [str(exe_path)],
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=CREATE_NO_WINDOW,
    )


def _open_browser_when_ready() -> None:
    if _wait_for_port(GATEWAY_PORT):
        webbrowser.open(f"http://{HOST}:{GATEWAY_PORT}")


def _fail(msg: str) -> None:
    print(f"\n{msg}")
    # A blocking pause, not a bare exit: double-clicked from Explorer, this
    # console window has no parent to print to -- without this, it would
    # flash open and close before a recipient could read why it failed.
    # input() can raise EOFError/OSError if stdin isn't a real console (e.g.
    # launched from something that doesn't attach one) -- caught so a broken
    # launch environment fails with this clean message instead of an
    # unhandled-exception traceback on top of it.
    try:
        input("\nPress Enter to close this window...")
    except (EOFError, OSError):
        pass
    sys.exit(1)


def main() -> None:
    global _job_handle
    _install_console_ctrl_handler()
    _job_handle = _make_kill_on_close_job()

    print("=" * 58)
    print("  Horizon -- Renewals + New Business")
    print("=" * 58)
    print("\n  Starting up... your web browser will open automatically")
    print("  in a few seconds. (First launch can take up to a minute.)")
    print("\n  >> Keep this black window open while you use Horizon. <<")
    print("     Closing it shuts the whole app down.\n")

    for port, label in ((GATEWAY_PORT, "gateway"), (RENEWAL_PORT, "renewal engine"),
                         (NB_PORT, "New Business engine")):
        if not _port_free(port):
            _fail(f"It looks like Horizon is already running (port {port}, {label}, is in use).\n"
                  f"Open your browser to  http://{HOST}:{GATEWAY_PORT}  to use it.\n"
                  "If not, close the other window and try again.")

    log_dir = _log_dir()
    print("  Preparing engines (one-time setup is slower; instant after)...")
    renewal_exe = _ensure_engine("HorizonRenewalEngine.exe")
    nb_exe = _ensure_engine("HorizonNewBusinessEngine.exe")
    print("  Starting renewal engine...")
    renewal = _spawn_engine(renewal_exe, "HORIZON_PORT", RENEWAL_PORT, log_dir / "renewal_engine.log")
    print("  Starting New Business engine...")
    nb = _spawn_engine(nb_exe, "HORIZON_NB_PORT", NB_PORT, log_dir / "nb_engine.log")
    # From here on, both children are reachable by the console-control
    # handler too -- a window-close before uvicorn.run's own finally: block
    # ever gets a chance to run must still be able to find and stop them.
    _children.extend([renewal, nb])
    for p in (renewal, nb):
        _assign_to_job(_job_handle, p)

    if not _wait_for_port(RENEWAL_PORT) or not _wait_for_port(NB_PORT):
        _cleanup_children()
        _fail(f"An engine failed to start in time. Check the log files in {log_dir} for details.")

    print(f"  Horizon is ready at  http://{HOST}:{GATEWAY_PORT}")
    print("  (If the browser didn't open, copy that address into it.)\n")

    threading.Thread(target=_open_browser_when_ready, daemon=True).start()

    import uvicorn
    from main import app  # gateway's own reverse-proxy ASGI app (gateway/main.py) --
                           # imports neither backend's `app` package, so no collision.

    try:
        uvicorn.run(app, host=HOST, port=GATEWAY_PORT, log_level="info",
                    http="h11", loop="asyncio", ws="none")
    except KeyboardInterrupt:
        pass
    finally:
        # Normal exit path (Ctrl+C, or uvicorn.run returning). The console
        # control handler above covers the window-close path that never
        # reaches here at all -- see its own docstring for why both exist.
        print("  Stopping engines...")
        _cleanup_children()


if __name__ == "__main__":
    main()
