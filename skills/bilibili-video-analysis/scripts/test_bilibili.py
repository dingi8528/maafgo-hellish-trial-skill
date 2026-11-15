"""Offline behavior tests. No Bilibili/network requests and no original media edits."""
import argparse
import io
import json
import pathlib
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import bilibili as b


class JsonResponse(io.StringIO):
    def __init__(self, value):
        super().__init__(json.dumps(value))


class ByteResponse(io.BytesIO):
    def __init__(self, value):
        super().__init__(value)
        self.headers = {"Content-Length": str(len(value))}


class SlowHttpReadTests(unittest.TestCase):
    """Local HTTP proves slow packets return to the wall-clock deadline loop."""
    @classmethod
    def setUpClass(cls):
        cls.payload = bytes(range(251)) * 80
        payload = cls.payload

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload) if self.path == "/download" else 100000))
                self.end_headers()
                try:
                    if self.path == "/download":
                        for offset in range(0, len(payload), 311):
                            self.wfile.write(payload[offset:offset + 311])
                            self.wfile.flush()
                            time.sleep(.001)
                    elif self.path == "/gap":
                        self.wfile.write(b"ok")
                        self.wfile.flush()
                        time.sleep(.4)
                        self.wfile.write(b"late")
                        self.wfile.flush()
                    else:
                        for _ in range(300):
                            self.wfile.write(b"x")
                            self.wfile.flush()
                            time.sleep(.01)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(timeout=1)

    def test_slow_drip_sample_returns_before_body_finishes(self):
        started = time.monotonic()
        result = b.sample_source(self.base + "/drip", seconds=.18)
        self.assertLess(time.monotonic() - started, .8)
        self.assertGreater(result["bytes"], 0)
        self.assertLess(result["bytes"], 300)
        self.assertIn(result["sample_stop_reason"], ("time_limit", "read_timeout"))

    def test_sample_read_timeout_preserves_measurement_and_is_not_eof(self):
        started = time.monotonic()
        result = b.sample_source(self.base + "/gap", seconds=.12)
        self.assertLess(time.monotonic() - started, .7)
        self.assertEqual(result["bytes"], 2)
        self.assertEqual(result["sample_stop_reason"], "read_timeout")
        self.assertEqual(result["total_bytes"], 100000)

    def test_download_accumulates_short_reads_without_losing_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "download.partial"
            result = b.download(self.base + "/download", path, len(self.payload), allow_slow=True)
            self.assertEqual(path.read_bytes(), self.payload)
            self.assertEqual(result["bytes"], len(self.payload))
            self.assertTrue(result["size_match"])
            self.assertTrue(result["http_size_match"])


