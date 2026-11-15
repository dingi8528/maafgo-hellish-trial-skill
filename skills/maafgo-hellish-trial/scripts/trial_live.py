#!/usr/bin/env python3
"""Local, read-only trial display. Never connects to or controls a device.

CLI writers publish a bounded event/state snapshot to a session directory
outside the installed skill. The HTTP server only reads these files and serves
the page assets. Browser window-sharing video never passes through this server.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.parse import unquote, urlsplit
import uuid


ASSET_ROOT = Path(__file__).resolve().parents[1] / "assets/live"
MAX_JSON = 512 * 1024
MAX_EVENTS = 40
MAX_MISSIONS = 128
STATIC_SUFFIXES = {".html", ".css", ".js", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".webm", ".mp4", ".woff2", ".ico"}
EVENT_KINDS = {"status", "commentary", "missions", "mission", "warning", "finish"}
MISSION_STATUSES = {"pending", "observed", "confirmed", "failed"}
SESSION_STATUSES = {"waiting", "ready", "running", "paused", "finished", "failed", "stopped"}


class LiveError(ValueError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _text(value, label, limit=2000, *, empty=True):
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise LiveError(f"{label}没有填写有效文字，无法发布播报。请在事件 JSON 中补齐{label}后重试。")
    if len(value) > limit:
        raise LiveError(f"{label}超过 {limit} 个字符，无法发布。请缩短这段文字后重试。")
    return value


def _count(value, label, maximum=None):
    if value is None:
        return None
    if type(value) is not int or value < 0 or (maximum is not None and value > maximum):
        raise LiveError(f"{label}超出当前任务范围，无法更新进度。请填写不小于零且不超过任务总数的整数，未知时填 null。")
    return value


def _read_json(path):
    path = Path(path)
    try:
        if path.stat().st_size > MAX_JSON:
            raise LiveError("展示数据过大，无法安全读取。请新建会话，或缩短本次任务与播报内容后重试。")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("expected object")
        return value
    except LiveError:
        raise
    except FileNotFoundError as exc:
        raise LiveError("展示会话尚未初始化，无法读取状态。请先运行 init --session 指向本次会话目录，再启动网页。") from exc
    except (OSError, ValueError) as exc:
        raise LiveError("展示状态暂时无法读取，网页不能确认当前进度。请检查本次会话文件是否完整；保留文件后重试，不要因此重复操作游戏。") from exc


def atomic_write(path, data):
    """Replace one complete file; retry short Windows sharing violations."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
        for attempt in range(8):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(min(.01 * (attempt + 1), .06))
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _write_json(path, value):
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_JSON:
        raise LiveError("本次展示状态超过大小限制，无法保存。请缩短任务说明或播报，再重试发布。")
    atomic_write(path, data)


@contextmanager
def _state_lock(session):
    session = Path(session)
    session.mkdir(parents=True, exist_ok=True)
    path = session / ".state.lock"
    deadline = time.monotonic() + 5
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            break
        except (FileExistsError, PermissionError):
            if time.monotonic() >= deadline:
                raise LiveError("另一个进程仍占用展示状态，暂时不能发布。请等待该次发布结束；若进程已退出，确认没有发布者后清理会话中的 .state.lock 再重试。")
            time.sleep(.01)
    try:
        yield
    finally:
        for attempt in range(8):
            try:
                path.unlink(missing_ok=True)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(.01 * (attempt + 1))


def init_session(session, title="地狱的试炼", mode="live"):
    session = Path(session).expanduser().resolve()
    if mode not in {"live", "demo"}:
        raise LiveError("展示模式无法识别。请在 --mode 中选择 live 或 demo。")
    _text(title, "会话标题", 160, empty=False)
    with _state_lock(session):
        if (session / "state.json").exists():
            # Initialization is idempotent; it must not reset a real run.
            return _read_json(session / "state.json")
        now = utc_now()
        state = {
            "schema_version": 1,
            "session": {"id": uuid.uuid4().hex, "title": title, "mode": mode,
                        "status": "waiting", "started_at": now, "updated_at": now,
                        "turn": "准备中", "phase": "等待读取当期任务", "attempt": 0},
            "missions": {"items": [], "confirmed_count": 0, "observed_count": 0,
                         "total": None, "baseline_count": None, "game_count": None},
            "commentary": {"title": "BB亲正在准备", "detail": "等待实际画面与战斗记录；这里尚未开始游戏操作。", "at": now},
            "events": [],
            "last_event_id": 0,
        }
        _write_json(session / "state.json", state)
    return state


