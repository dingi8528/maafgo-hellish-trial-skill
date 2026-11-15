"""Verify the portable wrapper without starting ADB, MaaMCP or a device."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/maafgo-hellish-trial/scripts/maa_mcp_bootstrap.py"
SPEC = importlib.util.spec_from_file_location("trial_mcp_bootstrap", SCRIPT)
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.adb = self.root / "adb.exe"
        self.adb.write_bytes(b"not executable; all execution is mocked")
        self.out, self.err = io.StringIO(), io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.out))
        self.enterContext(contextlib.redirect_stderr(self.err))

    def test_help_needs_no_third_party_imports(self):
        result = subprocess.run([sys.executable, "-I", "-S", str(SCRIPT), "--help"],
                                capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--data-dir", result.stdout)
        self.assertIn("--adb-path", result.stdout)
        self.assertNotIn("ModuleNotFoundError", result.stderr)

    def test_missing_explicit_adb_explains_correct_input(self):
        with self.assertRaisesRegex(ValueError, "找不到指定的 ADB.*--adb-path"):
            bootstrap.adb_candidates(str(self.root / "missing.exe"))

    def test_explicit_path_has_priority_over_host_candidates(self):
        with patch.object(bootstrap.shutil, "which") as which:
            self.assertEqual(bootstrap.adb_candidates(str(self.adb)), [self.adb.resolve()])
        which.assert_not_called()

    def test_default_host_candidates_are_deduplicated(self):
        with patch.dict(os.environ, {"ProgramFiles": "", "ProgramW6432": ""}), \
                patch.object(bootstrap.shutil, "which", return_value=str(self.adb)):
            self.assertEqual(bootstrap.adb_candidates(), [self.adb.resolve()])

    def test_adb_endpoint_respects_environment(self):
        self.assertEqual(bootstrap.adb_endpoint({}), ("127.0.0.1", 5037))
        self.assertEqual(bootstrap.adb_endpoint({"ADB_SERVER_SOCKET": "tcp:6000"}), ("127.0.0.1", 6000))
        self.assertEqual(bootstrap.adb_endpoint({"ADB_SERVER_SOCKET": "tcp:[::1]:6001"}), ("::1", 6001))
        self.assertEqual(bootstrap.adb_endpoint({"ANDROID_ADB_SERVER_PORT": "6002"}), ("127.0.0.1", 6002))
        self.assertIsNone(bootstrap.adb_endpoint({"ADB_SERVER_SOCKET": "localfilesystem:test"}))
        for port in ("no", "0", "65536"):
            with self.subTest(port=port), self.assertRaisesRegex(ValueError, "ADB 服务端口"):
                bootstrap.adb_endpoint({"ANDROID_ADB_SERVER_PORT": port})

    def test_existing_daemon_is_not_restarted(self):
        with patch.dict(os.environ, {"ADB_SERVER_SOCKET": "tcp:127.0.0.1:5037"}), \
                patch.object(bootstrap, "server_listening", return_value=True), \
                patch.object(bootstrap.subprocess, "run") as run:
            self.assertTrue(bootstrap.ensure_adb_server(self.adb, self.root / "data"))
        run.assert_not_called()
        self.assertFalse((self.root / "data").exists())

    def test_remote_daemon_is_not_started_or_probed(self):
        with patch.dict(os.environ, {"ADB_SERVER_SOCKET": "tcp:example.invalid:5037"}), \
                patch.object(bootstrap, "server_listening") as listening, \
                patch.object(bootstrap.subprocess, "run") as run:
            self.assertTrue(bootstrap.ensure_adb_server(self.adb, self.root / "data"))
        listening.assert_not_called()
        run.assert_not_called()

    def test_absent_daemon_uses_private_temp_and_only_start_server(self):
        old_temp = os.environ.get("TEMP")
        with patch.dict(os.environ, {"ADB_SERVER_SOCKET": "tcp:127.0.0.1:5037"}), \
                patch.object(bootstrap, "server_listening", side_effect=[False, True]), \
                patch.object(bootstrap.subprocess, "run", return_value=SimpleNamespace(returncode=0, stderr=b"")) as run:
            self.assertTrue(bootstrap.ensure_adb_server(self.adb, self.root / "data"))
        self.assertEqual(run.call_args.args[0], [str(self.adb), "start-server"])
        options = run.call_args.kwargs
        self.assertEqual(options["env"]["TEMP"], options["env"]["TMP"])
        self.assertTrue(Path(options["env"]["TEMP"]).is_relative_to(self.root / "data/adb-host"))
        self.assertEqual(os.environ.get("TEMP"), old_temp)
        self.assertEqual(options["timeout"], 15)
        self.assertEqual(self.out.getvalue(), "")
        self.assertIn("adb_server_started", self.err.getvalue())

    def test_start_failure_is_reported_only_on_stderr(self):
        with patch.dict(os.environ, {"ADB_SERVER_SOCKET": "tcp:127.0.0.1:5037"}), \
                patch.object(bootstrap, "server_listening", return_value=False), \
                patch.object(bootstrap.subprocess, "run", side_effect=OSError("denied")):
            self.assertFalse(bootstrap.ensure_adb_server(self.adb, self.root / "data"))
        self.assertEqual(self.out.getvalue(), "")
        self.assertIn("adb_server_start_failed", self.err.getvalue())

    def test_discovery_uses_original_toolkit_with_confirmed_adb(self):
        original = Mock(return_value=["registered-by-maa"])
        toolkit = type("Toolkit", (), {"find_adb_devices": staticmethod(original)})
        with patch.object(bootstrap, "ensure_adb_server", return_value=True) as ensure:
            bootstrap.install_discovery(toolkit, self.root / "data", str(self.adb))
            self.assertEqual(toolkit.find_adb_devices(), ["registered-by-maa"])
        original.assert_called_once_with(self.adb.resolve())
        ensure.assert_called_once_with(self.adb.resolve(), self.root / "data")
        self.assertEqual(self.out.getvalue(), "")
        self.assertIn("adb_devices_found", self.err.getvalue())

    def test_explicit_discovery_does_not_fallback_to_unrelated_devices(self):
        original = Mock(return_value=[])
        toolkit = type("Toolkit", (), {"find_adb_devices": staticmethod(original)})
        with patch.object(bootstrap, "ensure_adb_server", return_value=True):
            bootstrap.install_discovery(toolkit, self.root / "data", str(self.adb))
            self.assertEqual(toolkit.find_adb_devices(), [])
        original.assert_called_once_with(self.adb.resolve())
        self.assertEqual(self.out.getvalue(), "")
        self.assertIn("adb_devices_empty", self.err.getvalue())

    def test_default_discovery_may_delegate_to_toolkit_auto_discovery(self):
        original = Mock(return_value=["found-auto"])
        toolkit = type("Toolkit", (), {"find_adb_devices": staticmethod(original)})
        with patch.object(bootstrap, "adb_candidates", return_value=[]):
            bootstrap.install_discovery(toolkit, self.root / "data")
            self.assertEqual(toolkit.find_adb_devices(), ["found-auto"])
        original.assert_called_once_with()

    def test_stop_compat_waits_for_post_stop(self):
        post_stop = Mock(return_value=SimpleNamespace(wait=Mock(return_value=SimpleNamespace(succeeded=True))))
        tasker_type = type("Tasker", (), {"post_stop": post_stop})
        bootstrap.install_stop_compat(tasker_type)
        tasker_type().stop()
        post_stop.assert_called_once_with()
        post_stop.return_value.wait.assert_called_once_with()

    def test_existing_stop_api_is_preserved(self):
        original = Mock()
        tasker_type = type("Tasker", (), {"stop": original})
        bootstrap.install_stop_compat(tasker_type)
        self.assertIs(tasker_type.stop, original)

    def test_stop_failure_is_not_reported_as_success(self):
        post_stop = Mock(return_value=SimpleNamespace(wait=lambda: SimpleNamespace(succeeded=False)))
        tasker_type = type("Tasker", (), {"post_stop": post_stop})
        bootstrap.install_stop_compat(tasker_type)
        with self.assertRaisesRegex(RuntimeError, "停止战斗任务失败"):
            tasker_type().stop()


if __name__ == "__main__":
    unittest.main()