class CollectionTests(unittest.TestCase):
    def test_transfer_eta_is_unknown_without_total(self):
        status = b.transfer_status(1024, 2)
        self.assertEqual(status["bytes_per_second"], 512)
        self.assertIsNone(status["estimated_remaining_seconds"])
        self.assertEqual(b.transfer_status(1024, 2, 2048)["estimated_remaining_seconds"], 2)

    def test_probe_caps_bytes_even_when_server_ignores_range(self):
        response = ByteResponse(b"x" * (2 * 1024 * 1024))
        response.status = 200
        with patch.object(b.urllib.request, "urlopen", return_value=response) as opener:
            status = b.sample_source("https://cdn.example/video")
        self.assertEqual(status["bytes"], 1024 * 1024)
        self.assertEqual(status["total_bytes"], 2 * 1024 * 1024)
        self.assertEqual(opener.call_args.args[0].get_header("Range"), "bytes=0-1048575")

    def test_probe_uses_content_range_total_instead_of_sample_length(self):
        response = ByteResponse(b"abc")
        response.status = 206
        response.headers["Content-Range"] = "bytes 0-2/9000000"
        with patch.object(b.urllib.request, "urlopen", return_value=response):
            self.assertEqual(b.sample_source("https://cdn.example/video")["total_bytes"], 9000000)

    def test_slow_download_stops_once_with_partial_and_requires_opt_in(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "download.partial"
            with patch.object(b.urllib.request, "urlopen", return_value=ByteResponse(b"abc")) as opener, \
                    patch.object(b.time, "monotonic", side_effect=[0, 6]):
                with self.assertRaisesRegex(b.CollectionError, "allow-slow"):
                    b.download("https://cdn.example/video", path, 100000000)
                self.assertEqual(path.read_bytes(), b"abc")
                self.assertEqual(opener.call_count, 1)
            unknown = ByteResponse(b"abc")
            unknown.headers = {}
            with patch.object(b.urllib.request, "urlopen", return_value=unknown), \
                    patch.object(b.time, "monotonic", side_effect=[0, 6, 7]):
                self.assertEqual(b.download("https://cdn.example/video", path, allow_slow=True)["bytes"], 3)

    def test_inspect_reads_description_and_selected_part_without_ffmpeg(self):
        detail = {"bvid": "BV1Zjj561EJo", "title": "fixture", "desc": "guide notes", "pages": [
            {"page": 1, "cid": 11}, {"page": 2, "cid": 22}]}
        with tempfile.TemporaryDirectory() as folder:
            args = argparse.Namespace(out=folder, input="https://www.bilibili.com/video/BV1Zjj561EJo?p=2",
                                      page=None, quality=64)
            play = {"durl": [{"url": "https://cdn.example/video", "size": 1000}]}
            with patch.object(b.Client, "api", side_effect=[detail, play]) as api, \
                    patch.object(b, "sample_source", return_value={"slow": False}) as sample, \
                    patch.object(b, "ffmpeg_path", side_effect=AssertionError("not needed")):
                self.assertEqual(b.inspect_command(args), 0)
            estimate = json.loads((pathlib.Path(folder) / "download_estimate.json").read_text(encoding="utf-8"))
            self.assertEqual(estimate["description"], "guide notes")
            self.assertEqual(estimate["cid"], 22)
            self.assertEqual(api.call_args.args[1]["cid"], 22)
            sample.assert_called_once_with("https://cdn.example/video", 1000)

    def test_input_page_and_identifiers(self):
        self.assertEqual(b.parse_video("https://www.bilibili.com/video/BV1Zjj561EJo/?p=3"),
                         ({"bvid": "BV1Zjj561EJo"}, 3))
        self.assertEqual(b.parse_video("av123"), ({"aid": 123}, None))
        self.assertEqual(b.parse_space("https://space.bilibili.com/71923484/upload/video"), 71923484)
        with self.assertRaises(b.CollectionError):
            b.parse_video("https://example.com/video/BV1Zjj561EJo")

    def test_unknown_expected_byte_count_is_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "data"
            path.write_bytes(b"abc")
            self.assertIsNone(b.size_check(path)["size_match"])
            self.assertFalse(b.size_check(path, 4)["size_match"])
            self.assertTrue(b.size_check(path, 3)["size_match"])

    def test_byte_mismatch_gets_only_one_complete_redownload(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "video.partial"
            with patch.object(b.urllib.request, "urlopen", side_effect=[ByteResponse(b"abc"), ByteResponse(b"abcd")]) as opener, \
                    patch.object(b.time, "sleep"):
                self.assertTrue(b.download("https://cdn.example/video", path, 4)["size_match"])
                self.assertEqual(opener.call_count, 2)
            with patch.object(b.urllib.request, "urlopen", side_effect=[ByteResponse(b"a"), ByteResponse(b"a")]) as opener, \
                    patch.object(b.time, "sleep"):
                with self.assertRaises(b.CollectionError):
                    b.download("https://cdn.example/video", path, 4)
                self.assertEqual(opener.call_count, 2)
                self.assertTrue(path.exists())

    def test_api_access_error_is_saved_and_not_retried(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(b, "transport_open", return_value=JsonResponse({"code": -352, "message": "blocked"})) as opener:
                with self.assertRaises(b.CollectionError):
                    b.Client(folder).api("/test", {}, "error.json")
            self.assertEqual(opener.call_count, 1)
            self.assertEqual(json.loads((pathlib.Path(folder) / "error.json").read_text())["response"]["code"], -352)

    def test_http_restriction_is_not_retried(self):
        error = urllib.error.HTTPError("https://api.bilibili.com", 412, "blocked", {}, None)
        with patch.object(b.urllib.request, "urlopen", side_effect=error) as opener:
            with self.assertRaises(urllib.error.HTTPError):
                b.transport_open("https://api.bilibili.com")
        self.assertEqual(opener.call_count, 1)

    def test_comment_rpid_dedup_and_creator_and_child(self):
        child = {"rpid": 20, "member": {"mid": 9}, "content": {"message": "child"}, "like": 2}
        parent = {"rpid": 10, "member": {"mid": 8}, "content": {"message": "parent"}, "replies": [child]}
        rows = {}
        b.ingest_comments(rows, {"replies": [parent], "top": {"upper": parent}}, "BV1Zjj561EJo", 9, "hot", "now")
        b.ingest_comments(rows, {"replies": [parent]}, "BV1Zjj561EJo", 9, "time", "later")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows["10"]["sampled_from"], ["hot", "time"])
        self.assertEqual(rows["20"]["level"], "child")
        self.assertEqual(rows["20"]["parent_rpid"], "10")
        self.assertTrue(rows["20"]["is_creator"])
        self.assertFalse(rows["10"]["is_creator"])

    def test_url_part_selection_and_explicit_override(self):
        detail = {"bvid": "BV1Zjj561EJo", "aid": 1, "title": "fixture", "pages": [
            {"page": 1, "cid": 11}, {"page": 2, "cid": 22}]}
        with tempfile.TemporaryDirectory() as folder:
            args = argparse.Namespace(out=folder, ffmpeg="fake", input="https://www.bilibili.com/video/BV1Zjj561EJo?p=2",
                                      page=None, all_parts=False, skip_frames=True)
            with patch.object(b, "ffmpeg_path", return_value="fake"), patch.object(b.Client, "api", return_value=detail), \
                    patch.object(b, "download_part", return_value={"audio_status": "present"}) as download:
                self.assertEqual(b.video_command(args), 0)
                self.assertEqual(download.call_args.args[3]["page"], 2)
                args.page = 1
                self.assertEqual(b.video_command(args), 0)
                self.assertEqual(download.call_args.args[3]["page"], 1)

    def test_sampling_includes_tail_and_rejects_outside(self):
        samples = b.requested_times(9.8, 4)
        self.assertEqual(samples[:3], [0, 4, 8])
        self.assertGreater(samples[-1], 9.6)
        with self.assertRaises(b.CollectionError):
            b.requested_times(9.8, times=[9.8])

    def test_source_plan_multisegment_and_dash_audio_selection(self):
        route, sources = b.source_plan({"durl": [{"url": "https://cdn/1", "size": 1},
                                               {"url": "https://cdn/2", "size": 2}]}, 64)
        self.assertEqual(route, "durl")
        self.assertEqual([s["expected_bytes"] for s in sources], [1, 2])
        dash = {"dash": {"video": [{"id": 80, "baseUrl": "https://cdn/80", "bandwidth": 100},
                                    {"id": 64, "base_url": "https://cdn/64", "bandwidth": 90}],
                         "audio": [{"baseUrl": "https://cdn/quiet", "bandwidth": 10},
                                   {"baseUrl": "https://cdn/audio", "bandwidth": 20}]}}
        route, sources = b.source_plan(dash, 64)
        self.assertEqual(route, "dash")
        self.assertEqual([s["url"] for s in sources], ["https://cdn/64", "https://cdn/audio"])
        dash["dash"]["audio"] = []
        self.assertEqual([s["kind"] for s in b.source_plan(dash, 64)[1]], ["video"])

    def test_all_parts_and_same_output_namespace(self):
        details = [{"bvid": ident, "aid": 1, "title": "fixture", "pages": [
            {"page": 1, "cid": 11}, {"page": 2, "cid": 22}]} for ident in ("BV1Zjj561EJo", "BV1473A6mEET")]
        with tempfile.TemporaryDirectory() as folder:
            args = argparse.Namespace(out=folder, ffmpeg="fake", input="BV1Zjj561EJo",
                                      page=None, all_parts=True, skip_frames=True)
            def part(client, ff, detail, page, *rest):
                return {"page": page["page"], "audio_status": "present", "status": "verified"}
            with patch.object(b, "ffmpeg_path", return_value="fake"), patch.object(b.Client, "api", side_effect=details), \
                    patch.object(b, "download_part", side_effect=part) as download:
                self.assertEqual(b.video_command(args), 0)
                args.input = "BV1473A6mEET"
                self.assertEqual(b.video_command(args), 0)
                self.assertEqual(download.call_count, 4)
            for detail in details:
                manifest = json.loads((pathlib.Path(folder) / "03_数据" / detail["bvid"] / "video_manifest.json").read_text(encoding="utf-8"))
                self.assertEqual([p["page"] for p in manifest["parts"]], [1, 2])
                self.assertEqual(manifest["bvid"], detail["bvid"])

    def test_comment_outputs_have_video_namespace(self):
        identifiers = ("BV1Zjj561EJo", "BV1473A6mEET")
        responses = []
        for ident in identifiers:
            responses.extend([{"bvid": ident, "aid": 1, "owner": {"mid": 8}},
                {"replies": [{"rpid": 10, "member": {"mid": 9}, "content": {"message": ident}}], "cursor": {"is_end": True}}])
        with tempfile.TemporaryDirectory() as folder, patch.object(b.Client, "api", side_effect=responses):
            for ident in identifiers:
                args = argparse.Namespace(out=folder, input=ident, sort="hot", pages=1)
                self.assertEqual(b.comments_command(args), 0)
            for ident in identifiers:
                path = pathlib.Path(folder) / "03_数据" / ident / "评论" / "comments_all.json"
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))[0]["message"], ident)


