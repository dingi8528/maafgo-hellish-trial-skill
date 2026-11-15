"""Exercise the optional local display without a browser, ADB, or game input."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/maafgo-hellish-trial/scripts/trial_live.py"
SPEC = importlib.util.spec_from_file_location("trial_live", SCRIPT)
live = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(live)


class StateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.session = Path(temporary.name) / "session"
        live.init_session(self.session)

    def test_initial_state_does_not_invent_mission_total_or_completion(self):
        state = live.read_state(self.session)
        self.assertIsNone(state["missions"]["total"])
        self.assertIsNone(state["missions"]["baseline_count"])
        self.assertIsNone(state["missions"]["game_count"])
        self.assertEqual(state["missions"]["confirmed_count"], 0)
        self.assertNotIn("frame", state)
        self.assertNotIn("last_event_id", state)

    def test_observation_and_game_confirmation_are_distinct(self):
        live.publish_event(self.session, {"kind": "missions", "missions": [
            {"id": "a", "label": "当前任务", "status": "pending"}], "baseline_count": 0})
        live.publish_event(self.session, {"kind": "mission", "mission": {"id": "a", "status": "observed"}})
        state = live.read_state(self.session)
        self.assertEqual(state["missions"]["observed_count"], 1)
        self.assertEqual(state["missions"]["confirmed_count"], 0)
        with self.assertRaisesRegex(live.LiveError, "确认依据"):
            live.publish_event(self.session, {"kind": "mission", "mission": {"id": "a", "status": "confirmed"}})
        self.assertEqual(live.read_state(self.session)["missions"]["observed_count"], 1)
        live.publish_event(self.session, {"kind": "mission", "mission": {
            "id": "a", "status": "confirmed", "evidence": "结算后任务页已确认"}, "game_count": 1})
        state = live.read_state(self.session)
        self.assertEqual(state["missions"]["confirmed_count"], 1)
        self.assertEqual(state["missions"]["game_count"], 1)
        self.assertEqual(state["missions"]["baseline_count"], 0)

    def test_total_is_the_actual_list_length_not_fifteen(self):
        live.publish_event(self.session, {"kind": "missions", "missions": [
            {"id": str(i), "label": f"任务{i}"} for i in range(23)]})
        self.assertEqual(live.read_state(self.session)["missions"]["total"], 23)

    def test_invalid_events_leave_state_unchanged(self):
        before = (self.session / "state.json").read_bytes()
        events = [
            {"kind": "click"}, {"kind": []}, {"kind": "commentary", "detail": "x" * 4001},
            {"kind": "status", "status": "victory-guaranteed"},
            {"kind": "status", "status": []},
            {"kind": "missions", "missions": [{"id": "x", "label": "A", "status": []}]},
            {"kind": "missions", "missions": [{"id": "x", "label": "A"}, {"id": "x", "label": "B"}]},
            {"kind": "missions", "missions": [{"id": "x", "label": "A"}], "game_count": 2},
            {"kind": "mission", "mission": {"id": "missing", "status": "observed"}},
            {"kind": "status", "attempt": True},
        ]
        for event in events:
            with self.subTest(event=event["kind"]), self.assertRaises(live.LiveError):
                live.publish_event(self.session, event)
            self.assertEqual((self.session / "state.json").read_bytes(), before)

    def test_concurrent_writers_keep_unique_ids_and_bounded_history(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            records = list(pool.map(lambda i: live.publish_event(self.session,
                {"kind": "commentary", "title": str(i)}), range(65)))
        self.assertEqual(sorted(row["id"] for row in records), list(range(1, 66)))
        state = live.read_state(self.session)
        self.assertEqual(len(state["events"]), 40)
        self.assertEqual([row["id"] for row in state["events"]], list(range(26, 66)))
        self.assertFalse((self.session / ".state.lock").exists())

    def test_idempotent_initialization_does_not_reset_real_run(self):
        live.publish_event(self.session, {"kind": "status", "status": "running", "attempt": 3, "turn": "T7"})
        live.init_session(self.session, mode="demo")
        state = live.read_state(self.session)
        self.assertEqual(state["session"]["mode"], "live")
        self.assertEqual(state["session"]["turn"], "T7")
        with self.assertRaisesRegex(live.LiveError, "真实会话"):
            live.demo_session(self.session)

    def test_atomic_replace_retries_sharing_violation_without_partial_file(self):
        target = self.session / "example.json"
        target.write_bytes(b"old")
        original = live.os.replace
        count = 0

        def replace(source, destination):
            nonlocal count
            count += 1
            self.assertEqual(target.read_bytes(), b"old")
            if count == 1:
                raise PermissionError("sharing violation")
            return original(source, destination)

        with patch.object(live.os, "replace", side_effect=replace):
            live.atomic_write(target, b"complete-new-data")
        self.assertEqual(count, 2)
        self.assertEqual(target.read_bytes(), b"complete-new-data")
        self.assertFalse(list(self.session.glob("*.tmp")))

    def test_cli_help_works_without_third_party_modules(self):
        result = subprocess.run([sys.executable, "-I", "-S", str(SCRIPT), "--help"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("serve", result.stdout)

    def test_demo_needs_only_standard_library_and_does_not_capture_frames(self):
        folder = self.session.parent / "isolated-demo"
        result = subprocess.run([sys.executable, "-I", "-S", str(SCRIPT), "demo", "--session", str(folder)],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(result.stdout)
        self.assertEqual(state["session"]["mode"], "demo")
        self.assertEqual(state["missions"]["confirmed_count"], 0)
        self.assertNotIn("frame", state)
        self.assertFalse(list(folder.glob("*.jpg")))
        removed = subprocess.run([sys.executable, "-I", "-S", str(SCRIPT), "frame", "--session", str(folder)],
                                 capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(removed.returncode, 2)


class HttpTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.session, self.assets = self.root / "session", self.root / "assets"
        self.assets.mkdir()
        (self.assets / "index.html").write_text("<h1>Local display</h1>", encoding="utf-8")
        (self.assets / "bb-loop.webm").write_bytes(b"0123456789")
        (self.assets / ".secret").write_text("secret")
        (self.root / "private.txt").write_text("private")
        live.init_session(self.session)
        self.server = live.create_server(self.session, port=0, asset_root=self.assets)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(3)

    def get(self, path, *, headers=None, method="GET", body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_real_http_returns_current_state_and_no_cors(self):
        live.publish_event(self.session, {"kind": "commentary", "title": "前辈，等一下♡"})
        code, headers, raw = self.get("/api/state")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(raw)["commentary"]["title"], "前辈，等一下♡")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertIn("nosniff", headers["X-Content-Type-Options"])
        # The read-only page must still be allowed to request its own window
        # stream; it gets no camera or microphone permission from this server.
        self.assertIn("display-capture=(self)", headers["Permissions-Policy"])
        self.assertIn("camera=()", headers["Permissions-Policy"])
        self.assertIn("media-src 'self' blob:", headers["Content-Security-Policy"])

    def test_closed_keepalive_client_does_not_report_a_server_error(self):
        # Chrome can reset an idle HTTP/1.1 connection when its tab closes.
        # The reset happens in handle() reading the next request, outside GET.
        finished = threading.Event()
        original = self.server.process_request_thread
        reported = []

        def process(request, address):
            try:
                original(request, address)
            finally:
                finished.set()

        self.server.process_request_thread = process
        self.server.handle_error = lambda request, address: reported.append(sys.exc_info()[1])
        client = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=3)
        try:
            client.sendall((f"GET /api/state HTTP/1.1\r\nHost: 127.0.0.1:{self.server.server_port}"
                            "\r\nConnection: keep-alive\r\n\r\n").encode("ascii"))
            raw = b""
            while b"\r\n\r\n" not in raw:
                raw += client.recv(4096)
            header, body = raw.split(b"\r\n\r\n", 1)
            self.assertTrue(header.startswith(b"HTTP/1.1 200"))
            length = int(next(line.split(b":", 1)[1] for line in header.split(b"\r\n")
                              if line.lower().startswith(b"content-length:")))
            while len(body) < length:
                body += client.recv(4096)
            client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                              struct.pack("hh" if os.name == "nt" else "ii", 1, 0))
        finally:
            client.close()
        self.assertTrue(finished.wait(3), "reset client handler did not finish")
        self.assertEqual(reported, [], "an ordinary tab close reached the server error logger")

    def test_handler_programming_errors_are_not_silenced(self):
        reported = []
        self.server.handle_error = lambda request, address: reported.append(sys.exc_info()[1])
        with patch.object(self.server.RequestHandlerClass, "do_GET", side_effect=RuntimeError("intentional test failure")):
            with self.assertRaises(http.client.RemoteDisconnected):
                self.get("/api/state")
        self.assertEqual(len(reported), 1)
        self.assertIsInstance(reported[0], RuntimeError)

    def test_static_byte_ranges_and_conditional_requests(self):
        code, headers, raw = self.get("/bb-loop.webm", headers={"Range": "bytes=2-5"})
        self.assertEqual((code, raw), (206, b"2345"))
        self.assertEqual(headers["Content-Range"], "bytes 2-5/10")
        etag = headers["ETag"]
        code, _, raw = self.get("/bb-loop.webm", headers={"If-None-Match": etag})
        self.assertEqual((code, raw), (304, b""))
        self.assertEqual(self.get("/bb-loop.webm", headers={"Range": "bytes=-3"})[2], b"789")
        self.assertEqual(self.get("/bb-loop.webm", headers={"Range": "bytes=99-100"})[0], 416)
        self.assertEqual(self.get("/bb-loop.webm", headers={"Range": "bytes=0-1,3-4"})[0], 416)

    def test_traversal_session_and_hidden_files_are_not_served(self):
        for path in ("/../private.txt", "/%2e%2e/private.txt", "/.secret", "/state.json", "/binding.json", "/foo\\..\\private.txt"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path)[0], 404)

    def test_foreign_host_and_origin_are_rejected(self):
        for headers in ({"Host": "attacker.example"}, {"Origin": "https://attacker.example"},
                        {"Host": "127.0.0.1:9"}):
            self.assertEqual(self.get("/api/state", headers=headers)[0], 403)

    def test_webpage_has_no_write_or_control_endpoint(self):
        before = (self.session / "state.json").read_bytes()
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            self.assertEqual(self.get("/api/state", method=method, body=b"click")[0], 405)
        self.assertEqual((self.session / "state.json").read_bytes(), before)

    def test_frame_endpoint_is_absent_even_when_session_has_old_jpeg(self):
        (self.session / "frame.jpg").write_bytes(b"old-session-image")
        self.assertEqual(self.get("/frame.jpg")[0], 404)
        self.assertNotIn("frame", live.read_state(self.session))

    def test_corrupt_snapshot_is_reported_without_filesystem_trace(self):
        (self.session / "state.json").write_text("broken", encoding="utf-8")
        code, _, raw = self.get("/api/state")
        self.assertEqual(code, 503)
        self.assertNotIn(str(self.root), raw.decode("utf-8"))

if __name__ == "__main__":
    unittest.main()
