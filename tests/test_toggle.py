"""Exercise socket ownership, stale state and cancellation without audio/models."""
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

import wow_diktate as wd
import wow_diktate_toggle as toggle


class ToggleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wd-toggle-")
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.directory = toggle.runtime_directory()
        self.address = self.directory / "dictation.sock"

    def test_active_socket_receives_cancel_without_starting_process(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
            server.bind(str(self.address))
            server.settimeout(1)
            with mock.patch.object(subprocess, "Popen") as spawn:
                self.assertEqual(toggle.toggle([]), 0)
            self.assertEqual(server.recv(64), b"cancel")
            spawn.assert_not_called()

    def test_stale_socket_is_replaced(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as old:
            old.bind(str(self.address))
        with mock.patch.object(subprocess, "Popen") as spawn:
            toggle.toggle(["--dry-run"])
        spawn.assert_called_once()
        self.assertIn("--dry-run", spawn.call_args.args[0])
        self.assertTrue(spawn.call_args.kwargs["pass_fds"])
        self.assertEqual(self.address.stat().st_mode & 0o777, 0o600)

    def test_background_cli_process_releases_socket_on_exit(self):
        original = subprocess.Popen
        children = []

        def spawn(*args, **kwargs):
            child = original(*args, **kwargs)
            children.append(child)
            return child

        with mock.patch.object(toggle.subprocess, "Popen", side_effect=spawn):
            toggle.toggle(["--help"])
        child = children[0]
        try:
            self.assertEqual(child.wait(timeout=5), 0)
            self.assertFalse(self.address.exists())
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=3)

    def test_spawn_failure_does_not_leave_stale_socket(self):
        with mock.patch.object(subprocess, "Popen", side_effect=OSError("cannot start")):
            with self.assertRaises(OSError):
                toggle.toggle([])
        self.assertFalse(self.address.exists())

    def test_old_pid_file_cannot_kill_an_unrelated_process(self):
        child = subprocess.Popen(["sleep", "30"])
        try:
            Path(self.tmp.name, "wow_diktate.pid").write_text(str(child.pid))
            with mock.patch.object(toggle.subprocess, "Popen"):
                toggle.toggle([])
            self.assertIsNone(child.poll())
        finally:
            child.terminate()
            child.wait(timeout=3)

    def test_socket_cancellation_reaches_cli_and_cleans_up(self):
        ready = threading.Event()
        finished = threading.Event()
        results = []
        server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        server.bind(str(self.address))
        fd = server.detach()

        def main(args, stop_event):
            ready.set()
            self.assertTrue(stop_event.wait(2))
            return 0

        def serve():
            try:
                results.append(toggle.serve(fd, []))
            finally:
                finished.set()

        with mock.patch.object(wd, "main", side_effect=main):
            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            self.assertTrue(ready.wait(2))
            toggle.toggle([])
            self.assertTrue(finished.wait(3))
            thread.join(timeout=1)
        self.assertEqual(results, [0])
        self.assertFalse(self.address.exists())

    def test_shared_runtime_subdirectory_is_rejected(self):
        self.directory.chmod(0o755)
        with self.assertRaisesRegex(RuntimeError, "Unsafe runtime directory"):
            toggle.runtime_directory()
