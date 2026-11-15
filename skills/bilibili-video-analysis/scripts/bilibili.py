#!/usr/bin/env python3
"""Download public Bilibili media, verify it, and preserve analysis evidence.

Network collection uses only Python's standard library. Frame sheets additionally
need Pillow; media operations need ffmpeg (PATH, explicit path, or imageio_ffmpeg).
"""
import argparse
import csv
import datetime as dt
import hashlib
import http.client
import json
import math
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.bilibili.com"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"}


class CollectionError(RuntimeError):
    pass


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def save(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def log(message):
    print(message, flush=True)


def transport_open(url, timeout=45):
    """One retry for transport errors/5xx only; never retry access restrictions."""
    for attempt in range(2):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt:
                raise
        except (urllib.error.URLError, TimeoutError, socket.timeout, http.client.IncompleteRead):
            if attempt:
                raise
        time.sleep(1)


class Client:
    def __init__(self, data_dir):
        self.data_dir = pathlib.Path(data_dir)
        self.last_call = 0

    def api(self, endpoint, params, name):
        wait = 1 - (time.monotonic() - self.last_call)
        if wait > 0:
            time.sleep(wait)
        url = API + endpoint + "?" + urllib.parse.urlencode(params)
        wrapper = {"captured_at": now(), "url": url, "parameters": params}
        try:
            with transport_open(url) as response:
                obj = json.load(response)
            wrapper["response"] = obj
            save(self.data_dir / name, wrapper)
            if obj.get("code") != 0:
                raise CollectionError(f"API {obj.get('code')}: {obj.get('message', '')}; stopped without bypass/retry")
            return obj.get("data") or {}
        except Exception as exc:
            if "response" not in wrapper:
                wrapper["error"] = str(exc)
                save(self.data_dir / name, wrapper)
            raise
        finally:
            self.last_call = time.monotonic()


def parse_video(value, opener=transport_open):
    value = value.strip()
    page = None
    if re.fullmatch(r"BV[0-9A-Za-z]{10}", value):
        return {"bvid": value}, page
    if re.fullmatch(r"av\d+", value, re.I):
        return {"aid": int(value[2:])}, page
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ("http", "https"):
        raise CollectionError("Use a BV ID, av ID, Bilibili video URL, or public b23.tv URL")
    if parsed.hostname in ("b23.tv", "www.b23.tv"):
        with opener(value) as response:
            value = response.geturl()
        parsed = urllib.parse.urlparse(value)
    if parsed.hostname not in ("bilibili.com", "www.bilibili.com", "m.bilibili.com"):
        raise CollectionError("The resolved link is not a Bilibili video page")
    match = re.search(r"/video/(BV[0-9A-Za-z]{10}|av\d+)(?:/|$)", parsed.path, re.I)
    if not match:
        raise CollectionError("Unsupported video URL (live, courses, and bangumi are not this command)")
    ident = match.group(1)
    if ident[:2].lower() == "bv":
        ident = "BV" + ident[2:]
    params, _ = parse_video(ident)
    p = urllib.parse.parse_qs(parsed.query).get("p")
    if p:
        try:
            page = int(p[0])
            if page < 1:
                raise ValueError()
        except ValueError:
            raise CollectionError("URL p must be a positive integer")
    return params, page


def parse_space(value):
    if value.isdigit() and int(value) > 0:
        return int(value)
    parsed = urllib.parse.urlparse(value)
    match = re.match(r"^/(\d+)(?:/|$)", parsed.path)
    if parsed.hostname == "space.bilibili.com" and match:
        return int(match.group(1))
    raise CollectionError("Use a numeric UID or https://space.bilibili.com/UID/upload/video")


def ffmpeg_path(explicit=None):
    result = explicit or shutil.which("ffmpeg")
    if not result:
        try:
            import imageio_ffmpeg
            result = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            raise CollectionError("ffmpeg unavailable: supply --ffmpeg PATH or install/use a configured runtime")
    if not pathlib.Path(result).is_file():
        raise CollectionError(f"ffmpeg does not exist: {result}")
    return str(result)


def run_ffmpeg(ff, args, timeout=600):
    return subprocess.run([ff, "-hide_banner", "-nostdin"] + args, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)


def parse_duration(text):
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    return int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3]) if match else None


def probe(ff, path):
    result = run_ffmpeg(ff, ["-i", str(path)], timeout=45)
    streams = re.findall(r"Stream #\d+:\d+[^\n]*: (Video|Audio):", result.stderr)
    fps = re.search(r",\s*(\d+(?:\.\d+)?) fps", result.stderr)
    return {"container_duration_seconds": parse_duration(result.stderr),
            "video_streams": streams.count("Video"), "audio_streams": streams.count("Audio"),
            "nominal_frame_rate": float(fps[1]) if fps else None,
            "probe_log": result.stderr}


def size_check(path, expected=None):
    actual = pathlib.Path(path).stat().st_size
    return {"bytes": actual, "expected_bytes": expected,
            "size_match": actual == expected if expected is not None else None}