def _mission(value):
    if not isinstance(value, dict):
        raise LiveError("任务内容不是对象，无法更新任务面板。请按任务事件示例填写 id、label 和 status。")
    result = {
        "id": _text(value.get("id"), "任务标识", 100, empty=False),
        "label": _text(value.get("label"), "任务名称", 200, empty=False),
        "detail": _text(value.get("detail", ""), "任务说明", 1500),
        "status": value.get("status", "pending"),
        "evidence": _text(value.get("evidence", ""), "任务依据", 1500),
    }
    if not isinstance(result["status"], str) or result["status"] not in MISSION_STATUSES:
        raise LiveError("任务状态无法识别，不能更新进度。请使用 pending、observed、confirmed 或 failed。")
    if result["status"] == "confirmed" and not result["evidence"].strip():
        raise LiveError("任务没有确认依据，不能标记为游戏内已确认。请补充任务页核对记录；只有战斗观察时应使用 observed。")
    return result


def publish_event(session, event):
    session = Path(session)
    if not isinstance(event, dict) or not isinstance(event.get("kind"), str) or event["kind"] not in EVENT_KINDS:
        raise LiveError("播报事件类型无法识别，未修改进度。请按文档选择 status、commentary、missions、mission、warning 或 finish。")
    kind = event["kind"]
    title = _text(event.get("title", ""), "播报标题", 200)
    detail = _text(event.get("detail", ""), "播报详情", 4000)
    with _state_lock(session):
        state = _read_json(session / "state.json")
        now = utc_now()
        session_state = state["session"]
        if "status" in event:
            if not isinstance(event["status"], str) or event["status"] not in SESSION_STATUSES:
                raise LiveError("会话状态无法识别，无法更新网页。请使用 waiting、ready、running、paused、finished、failed 或 stopped。")
            session_state["status"] = event["status"]
        if kind == "finish":
            session_state["status"] = event.get("status", "finished")
        for key in ("turn", "phase"):
            if key in event:
                session_state[key] = _text(event[key], "回合或阶段", 160)
        if "attempt" in event:
            session_state["attempt"] = _count(event["attempt"], "挑战次数")
        missions = state["missions"]
        if kind == "missions":
            values = event.get("missions")
            if not isinstance(values, list) or len(values) > MAX_MISSIONS:
                raise LiveError(f"任务清单不存在或超过 {MAX_MISSIONS} 项，无法建立面板。请填写当期实际任务列表。")
            items = [_mission(item) for item in values]
            if len({item["id"] for item in items}) != len(items):
                raise LiveError("任务标识重复，无法对应进度。请给每项任务使用不同的 id 后重试。")
            missions.update(items=items, total=len(items))
        elif kind == "mission":
            value = event.get("mission")
            if not isinstance(value, dict):
                raise LiveError("任务更新缺少 mission 内容，无法发布。请填写要更新的任务 id 与状态。")
            old = next((item for item in missions["items"] if item["id"] == value.get("id")), None)
            if old is None:
                raise LiveError("要更新的任务不在本期清单中，未修改进度。请先发布包含该 id 的 missions 清单，再更新任务。")
            new = _mission({**old, **value})
            missions["items"] = [new if item["id"] == new["id"] else item for item in missions["items"]]
        for key in ("baseline_count", "game_count"):
            if key in event:
                missions[key] = _count(event[key], "游戏任务进度", missions["total"])
        # Replacing the list must also validate retained baseline values.
        for key in ("baseline_count", "game_count"):
            _count(missions[key], "游戏任务进度", missions["total"])
        missions["confirmed_count"] = sum(item["status"] == "confirmed" for item in missions["items"])
        missions["observed_count"] = sum(item["status"] == "observed" for item in missions["items"])
        if kind in {"commentary", "warning", "finish"} or title or detail:
            state["commentary"] = {"title": title or ("请留意当前情况" if kind == "warning" else "BB亲播报"),
                                   "detail": detail, "at": now}
        state["last_event_id"] = int(state.get("last_event_id", 0)) + 1
        record = {"id": state["last_event_id"], "at": now, "kind": kind,
                  "title": title, "detail": detail, "turn": session_state["turn"], "phase": session_state["phase"]}
        state["events"] = (state["events"] + [record])[-MAX_EVENTS:]
        session_state["updated_at"] = now
        _write_json(session / "state.json", state)
    return record


