"""Offline behavioral checks: bounded HTTP sampling and truthful version comparisons."""
import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/maafgo-hellish-trial/scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


net = load("network_probe")
env = load("check_environment")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/slow":
            time.sleep(0.5)
        body = b"x" * 65536
        if path == "/range":
            self.send_response(206)
            self.send_header("Content-Range", "bytes 0-65535/1048576")
            self.send_header("Content-Length", "65536")
        elif path == "/partial-unknown":
            self.send_response(206)
            self.send_header("Content-Length", "65536")
        else:
            self.send_response(200)
            if path != "/unknown":
                self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


class NetworkProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def sample(self, path, **args):
        # Explicitly bypass machine proxy settings only for this local test client.
        from urllib.request import build_opener, ProxyHandler
        opener = build_opener(ProxyHandler({}))
        with patch.object(net, "urlopen", opener.open):
            return net.probe(self.base + path, max_bytes=4096, seconds=args.pop("seconds", 1), **args)

    def test_content_range_uses_whole_size_and_caps_sample(self):
        result = self.sample("/range")
        self.assertEqual(result["sample_bytes"], 4096)
        self.assertEqual(result["total_bytes"], 1048576)
        self.assertEqual(result["total_size_source"], "content_range")
        self.assertIsNotNone(result["estimated_remaining_seconds"])

    def test_ignored_range_still_caps_bytes(self):
        result = self.sample("/ignore")
        self.assertEqual(result["sample_bytes"], 4096)
        self.assertEqual(result["total_bytes"], 65536)

    def test_partial_content_length_is_not_the_file_size(self):
        result = self.sample("/partial-unknown")
        self.assertIsNone(result["total_bytes"])
        self.assertIsNone(result["estimated_remaining_seconds"])

    def test_unknown_size_does_not_invent_eta(self):
        result = self.sample("/unknown")
        self.assertEqual(result["sample_bytes"], 4096)
        self.assertIsNone(result["total_bytes"])
        self.assertIsNone(result["estimated_remaining_seconds"])

    def test_known_size_is_used_only_when_headers_do_not_supply_total(self):
        self.assertEqual(self.sample("/unknown", total_bytes=900000)["total_bytes"], 900000)
        self.assertEqual(self.sample("/range", total_bytes=900000)["total_bytes"], 1048576)

    def test_timeout_is_bounded_and_requires_user_choice(self):
        started = time.monotonic()
        result = self.sample("/slow", seconds=0.12)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(result["status"], "time_limit")
        self.assertTrue(result["requires_user_choice"])

    def test_signed_query_and_private_path_are_not_reported(self):
        result = self.sample("/private?token=never-print-this")
        self.assertNotIn("never-print", json.dumps(result))
        self.assertNotIn("/private", result["source"])

    def test_failure_after_fast_partial_data_requires_choice_and_invalidates_eta(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = self.base + "/private?token=secret"
        response.headers = {"Content-Length": "32768"}
        response.status = 200
        response.read1.side_effect = [b"x" * 16384, OSError("secret signed URL")]
        with patch.object(net, "urlopen", return_value=response), \
                patch.object(net.time, "perf_counter", side_effect=[0.0, 0.001, 0.002, 0.003]):
            result = net.probe(self.base, max_bytes=32768)
        self.assertEqual(result["status"], "request_failed")
        self.assertEqual(result["sample_bytes"], 16384)
        self.assertFalse(result["slow"])
        self.assertTrue(result["requires_user_choice"])
        self.assertIsNone(result["estimated_remaining_seconds"])
        self.assertNotIn("secret", json.dumps(result))

    def test_unsupported_schemes_credentials_and_bad_limits_are_rejected(self):
        for value in ("file:///secret", "ftp://example.com/file", "https://user:pass@example.com/f"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                net.probe(value)
        for args in ({"max_bytes": 0}, {"seconds": 0}, {"total_bytes": -1},
                     {"seconds": float("nan")}, {"seconds": float("inf")},
                     {"seconds": float("-inf")}, {"max_bytes": float("nan")},
                     {"max_bytes": float("inf")}, {"total_bytes": float("nan")},
                     {"total_bytes": float("inf")}, {"max_bytes": 1.5}):
            with self.assertRaises(ValueError):
                net.probe(self.base, **args)


class EnvironmentTests(unittest.TestCase):
    def test_offline_mode_never_queries_remote(self):
        with patch.object(env, "fetch_json", side_effect=AssertionError("network forbidden")):
            result = env.environment()
        self.assertEqual(result["upstream"]["status"], "not_checked")
        self.assertEqual(result["vision"], "requires_actual_screenshot_test_by_agent")

    def test_source_and_release_are_separate_and_404_is_unknown(self):
        calls = []

        def fetch(url):
            calls.append(url)
            if url.endswith("/repos/owner/source"):
                return {"status": "checked", "data": {"default_branch": "main"}}
            if "/commits/" in url:
                return {"status": "checked", "data": {"sha": "abc"}}
            return {"status": "unknown", "reason": "http_404"}

        with patch.object(env, "fetch_json", side_effect=fetch):
            result = env.upstream_versions("owner/source", "owner/releases")
        self.assertIn("https://api.github.com/repos/owner/releases/releases/latest", calls)
        self.assertNotIn("https://api.github.com/repos/owner/source/releases/latest", calls)
        self.assertEqual(result["stable_release"]["status"], "unknown")
        self.assertIsNone(result["packages"]["maa-mcp"]["version"])

    def test_zip_declaration_does_not_prove_head_is_latest(self):
        local = {"packages": {"maa-mcp": "1.0", "maafw": None}, "project": {
            "status": "checked", "git": {"head": None}, "interface": {"declared_version": "v2"}}}
        upstream = {"packages": {"maa-mcp": {"version": "2.0"}, "maafw": {"version": None}},
                    "commit": {"sha": "abc"}, "stable_release": {"tag": "v2"}}
        result = env.compare(local, upstream)
        self.assertEqual(result["project_head"], "unknown")
        self.assertEqual(result["declared_release_tag"], "matches")
        self.assertEqual(result["packages"]["maa-mcp"], "different")
        self.assertEqual(result["packages"]["maafw"], "not_installed")

    def test_network_failure_does_not_report_latest(self):
        local = {"packages": {"maa-mcp": "1.0", "maafw": "5.0"}, "project": {
            "status": "checked", "git": {"head": "local"}, "interface": {"declared_version": "v2"}}}
        upstream = {"packages": {name: {"version": None} for name in local["packages"]},
                    "commit": {"sha": None}, "stable_release": {"tag": None}}
        result = env.compare(local, upstream)
        self.assertEqual(result["project_head"], "unknown")
        self.assertEqual(result["packages"]["maa-mcp"], "unknown")

    def test_project_has_explicit_missing_file_and_interface_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "assets").mkdir()
            (root / "assets/interface.json").write_text('{"version":"v-test"}', encoding="utf-8")
            result = env.inspect_project(root)
        self.assertEqual(result["interface"]["declared_version"], "v-test")
        self.assertFalse(any(result["required_files"].values()))
        self.assertEqual(result["git"]["status"], "not_a_project_git_checkout")

    @unittest.skipUnless(shutil.which("git"), "Git is not installed")
    def test_git_dirty_includes_untracked_and_tracked_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()

            def git(*args):
                return subprocess.run(["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root),
                                       *args], check=True, capture_output=True)

            git("init")
            (root / "tracked").write_text("original", encoding="utf-8")
            git("add", "tracked")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                "-c", "commit.gpgsign=false", "commit", "-m", "fixture")
            clean = env.inspect_git(root)
            self.assertFalse(clean["dirty"])
            self.assertEqual(len(clean["head"]), 40)
            (root / "new").write_text("new", encoding="utf-8")
            self.assertTrue(env.inspect_git(root)["dirty"])
            (root / "new").unlink()
            (root / "tracked").write_text("changed", encoding="utf-8")
            self.assertTrue(env.inspect_git(root)["dirty"])


if __name__ == "__main__":
    unittest.main()