def decode_track(ff, path, timeout, selector):
    """Decode one track so concurrent filter log fragments cannot interleave."""
    streams, diagnostic = {}, []
    command = [ff, "-hide_banner", "-nostdin", "-nostats", "-v", "info",
        "-xerror", "-err_detect", "explode", "-i", str(path), "-map", selector,
        "-vf", "showinfo,setpts=N/FRAME_RATE/TB", "-af", "ashowinfo,asetpts=N/SR/TB",
        "-progress", "pipe:1", "-f", "null", "-"]
    diagnostic.append("FFMPEG_COMMAND_JSON: " + json.dumps(command, ensure_ascii=False) + "\n")
    with tempfile.TemporaryDirectory(prefix="bili_decode_") as scratch:
        raw = pathlib.Path(scratch) / "decode.log"
        with raw.open("w", encoding="utf-8") as handle:
            process = subprocess.run(command, stdout=subprocess.PIPE, stderr=handle,
                text=True, encoding="utf-8", errors="replace", timeout=timeout)
        with raw.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                match = re.search(r"\[Parsed_(a?showinfo)_\d+ @ ([^\]]+)\].*\bn:\s*(\d+).*?pts_time:([^\s]+)", line)
                if not match:
                    if "Parsed_showinfo" not in line and "Parsed_ashowinfo" not in line:
                        diagnostic.append(line)
                    continue
                kind = "audio" if match[1] == "ashowinfo" else "video"
                key = (kind, match[2])
                row = streams.setdefault(key, {"kind": kind, "decoded_frames": 0, "first_pts_seconds": None,
                    "last_pts_seconds": None, "last_end_seconds": None, "sum_frame_duration_seconds": 0,
                    "duration_metadata_complete": True, "decoded_audio_samples": 0})
                row["decoded_frames"] += 1
                try:
                    pts = float(match[4])
                except ValueError:
                    row["duration_metadata_complete"] = False
                    continue
                if kind == "video":
                    duration_match = re.search(r"\bduration_time:([^\s]+)", line)
                    try:
                        frame_duration = float(duration_match[1]) if duration_match else None
                    except ValueError:
                        frame_duration = None
                else:
                    rate = re.search(r"\brate:(\d+)", line)
                    samples = re.search(r"\bnb_samples:(\d+)", line)
                    frame_duration = int(samples[1]) / int(rate[1]) if samples and rate and int(rate[1]) else None
                    row["decoded_audio_samples"] += int(samples[1]) if samples else 0
                row["first_pts_seconds"] = pts if row["first_pts_seconds"] is None else min(pts, row["first_pts_seconds"])
                row["last_pts_seconds"] = pts if row["last_pts_seconds"] is None else max(pts, row["last_pts_seconds"])
                if frame_duration is None or not math.isfinite(frame_duration) or frame_duration <= 0:
                    row["duration_metadata_complete"] = False
                    end = pts
                else:
                    row["sum_frame_duration_seconds"] += frame_duration
                    end = pts + frame_duration
                row["last_end_seconds"] = end if row["last_end_seconds"] is None else max(end, row["last_end_seconds"])
    records = []
    for row in streams.values():
        first, end = row["first_pts_seconds"], row["last_end_seconds"]
        row["pts_span_seconds"] = end - first if first is not None and end is not None else None
        row["coverage_duration_seconds"] = row["pts_span_seconds"] if row["duration_metadata_complete"] else None
        row["stream_selector"] = selector
        records.append(row)
    return process, records, "".join(diagnostic)


def decode_coverage(ff, path, timeout, info):
    records, diagnostics, progress, codes = [], [], [], []
    for kind, letter in (("video", "v"), ("audio", "a")):
        for index in range(info[kind + "_streams"]):
            process, rows, diagnostic = decode_track(ff, path, timeout, f"0:{letter}:{index}")
            codes.append(process.returncode)
            records.extend(rows)
            diagnostics.append(diagnostic)
            progress.append(process.stdout)
    process = subprocess.CompletedProcess([], 1 if any(codes) else 0, "\n".join(progress))
    return process, records, "\n".join(diagnostics)