def read_state(session):
    session = Path(session)
    value = _read_json(session / "state.json")
    value.pop("last_event_id", None)
    return value


class LiveServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def create_server(session, port=18767, asset_root=None):
    session = Path(session).expanduser().resolve()
    read_state(session)
    assets = Path(asset_root or ASSET_ROOT).resolve()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def handle(self):
            try:
                super().handle()
            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                # A tab can close while the base class waits for the next
                # keep-alive request or sends its response. This is not a
                # failed game operation. Other exceptions still reach the
                # server's error handler normally.
                self.close_connection = True

        def log_message(self, *args):
            pass

        def _allowed(self):
            try:
                host = urlsplit("http://" + self.headers.get("Host", "")).hostname
                host_port = urlsplit("http://" + self.headers.get("Host", "")).port
            except ValueError:
                return False
            expected_port = self.server.server_address[1]
            if host not in {"127.0.0.1", "localhost", "::1"} or (host_port or 80) != expected_port:
                return False
            origin = self.headers.get("Origin")
            if origin:
                try:
                    parsed = urlsplit(origin)
                    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or (parsed.port or 80) != expected_port:
                        return False
                except ValueError:
                    return False
            return True

        def _headers(self, status, length, content_type, *, etag=None, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; media-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'self'")
            self.send_header("Permissions-Policy", "display-capture=(self), camera=(), microphone=()")
            if etag:
                self.send_header("ETag", etag)
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()

        def _error(self, status, message):
            raw = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
            self._headers(status, len(raw), "application/json; charset=utf-8")
            if self.command != "HEAD":
                self.wfile.write(raw)

        def do_POST(self):
            self.close_connection = True
            self._error(405, "网页仅供查看，不接收控制指令。请由 AI 使用本机发布工具更新播报。")

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_POST

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self._allowed():
                self._error(403, "这个展示页只允许通过本机地址访问。请用启动时显示的 127.0.0.1 网页地址。")
                return
            path = unquote(urlsplit(self.path).path)
            if path == "/api/state":
                try:
                    raw = json.dumps(read_state(session), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                except LiveError as exc:
                    self._error(503, str(exc))
                    return
                self._headers(200, len(raw), "application/json; charset=utf-8")
                if self.command != "HEAD":
                    self.wfile.write(raw)
                return
            relative = path.lstrip("/") or "index.html"
            # No session files or arbitrary filesystem paths are served.
            parts = relative.replace("\\", "/").split("/")
            if any(part.startswith(".") or not part for part in parts) or "\\" in relative or ":" in relative:
                self._error(404, "没有这个展示资源。请检查网页地址。")
                return
            target = (assets / relative).resolve()
            if not target.is_relative_to(assets) or target.suffix.lower() not in STATIC_SUFFIXES:
                self._error(404, "没有这个展示资源。请检查网页地址。")
                return
            try:
                stream = target.open("rb")
            except (FileNotFoundError, IsADirectoryError, PermissionError):
                self._error(404, "页面资源不存在，无法打开。请检查网页地址和技能文件是否完整。")
                return
            with stream:
                stat = os.fstat(stream.fileno())
                size = stat.st_size
                etag = '"' + hashlib.sha256(f"{stat.st_mtime_ns}:{size}".encode()).hexdigest()[:24] + '"'
                if self.headers.get("If-None-Match") == etag:
                    self._headers(304, 0, "application/octet-stream", etag=etag)
                    return
                start, end, code = 0, size - 1, 200
                range_value = self.headers.get("Range")
                if range_value:
                    matched = re.fullmatch(r"bytes=(\d*)-(\d*)", range_value)
                    valid = matched is not None and any(matched.groups()) and size > 0
                    if valid:
                        first, last = matched.groups()
                        if first:
                            start, end = int(first), min(int(last), size - 1) if last else size - 1
                        else:
                            tail = int(last)
                            valid = tail > 0
                            start, end = max(0, size - tail), size - 1
                        valid = valid and 0 <= start <= end < size
                    if not valid:
                        self._headers(416, 0, "application/octet-stream", extra={"Content-Range": f"bytes */{size}"})
                        return
                    code = 206
                length = max(0, end - start + 1)
                extra = {"Accept-Ranges": "bytes"}
                if code == 206:
                    extra["Content-Range"] = f"bytes {start}-{end}/{size}"
                content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                if target.suffix in {".html", ".css", ".js", ".svg"}:
                    content_type += "; charset=utf-8"
                self._headers(code, length, content_type, etag=etag, extra=extra)
                if self.command == "HEAD":
                    return
                stream.seek(start)
                remaining = length
                try:
                    while remaining:
                        chunk = stream.read(min(64 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                    pass  # A hidden/closed tab can cancel media requests.

    return LiveServer(("127.0.0.1", port), Handler)


def demo_session(session):
    """Create only unmistakable local sample data, never a simulated completion."""
    state = init_session(session, title="BB亲展示页演示", mode="demo")
    if state["session"]["mode"] != "demo":
        raise LiveError("这个目录属于真实会话，不能写入演示数据。请用新的 --session 目录运行 demo。")
    publish_event(session, {"kind": "missions", "missions": [
        {"id": "demo-observation", "label": "演示：记录战斗观察", "detail": "这里只展示待确认效果，不对应当期游戏任务。", "status": "observed", "evidence": "演示数据"},
        {"id": "demo-pending", "label": "演示：等待任务页核对", "status": "pending"}],
        "baseline_count": None, "game_count": None})
    publish_event(session, {"kind": "commentary", "title": "前辈，展示页准备好了♡", "detail": "这是演示会话，还没有连接游戏；实际战斗只播报已观察到的内容。", "phase": "演示 · 未连接游戏"})
    return read_state(session)


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "serve", "publish", "demo"):
        item = commands.add_parser(name)
        item.add_argument("--session", type=Path, required=True, help="本次本地展示会话目录，不放在技能安装目录")
        if name == "init":
            item.add_argument("--title", default="地狱的试炼")
            item.add_argument("--mode", choices=("live", "demo"), default="live")
        elif name == "serve":
            item.add_argument("--port", type=int, default=18767)
        elif name == "publish":
            item.add_argument("--event-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = init_session(args.session, args.title, args.mode)
        elif args.command == "publish":
            result = publish_event(args.session, _read_json(args.event_file))
        elif args.command == "demo":
            result = demo_session(args.session)
        else:
            if not 0 <= args.port <= 65535:
                raise LiveError("网页端口超出范围，无法启动。请使用1～65535中的可用端口；测试时可用0自动分配。")
            server = create_server(args.session, args.port)
            info = {"url": f"http://127.0.0.1:{server.server_port}/", "pid": os.getpid()}
            _write_json(args.session / "server.json", info)
            print(json.dumps(info, ensure_ascii=False), flush=True)
            try:
                server.serve_forever()
            finally:
                server.server_close()
            return 0
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    except KeyboardInterrupt:
        return 0
    except (LiveError, OSError) as exc:
        print(f"展示功能未能完成：{exc}。请检查本次会话目录、依赖和端口后重试；简化版可以继续使用。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