class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ff = b.ffmpeg_path()
        cls.temp = tempfile.TemporaryDirectory()
        cls.folder = pathlib.Path(cls.temp.name)
        cls.video = cls.folder / "two_seconds.mp4"
        result = b.run_ffmpeg(cls.ff, ["-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=12",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "2", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(cls.video)])
        if result.returncode:
            raise RuntimeError(result.stderr)
        cls.video_only = cls.folder / "video_only.mp4"
        cls.audio_only = cls.folder / "audio_only.m4a"
        for output, flags in ((cls.video_only, ["-an"]), (cls.audio_only, ["-vn"])):
            result = b.run_ffmpeg(cls.ff, ["-v", "error", "-i", str(cls.video)] + flags + ["-c", "copy", str(output)])
            if result.returncode:
                raise RuntimeError(result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_legal_clip_verified_when_expected_duration_matches(self):
        result = b.validate_media(self.ff, self.video, 2, self.video.stat().st_size)
        self.assertTrue(result["verified"], result)
        self.assertEqual(result["video_streams"], 1)
        self.assertEqual(result["audio_streams"], 1)
        self.assertGreater(result["decoded_duration_seconds"], 1.8)

    def test_legal_short_clip_does_not_pass_expected_long_duration(self):
        result = b.validate_media(self.ff, self.video, 10)
        self.assertFalse(result["verified"])
        self.assertIn("decoded video duration differs from expected duration", result["errors"])

    def test_truncated_file_rejected_even_without_expected_bytes(self):
        broken = self.folder / "truncated.mp4"
        contents = self.video.read_bytes()
        broken.write_bytes(contents[:len(contents) // 2])
        result = b.validate_media(self.ff, broken, 2)
        self.assertFalse(result["verified"], result)
        self.assertIsNone(result["size_match"])

    def test_frame_extraction_and_explicit_timestamps(self):
        output = self.folder / "frames"
        path = b.extract_frames(self.ff, self.video, output, times=[0.1, 1.8], width=160)
        manifest = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        self.assertEqual([f["requested_seconds"] for f in manifest["frames"]], [0.1, 1.8])
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(len(manifest["contact_sheets"]), 1)

    def exercise_download(self, play, duration, tag):
        root = self.folder / tag
        data, videos = root / "data", root / "videos"
        data.mkdir(parents=True, exist_ok=True)
        videos.mkdir(parents=True, exist_ok=True)
        sources = {"https://fixture/full": self.video, "https://fixture/video": self.video_only,
                   "https://fixture/audio": self.audio_only}
        def copy_source(url, path, expected, allow_slow=False):
            shutil.copyfile(sources[url], path)
            return b.size_check(path, expected)
        client = b.Client(data)
        detail = {"bvid": "BV1Zjj561EJo"}
        page = {"page": 1, "cid": 1, "duration": duration}
        args = argparse.Namespace(quality=64)
        with patch.object(client, "api", return_value=play), patch.object(b, "download", side_effect=copy_source):
            result = b.download_part(client, self.ff, detail, page, args, data, videos)
        self.assertEqual(result["status"], "verified", result)
        self.assertTrue(pathlib.Path(result["video_path"]).exists())
        self.assertFalse(pathlib.Path(result["partial_path"]).exists())
        with patch.object(client, "api", return_value=play), patch.object(b, "download") as downloader:
            reused = b.download_part(client, self.ff, detail, page, args, data, videos)
        self.assertTrue(reused["reused_existing"])
        self.assertTrue(reused["validation"]["verified"])
        downloader.assert_not_called()
        return result

    def test_durl_multisegment_concat_and_revalidation(self):
        size = self.video.stat().st_size
        result = self.exercise_download({"quality": 64, "durl": [
            {"url": "https://fixture/full", "size": size},
            {"url": "https://fixture/full", "size": size}]}, 4, "multisegment")
        self.assertEqual(result["route"], "durl")
        self.assertIsNone(result["validation"]["size_match"])
        self.assertTrue(all(s["byte_validation"]["size_match"] for s in result["sources"]))

    def test_dash_with_and_without_audio_mux(self):
        for has_audio in (True, False):
            play = {"quality": 64, "dash": {"video": [{"id": 64, "baseUrl": "https://fixture/video"}],
                "audio": [{"baseUrl": "https://fixture/audio"}] if has_audio else []}}
            result = self.exercise_download(play, 2, "dash_" + str(has_audio))
            self.assertEqual(result["audio_status"], "present" if has_audio else "absent")

    def test_audio_cannot_hide_short_video(self):
        path = self.folder / "video2_audio10.mp4"
        result = b.run_ffmpeg(self.ff, ["-v", "error", "-i", str(self.video_only), "-f", "lavfi", "-i",
            "sine=frequency=440:sample_rate=44100", "-t", "10", "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", str(path)])
        self.assertEqual(result.returncode, 0, result.stderr)
        checked = b.validate_media(self.ff, path, 10)
        self.assertFalse(checked["verified"], checked)
        self.assertIn("decoded video duration differs from expected duration", checked["errors"])
        self.assertAlmostEqual(checked["container_duration_seconds"], 10, delta=0.1)
        video = next(r for r in checked["decoded_stream_coverage"] if r["kind"] == "video")
        audio = next(r for r in checked["decoded_stream_coverage"] if r["kind"] == "audio")
        self.assertAlmostEqual(video["coverage_duration_seconds"], 2, delta=0.1)
        self.assertAlmostEqual(audio["coverage_duration_seconds"], 10, delta=0.1)

    def test_video_cannot_hide_short_audio(self):
        path = self.folder / "video10_audio2.mp4"
        result = b.run_ffmpeg(self.ff, ["-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=12",
            "-i", str(self.audio_only), "-t", "10", "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264",
            "-c:a", "copy", str(path)])
        self.assertEqual(result.returncode, 0, result.stderr)
        checked = b.validate_media(self.ff, path, 10)
        self.assertFalse(checked["verified"], checked)
        self.assertIn("decoded audio duration differs from expected duration", checked["errors"])

    def test_vfr_uses_original_pts_instead_of_nominal_fps(self):
        path = self.folder / "vfr.mp4"
        result = b.run_ffmpeg(self.ff, ["-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=20:duration=2",
            "-vf", "select=not(mod(n\\,3))", "-fps_mode", "vfr", "-c:v", "libx264", str(path)])
        self.assertEqual(result.returncode, 0, result.stderr)
        checked = b.validate_media(self.ff, path)
        self.assertTrue(checked["verified"], checked)
        track = checked["decoded_stream_coverage"][0]
        self.assertTrue(track["duration_metadata_complete"])
        self.assertAlmostEqual(track["coverage_duration_seconds"], checked["container_duration_seconds"], delta=0.1)

    def test_verify_command_is_offline_and_scope_is_explicit(self):
        output = self.folder / "verify"
        with patch.object(b, "transport_open", side_effect=AssertionError("network forbidden")):
            code = b.main(["verify", str(self.video), "--out", str(output), "--ffmpeg", self.ff])
        self.assertEqual(code, 0)
        checked = json.loads((output / "validation.json").read_text(encoding="utf-8"))
        self.assertEqual(checked["verification_scope"], "local_container_decode_self_consistency")
        self.assertFalse(checked["remote_source_duration_provided"])
        code = b.main(["verify", str(self.video), "--out", str(output), "--expected-seconds", "10", "--ffmpeg", self.ff])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