def validate_media(ff, path, expected_seconds=None, expected_bytes=None, log_path=None):
    """Decode every audio/video stream; compare declared and decoded duration.

    Timestamp filters prevent legal duplicate DTS from failing a null output mux.
    Decoding itself uses -xerror and explode, so corrupt packets still fail.
    """
    result = size_check(path, expected_bytes)
    info = probe(ff, path)
    result.update({k: v for k, v in info.items() if k != "probe_log"})
    duration = expected_seconds if expected_seconds is not None else info["container_duration_seconds"]
    result["expected_duration_seconds"] = duration
    decode, coverage, diagnostic = decode_coverage(ff, path, max(90, (duration or 300) * 5 + 60), info)
    times = re.findall(r"^out_time_us=(\d+)$", decode.stdout, re.M)
    decoded = max(map(int, times), default=0) / 1_000_000
    frames = re.findall(r"^frame=(\d+)$", decode.stdout, re.M)
    tolerance = max(1.25, (duration or 0) * 0.01)
    errors = []
    if result["bytes"] <= 0 or result["size_match"] is False:
        errors.append("byte count mismatch or empty file")
    if not info["video_streams"] or max(map(int, frames), default=0) <= 0:
        errors.append("no decoded video frames")
    if decode.returncode:
        errors.append("strict full audio/video decode failed")
    if duration is None:
        errors.append("duration unavailable; completeness cannot be verified")
    elif decoded <= 0:
        errors.append("no output from full decode")
    for kind in ("video", "audio"):
        tracks = [row for row in coverage if row["kind"] == kind]
        if len(tracks) < info[kind + "_streams"]:
            errors.append(f"not every {kind} stream produced decoded coverage")
        for track in tracks:
            actual = track["coverage_duration_seconds"]
            if actual is None:
                errors.append(f"{kind} coverage duration unknown; no inference from nominal frame rate")
            elif duration is not None and (abs(actual - duration) > tolerance or
                    (kind == "audio" and abs(track["sum_frame_duration_seconds"] - duration) > tolerance)):
                errors.append(f"decoded {kind} duration differs from expected duration")
    if duration is not None and (info["container_duration_seconds"] is None or
                                abs(info["container_duration_seconds"] - duration) > tolerance):
        errors.append("container duration differs from expected duration")
    result.update({"decoded_duration_seconds": decoded, "decode_returncode": decode.returncode,
                   "duration_tolerance_seconds": tolerance, "verified": not errors, "errors": errors,
                   "decoded_stream_coverage": coverage,
                   "verification_scope": "expected_duration_and_full_decode" if expected_seconds is not None else "local_container_decode_self_consistency",
                   "remote_source_duration_provided": expected_seconds is not None,
                   "progress_duration_note": "Output progress is the longest timestamp-normalized stream, not proof of each source track's duration.",
                   "timestamp_normalization": "video N/FRAME_RATE/TB; audio N/SR/TB"})
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    result["sha256"] = digest.hexdigest()
    if log_path:
        pathlib.Path(log_path).write_text(info["probe_log"] + "\nDECODE DIAGNOSTICS\n" + diagnostic +
                                        "\nPROGRESS\n" + decode.stdout, encoding="utf-8")
    return result


def transfer_status(received, elapsed, total=None):
    rate = received / max(elapsed, 0.001)
    remaining = max(0, total - received) / rate if total is not None and rate else None
    return {"bytes": received, "elapsed_seconds": round(elapsed, 3),
            "bytes_per_second": round(rate, 1), "total_bytes": total,
            "estimated_remaining_seconds": round(remaining, 1) if remaining is not None else None}


def slow_transfer(status):
    """An estimate is a prompt to choose, not authorization to silently wait."""
    remaining = status["estimated_remaining_seconds"]
    return status["bytes_per_second"] < 128 * 1024 and (remaining is None or remaining > 300)


def read_available(response, limit, timeout):
    """One buffered/network read, never read(n)'s repeated slow-drip receives."""
    reader = getattr(response, "read1", None)
    if not callable(reader):
        raise CollectionError("当前HTTP响应不支持短分块读取，无法可靠测量下载速度。请检查Python环境，或使用已有本地视频。")
    # urllib's HTTPResponse keeps the socket below BufferedReader/SocketIO.
    # Memory-backed test responses need no timeout. A response whose fp is None
    # has already reached EOF, so read1 below safely returns an empty block.
    raw = getattr(getattr(response, "fp", None), "raw", None)
    transport = getattr(raw, "_sock", None)
    if transport is not None:
        transport.settimeout(max(0.001, timeout))
    return reader(limit)


def sample_source(url, expected=None, seconds=8, byte_limit=1024 * 1024):
    """Read at most 1 MiB, including when a CDN ignores Range; do not save media."""
    started = time.monotonic()
    headers = dict(HEADERS, Range=f"bytes=0-{byte_limit - 1}")
    received, stop_reason = 0, "time_limit"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=min(5, seconds)) as response:
        size = expected
        content_range = response.headers.get("Content-Range", "")
        if size is None and re.search(r"/(\d+)$", content_range):
            size = int(content_range.rsplit("/", 1)[1])
        if size is None and getattr(response, "status", None) == 200:
            length = response.headers.get("Content-Length", "")
            size = int(length) if length.isdigit() else None
        while received < byte_limit:
            remaining = seconds - (time.monotonic() - started)
            if remaining <= 0:
                break
            try:
                block = read_available(response, min(64 * 1024, byte_limit - received), min(2, remaining))
            except (socket.timeout, TimeoutError):
                stop_reason = "read_timeout"
                break
            if not block:
                stop_reason = "source_eof"
                break
            received += len(block)
        if received >= byte_limit:
            stop_reason = "byte_limit"
    if not received:
        raise CollectionError("下载采样未取得媒体字节，暂时无法测算速率。请核对网络响应，或让用户提供已有本地视频。")
    status = transfer_status(received, time.monotonic() - started, size)
    status.update(sample_only=True, slow=slow_transfer(status),
                  sample_stop_reason=stop_reason,
                  estimate_note="Small measured sample; CDN/network speed can change. Decode time is extra.")
    return status


