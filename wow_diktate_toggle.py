#!/usr/bin/env python3
"""Start/cancel a CLI dictation using a private socket, never a saved PID."""
import fcntl
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import threading


def runtime_directory():
    base = os.environ.get("XDG_RUNTIME_DIR")
    path = (Path(base) / "wow-diktate" if base else
            Path(tempfile.gettempdir()) / f"wow-diktate-{os.getuid()}")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077):
        raise RuntimeError(f"Unsafe runtime directory: {path} (must be owned by you, mode 700)")
    return path


def toggle(args):
    directory = runtime_directory()
    address = directory / "dictation.sock"
    # Serialize simultaneous hotkey presses and stale-socket cleanup.
    with open(directory / "toggle.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
            client.setblocking(False)
            try:
                client.connect(str(address))
                client.send(b"cancel")
                return 0
            except BlockingIOError:
                return 0  # another cancellation is already queued
            except (FileNotFoundError, ConnectionRefusedError):
                address.unlink(missing_ok=True)
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
            server.bind(str(address))
            os.chmod(address, 0o600)
            try:
                with open(directory / "dictation.log", "w") as output:
                    subprocess.Popen(
                        [sys.executable, str(Path(__file__).resolve()),
                         "--serve-fd", str(server.fileno()), *args],
                        stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                        pass_fds=(server.fileno(),), start_new_session=True)
            except Exception:
                address.unlink(missing_ok=True)
                raise
    return 0


def serve(fd, args):
    from wow_diktate import main

    directory = runtime_directory()
    stop = threading.Event()
    server = socket.socket(fileno=fd)

    def listen():
        try:
            while True:
                if server.recv(64) == b"cancel":
                    stop.set()
        except OSError:
            pass  # the main thread closed the socket during shutdown

    threading.Thread(target=listen, daemon=True).start()
    try:
        return main(args, stop_event=stop)
    finally:
        with open(directory / "toggle.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            (directory / "dictation.sock").unlink(missing_ok=True)
            server.close()


if __name__ == "__main__":
    os.umask(0o077)
    try:
        if sys.argv[1:2] == ["--serve-fd"]:
            sys.exit(serve(int(sys.argv[2]), sys.argv[3:]))
        sys.exit(toggle(sys.argv[1:]))
    except (OSError, RuntimeError) as exc:
        print(f"wow-diktate toggle: {exc}", file=sys.stderr)
        sys.exit(1)