def inspect_command(args):
    root = pathlib.Path(args.out).resolve()
    client = Client(root / "03_数据")
    params, url_page = parse_video(args.input)
    detail = client.api("/x/web-interface/view", params, "inspect_view.json")
    page_number = args.page if args.page is not None else url_page or 1
    page = next((p for p in detail.get("pages", []) if p["page"] == page_number), None)
    if page is None:
        raise CollectionError(f"P{page_number} is unavailable")
    play = client.api("/x/player/playurl", {"bvid": detail["bvid"], "cid": page["cid"],
        "qn": args.quality, "platform": "html5", "fnval": 0}, "inspect_playurl.json")
    route, sources = source_plan(play, args.quality)
    samples = []
    for source in sources:
        samples.append({"kind": source["kind"],
                        **sample_source(source["url"], source["expected_bytes"])})
    result = {"bvid": detail["bvid"], "title": detail["title"], "description": detail.get("desc"),
              "owner": detail.get("owner"), "pages": detail.get("pages"), "page": page_number,
              "cid": page["cid"], "route": route, "samples": samples,
              "slow": any(item["slow"] for item in samples)}
    path = root / "download_estimate.json"
    save(path, result)
    log(json.dumps(result, ensure_ascii=False, indent=2))
    log(f"Estimate: {path}")
    return 0


def download(url, path, expected=None, allow_slow=False):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise CollectionError("Unsupported public media URL scheme")
    # Entire transfers can fail during read; restart once, never append an untrusted partial.
    for attempt in range(2):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=5) as response:
                length = response.headers.get("Content-Length")
                total = int(expected) if expected is not None else int(length) if length and length.isdigit() else None
                started, reported, received = time.monotonic(), 0, 0
                with path.open("wb") as target:
                    while True:
                        block = read_available(response, 64 * 1024, 5)
                        if not block:
                            break
                        target.write(block)
                        received += len(block)
                        elapsed = time.monotonic() - started
                        if elapsed - reported >= 5:
                            status = transfer_status(received, elapsed, total)
                            log("TRANSFER " + json.dumps(status, ensure_ascii=False))
                            reported = elapsed
                            if not allow_slow and slow_transfer(status):
                                raise CollectionError("下载速度较慢，已保留未完成文件并停止。请向用户说明实测速率与预计剩余时间，让其选择继续（添加 --allow-slow）、提供本地视频，或自行调整网络后重试。")
            check = size_check(path, int(expected) if expected is not None else None)
            check["transfer"] = transfer_status(received, time.monotonic() - started, total)
            check["http_content_length"] = int(length) if length and length.isdigit() else None
            check["http_size_match"] = check["bytes"] == check["http_content_length"] if check["http_content_length"] is not None else None
            if check["size_match"] is False or check["http_size_match"] is False or not check["bytes"]:
                if not attempt:
                    time.sleep(1)
                    continue
                raise CollectionError("Downloaded media byte count mismatch")
            return check
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt:
                raise
        except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError, http.client.IncompleteRead):
            if attempt:
                raise
        time.sleep(1)


def source_plan(data, requested_quality):
    if data.get("durl"):
        return "durl", [{"url": item["url"], "expected_bytes": item.get("size"),
                         "length_ms": item.get("length"), "kind": "segment"} for item in data["durl"]]
    dash = data.get("dash") or {}
    videos = dash.get("video") or []
    if not videos:
        raise CollectionError("No publicly downloadable durl/DASH video returned")
    below = [v for v in videos if v.get("id", 0) <= requested_quality] or videos
    chosen = max(below, key=lambda v: (v.get("id", 0), v.get("bandwidth", 0)))
    audio = max(dash.get("audio") or [], key=lambda a: a.get("bandwidth", 0), default=None)
    sources = [{"url": chosen.get("baseUrl") or chosen.get("base_url"),
                "expected_bytes": chosen.get("size"), "kind": "video", "quality": chosen.get("id")}]
    if audio:
        sources.append({"url": audio.get("baseUrl") or audio.get("base_url"),
                        "expected_bytes": audio.get("size"), "kind": "audio"})
    return "dash", sources


def download_part(client, ff, detail, page, args, data_dir, video_dir):
    bvid, cid = detail["bvid"], page["cid"]
    label = f"{bvid}_P{page['page']:02d}"
    record = {"page": page["page"], "cid": cid, "part_title": page.get("part"),
              "expected_duration_seconds": page.get("duration"), "status": "pending"}
    progress_path = data_dir / f"{label}_part.json"
    save(progress_path, record)
    for refresh in range(2):
        play = client.api("/x/player/playurl", {"bvid": bvid, "cid": cid, "qn": args.quality,
                          "platform": "html5", "fnval": 0}, f"{label}_playurl_{refresh}.json")
        route, sources = source_plan(play, args.quality)
        quality = play.get("quality") or next((s.get("quality") for s in sources if s.get("quality")), None)
        final = video_dir / f"{label}_q{quality or 'public'}.mp4"
        partial = pathlib.Path(str(final) + ".partial")
        expected_bytes = sources[0]["expected_bytes"] if route == "durl" and len(sources) == 1 else None
        expected_duration = page.get("duration") or (play.get("timelength", 0) / 1000) or None
        record.update({"quality_requested": args.quality, "quality_returned": quality,
                       "route": route, "sources": sources, "video_path": str(final),
                       "audio_status": "not yet verified", "media_url_refreshes": refresh})
        record["partial_path"] = str(partial)
        save(progress_path, record)
        if final.exists():
            checked = validate_media(ff, final, expected_duration, expected_bytes, data_dir / f"{label}_decode.txt")
            record["validation"] = checked
            save(progress_path, record)
            if checked["verified"]:
                record.update(status="verified", reused_existing=True,
                              audio_status="present" if checked["audio_streams"] else "absent")
                record["sha256"] = checked["sha256"]
                save(progress_path, record)
                return record
            raise CollectionError(f"Existing file failed revalidation: {final}; kept without overwriting")
        try:
            source_paths = []
            for n, source in enumerate(sources, 1):
                path = partial if route == "durl" and len(sources) == 1 else data_dir / f"{label}_source_{n:02d}.media.partial"
                source["byte_validation"] = download(source["url"], path, source["expected_bytes"],
                    allow_slow=getattr(args, "allow_slow", False))
                source["local_path"] = str(path)
                source_paths.append(path)
                save(progress_path, record)
            if len(source_paths) > 1 or route == "dash":
                if route == "durl":
                    concat = data_dir / f"{label}_concat.txt"
                    # Relative basenames avoid drive letters and quote injection in ffconcat paths.
                    concat.write_text("".join(f"file '{p.name}'\n" for p in source_paths), encoding="utf-8")
                    merge_args = ["-f", "concat", "-safe", "0", "-i", str(concat)]
                else:
                    merge_args = [arg for p in source_paths for arg in ("-i", str(p))]
                    merge_args += ["-map", "0:v:0"]
                    if len(source_paths) > 1:
                        merge_args += ["-map", "1:a:0"]
                merged = run_ffmpeg(ff, ["-v", "error", "-y"] + merge_args +
                                    ["-c", "copy", "-movflags", "+faststart", "-f", "mp4", str(partial)])
                (data_dir / f"{label}_merge.txt").write_text(merged.stderr, encoding="utf-8")
                if merged.returncode:
                    raise CollectionError("Media concat/mux failed; sources and partial kept")
            checked = validate_media(ff, partial, expected_duration, expected_bytes, data_dir / f"{label}_decode.txt")
            record["validation"] = checked
            save(progress_path, record)
            if not checked["verified"]:
                raise CollectionError("Full media validation failed: " + "; ".join(checked["errors"]))
            partial.replace(final)
            for source, path in zip(sources, source_paths):
                if path != partial:
                    preserved = path.with_suffix("")
                    path.replace(preserved)
                    source["local_path"] = str(preserved)
                else:
                    source["local_path"] = str(final)
            record.update(status="verified", reused_existing=False,
                          audio_status="present" if checked["audio_streams"] else "absent")
            record["sha256"] = checked["sha256"]
            save(progress_path, record)
            return record
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 410) and refresh == 0:
                log(f"{label}: media URL rejected; fetching playurl once more")
                continue
            raise
    raise CollectionError("Media URL refresh exhausted")


def requested_times(duration, interval=4, times=None, tail_margin=0.12):
    if times is not None:
        values = sorted(set(float(t) for t in times))
        if not values or any(not math.isfinite(t) or t < 0 or t >= duration for t in values):
            raise CollectionError("Each requested frame time must be >= 0 and < container duration")
        return values
    tail = round(max(0, duration - min(tail_margin, duration / 2)), 6)
    values = [round(i * interval, 6) for i in range(math.ceil(duration / interval)) if i * interval <= tail]
    if not values or tail - values[-1] > 0.05:
        values.append(tail)
    return values


def extract_frames(ff, video, output, interval=4, times=None, width=640):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise CollectionError("Frame contact sheets need Pillow; use a configured runtime or --skip-frames")
    output = pathlib.Path(output)
    output.mkdir(parents=True, exist_ok=True)
    info = probe(ff, video)
    duration = info["container_duration_seconds"]
    if not duration or not info["video_streams"]:
        raise CollectionError("Cannot extract frames: video duration/stream unavailable")
    frames = []
    manifest = {"video_path": str(pathlib.Path(video).resolve()), "captured_at": now(),
                "container_duration_seconds": duration, "interval_seconds": None if times else interval,
                "timestamp_note": "Requested seek seconds; frame presentation is approximate within source frame/timebase precision.",
                "frames": frames, "contact_sheets": [], "status": "in_progress"}
    index = output / "frames_manifest.json"
    save(index, manifest)
    fps = info.get("nominal_frame_rate")
    tail_margin = max(0.12, 1 / fps + 0.02) if fps and fps > 0 else 0.5
    for n, seconds in enumerate(requested_times(duration, interval, times, tail_margin), 1):
        path = output / f"frame_{n:04d}_{seconds:09.3f}s.jpg"
        result = run_ffmpeg(ff, ["-v", "error", "-y", "-ss", str(seconds), "-i", str(video),
                                "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "2", str(path)], timeout=90)
        if result.returncode or not path.exists() or not path.stat().st_size:
            manifest.update(status="failed", error=f"Frame seek failed at {seconds}s: {result.stderr}")
            save(index, manifest)
            raise CollectionError(manifest["error"])
        frames.append({"path": str(path.resolve()), "requested_seconds": seconds,
                       "timestamp_is_approximate": True})
    font_path = pathlib.Path("C:/Windows/Fonts/msyh.ttc")
    font = ImageFont.truetype(str(font_path), 18) if font_path.exists() else ImageFont.load_default()
    for start in range(0, len(frames), 12):
        batch = frames[start:start + 12]
        images = []
        for row in batch:
            with Image.open(row["path"]) as source:
                images.append(source.convert("RGB"))
        height = max(im.height for im in images)
        sheet = Image.new("RGB", (width * 3, math.ceil(len(images) / 3) * (height + 30)), "#eeeeee")
        draw = ImageDraw.Draw(sheet)
        for n, (im, row) in enumerate(zip(images, batch)):
            x, y = (n % 3) * width, (n // 3) * (height + 30)
            sheet.paste(im, (x, y))
            draw.text((x + 6, y + height + 3), f"seek {row['requested_seconds']:.3f}s (approx)", font=font, fill="black")
        path = output / f"contact_{start // 12 + 1:03d}.jpg"
        sheet.save(path, quality=92)
        manifest["contact_sheets"].append(str(path.resolve()))
    manifest["status"] = "complete"
    save(index, manifest)
    log(f"Frames: {len(frames)}; sheets: {len(manifest['contact_sheets'])}")
    return str(index.resolve())


def video_command(args):
    root = pathlib.Path(args.out).resolve()
    base_data, video_dir = root / "03_数据", root / "01_视频"
    video_dir.mkdir(parents=True, exist_ok=True)
    client, ff = Client(base_data), ffmpeg_path(args.ffmpeg)
    params, url_page = parse_video(args.input)
    input_id = params.get("bvid") or f"av{params['aid']}"
    raw_name = f"_input_{input_id}_view.json"
    detail = client.api("/x/web-interface/view", params, raw_name)
    data_dir = base_data / detail["bvid"]
    data_dir.mkdir(parents=True, exist_ok=True)
    if (base_data / raw_name).exists():
        (base_data / raw_name).replace(data_dir / "video_view.json")
    client.data_dir = data_dir
    pages = detail.get("pages") or []
    chosen_page = args.page if args.page is not None else url_page or 1
    selected = pages if args.all_parts else [p for p in pages if p["page"] == chosen_page]
    if not selected:
        raise CollectionError(f"P{chosen_page} is unavailable")
    manifest = {"captured_at": now(), "input": args.input, "bvid": detail["bvid"],
                "aid": detail["aid"], "title": detail["title"], "description": detail.get("desc"),
                "owner": detail.get("owner"), "statistics": detail.get("stat"), "pages": pages,
                "source_url": "https://www.bilibili.com/video/" + detail["bvid"],
                "selection": "all_parts" if args.all_parts else f"P{chosen_page}", "parts": [],
                "analysis_note": "Downloaded media and sampled frames are evidence; audio has not been listened to/transcribed by this script."}
    failures = 0
    for page in selected:
        log(f"Downloading {detail['bvid']} P{page['page']}")
        try:
            item = download_part(client, ff, detail, page, args, data_dir, video_dir)
            if not args.skip_frames:
                try:
                    item["frames_manifest"] = extract_frames(ff, item["video_path"], root / "02_抽帧" /
                        f"{detail['bvid']}_P{page['page']:02d}", args.interval, width=args.width)
                except Exception as exc:
                    item["frames_error"] = str(exc)
                    failures += 1
            manifest["parts"].append(item)
            log(f"P{page['page']}: verified, audio {item['audio_status']}")
        except Exception as exc:
            failures += 1
            progress_path = data_dir / f"{detail['bvid']}_P{page['page']:02d}_part.json"
            failed = json.loads(progress_path.read_text(encoding="utf-8")) if progress_path.exists() else {"page": page["page"], "cid": page["cid"]}
            failed.update(status="failed", error=str(exc))
            save(progress_path, failed)
            manifest["parts"].append(failed)
            log(f"P{page['page']}: failed: {exc}")
        manifest["status"] = "partial_or_failed" if failures else "complete"
        save(data_dir / "video_manifest.json", manifest)
    return 1 if failures else 0


def space_command(args):
    data_dir = pathlib.Path(args.out).resolve() / "03_数据"
    client, uid = Client(data_dir), parse_space(args.input)
    manifest = {"captured_at": now(), "uid": uid, "order": args.order, "requested_limit": args.limit,
                "api_total": None, "actual_fetched_count": 0, "videos": [], "pages": [], "status": "in_progress"}
    status = 0
    ps = min(50, args.limit)
    try:
        for pn in range(1, math.ceil(args.limit / ps) + 1):
            data = client.api("/x/space/arc/search", {"mid": uid, "ps": ps, "pn": pn, "order": args.order}, f"space_page_{pn:03d}.json")
            page = data.get("page") or {}
            batch = (data.get("list") or {}).get("vlist") or []
            manifest["api_total"] = page.get("count", manifest["api_total"])
            manifest["actual_fetched_count"] += len(batch)
            manifest["pages"].append({"pn": pn, "ps": ps, "returned": len(batch), "page": page})
            for row in batch:
                if row.get("bvid") not in {v.get("bvid") for v in manifest["videos"]}:
                    manifest["videos"].append(row)
            log(f"Space page {pn}: {len(batch)} videos; fetched {manifest['actual_fetched_count']}")
            save(data_dir / "space_manifest.json", manifest)
            if len(manifest["videos"]) >= args.limit or not batch or len(batch) < ps:
                break
        manifest["videos"] = manifest["videos"][:args.limit]
        manifest["status"] = "complete_requested_sample"
    except Exception as exc:
        manifest.update(status="partial_or_failed", error=str(exc))
        status = 1
    manifest["selected_count"] = len(manifest["videos"])
    manifest["catalog_complete"] = manifest["api_total"] is not None and len(manifest["videos"]) >= manifest["api_total"]
    manifest["scope_note"] = "API-sorted returned snapshot; inaccessible pages are not a complete catalog or independently verified global ranking."
    save(data_dir / "space_manifest.json", manifest)
    return status


def ingest_comments(rows, data, bvid, creator_id, tag, captured_at):
    entries = list(data.get("replies") or [])
    top = data.get("top") or {}
    if isinstance(top, dict):
        entries.extend(v for v in top.values() if isinstance(v, dict) and v.get("rpid"))
    for key in ("top_replies", "hots"):
        entries.extend(data.get(key) or [])
    def add(reply, parent=None):
        rpid = str(reply["rpid"])
        if rpid not in rows:
            member = reply.get("member") or {}
            rows[rpid] = {"rpid": rpid, "bvid": bvid, "parent_rpid": str(parent) if parent else None,
                "level": "child" if parent else "main", "is_creator": str(member.get("mid")) == str(creator_id),
                "message": (reply.get("content") or {}).get("message", ""), "likes": reply.get("like", 0),
                "ctime": reply.get("ctime"), "captured_at": captured_at, "sampled_from": [],
                "source_url": f"https://www.bilibili.com/video/{bvid}#reply{rpid}"}
        if tag not in rows[rpid]["sampled_from"]:
            rows[rpid]["sampled_from"].append(tag)
        for child in reply.get("replies") or []:
            add(child, reply["rpid"])
    for reply in entries:
        add(reply)


def comments_command(args):
    base_data = pathlib.Path(args.out).resolve() / "03_数据"
    client = Client(base_data)
    params, _ = parse_video(args.input)
    input_id = params.get("bvid") or f"av{params['aid']}"
    raw_name = f"_input_{input_id}_comments_view.json"
    detail = client.api("/x/web-interface/view", params, raw_name)
    data_dir = base_data / detail["bvid"] / "评论"
    data_dir.mkdir(parents=True, exist_ok=True)
    if (base_data / raw_name).exists():
        (base_data / raw_name).replace(data_dir / "video_view.json")
    client.data_dir = data_dir
    rows, samples, failures = {}, [], []
    modes = [("hot", 3), ("time", 2)] if args.sort == "both" else [(args.sort, 3 if args.sort == "hot" else 2)]
    for sort, mode in modes:
        cursor, seen = 0, set()
        for page in range(1, args.pages + 1):
            tag = f"{sort}_{page:03d}"
            try:
                data = client.api("/x/v2/reply/main", {"type": 1, "oid": detail["aid"], "next": cursor, "mode": mode}, tag + ".json")
                captured = now()
                ingest_comments(rows, data, detail["bvid"], detail["owner"]["mid"], tag, captured)
                returned = data.get("cursor") or {}
                samples.append({"tag": tag, "mode": mode, "input_cursor": cursor, "returned_cursor": returned,
                                "returned_main_count": len(data.get("replies") or []), "captured_at": captured})
                log(f"Comments {tag}: {len(data.get('replies') or [])} main rows; {len(rows)} unique records")
                next_cursor = returned.get("next")
                if returned.get("is_end") or next_cursor is None or next_cursor == cursor or next_cursor in seen:
                    break
                seen.add(cursor)
                cursor = next_cursor
            except Exception as exc:
                failures.append({"tag": tag, "error": str(exc)})
                break
        if failures:
            break  # No other sort to probe after an access/API error.
    values = list(rows.values())
    audience = [r for r in values if not r["is_creator"]]
    creator = [r for r in values if r["is_creator"]]
    save(data_dir / "comments_all.json", values)
    save(data_dir / "comments_audience_main.json", [r for r in audience if r["level"] == "main"])
    save(data_dir / "comments_audience_children.json", [r for r in audience if r["level"] == "child"])
    save(data_dir / "comments_creator.json", creator)
    with (data_dir / "comments_all.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["rpid", "bvid", "parent_rpid", "level", "is_creator", "likes", "ctime", "message", "sampled_from", "source_url", "captured_at"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in values:
            writer.writerow(dict(row, sampled_from=" ".join(row["sampled_from"])))
    save(data_dir / "comments_manifest.json", {"captured_at": now(), "bvid": detail["bvid"], "aid": detail["aid"],
        "status": "partial_or_failed" if failures else "complete_requested_sample", "requested_pages_per_sort": args.pages,
        "sort": args.sort, "unique_records": len(values), "audience_main": sum(r["level"] == "main" for r in audience),
        "audience_children": sum(r["level"] == "child" for r in audience), "creator_records": len(creator),
        "samples": samples, "errors": failures,
        "scope_note": "Nonrandom API sample. Returned child replies are thread snippets, not complete threads. Records/likes are not unique viewers or causal evidence."})
    return 1 if failures else 0


def verify_command(args):
    output = pathlib.Path(args.out).resolve()
    output.mkdir(parents=True, exist_ok=True)
    try:
        result = validate_media(ffmpeg_path(args.ffmpeg), args.input, args.expected_seconds,
                                args.expected_bytes, output / "decode.txt")
        result.update(video_path=str(pathlib.Path(args.input).resolve()), captured_at=now())
    except Exception as exc:
        result = {"video_path": str(pathlib.Path(args.input).resolve()), "captured_at": now(),
                  "verified": False, "errors": [str(exc)]}
    save(output / "validation.json", result)
    log("Local verification: " + ("passed" if result["verified"] else "failed"))
    return 0 if result["verified"] else 1


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def positive_float(value):
    number = float(value)
    if number <= 0 or not math.isfinite(number):
        raise argparse.ArgumentTypeError("must be finite and positive")
    return number


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    video = commands.add_parser("video", help="download/verify a public video and sample frames")
    inspect = commands.add_parser("inspect", help="read description/parts and measure a bounded download sample")
    space = commands.add_parser("space", help="save a public UP submission snapshot")
    frames = commands.add_parser("frames", help="sample a local video with timestamped contact sheets")
    comments = commands.add_parser("comments", help="collect a bounded public comment sample")
    verify = commands.add_parser("verify", help="verify a local video without making any network request")
    for command in (video, inspect, space, frames, comments, verify):
        command.add_argument("input")
        command.add_argument("--out", required=True)
    parts = video.add_mutually_exclusive_group()
    parts.add_argument("--page", type=positive_int)
    parts.add_argument("--all-parts", action="store_true")
    video.add_argument("--quality", type=positive_int, default=64)
    video.add_argument("--skip-frames", action="store_true")
    video.add_argument("--allow-slow", action="store_true", help="continue slow downloads after the user chooses to wait")
    inspect.add_argument("--page", type=positive_int)
    inspect.add_argument("--quality", type=positive_int, default=64)
    for command in (video, frames, verify):
        command.add_argument("--ffmpeg")
    for command in (video, frames):
        command.add_argument("--width", type=positive_int, default=640)
    video.add_argument("--interval", type=positive_float, default=4)
    timing = frames.add_mutually_exclusive_group()
    timing.add_argument("--interval", type=positive_float, default=4)
    timing.add_argument("--times", help="comma-separated explicit seek seconds")
    space.add_argument("--order", choices=("click", "pubdate"), default="click")
    space.add_argument("--limit", type=positive_int, default=10)
    comments.add_argument("--sort", choices=("hot", "time", "both"), default="hot")
    comments.add_argument("--pages", type=positive_int, default=1)
    verify.add_argument("--expected-seconds", type=positive_float)
    verify.add_argument("--expected-bytes", type=positive_int)
    args = parser.parse_args(argv)
    try:
        if args.command == "frames":
            times = [float(v) for v in args.times.split(",")] if args.times else None
            extract_frames(ffmpeg_path(args.ffmpeg), args.input, args.out, args.interval, times, args.width)
            return 0
        return {"video": video_command, "inspect": inspect_command, "space": space_command, "comments": comments_command,
                "verify": verify_command}[args.command](args)
    except Exception as exc:
        log(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
