#!/usr/bin/env python3
"""Launch MaaMCP with process-local ADB discovery and stop compatibility.

Requires maa-mcp in this interpreter. Device registration and every device
operation remain in MaaMCP; this helper may only start an absent host ADB
daemon. It never kills or restarts an existing daemon or edits site-packages.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import runpy
import shutil
import socket
import subprocess
import sys
import tempfile


def diagnostic(event: str, **fields) -> None:
    """Reserve stdout for the MCP JSON-RPC transport."""
    print("[maa-mcp] " + json.dumps({"event": event, **fields}, ensure_ascii=False),
          file=sys.stderr, flush=True)


def adb_candidates(explicit: str | None = None) -> list[Path]:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise ValueError(
                f"找不到指定的 ADB 程序：{path}，无法用它发现模拟器。"
                "请将 --adb-path 改为当前模拟器安装目录中实际的 adb.exe 完整路径。")
        return [path]
    possible = []
    for key in ("ProgramFiles", "ProgramW6432"):
        if os.environ.get(key):
            root = Path(os.environ[key]) / "Netease" / "MuMu Player 12"
            possible.extend((root / "nx_main/adb.exe", root / "shell/adb.exe"))
    if found := shutil.which("adb"):
        possible.append(Path(found))
    paths = []
    for path in possible:
        path = path.resolve()
        if path.is_file() and path not in paths:
            paths.append(path)
    return paths


def adb_endpoint(environ) -> tuple[str, int] | None:
    value = environ.get("ADB_SERVER_SOCKET")
    if value:
        if not value.startswith("tcp:"):
            return None
        address = value[4:]
        host, sep, port = address.rpartition(":")
        if not sep:
            host, port = "127.0.0.1", address
        host = host.strip("[]") or "127.0.0.1"
    else:
        host, port = "127.0.0.1", environ.get("ANDROID_ADB_SERVER_PORT", "5037")
    try:
        number = int(port)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "ADB 服务端口无法识别，不能确认要连接的服务。"
            "请检查 ADB_SERVER_SOCKET 或 ANDROID_ADB_SERVER_PORT，端口需为 1～65535 的整数。") from exc
    if not 1 <= number <= 65535:
        raise ValueError("ADB 服务端口超出范围，无法连接。请将端口设置为 1～65535 的整数。")
    return host, number


def server_listening(endpoint: tuple[str, int]) -> bool:
    try:
        with socket.create_connection(endpoint, timeout=0.3):
            return True
    except OSError:
        return False


def ensure_adb_server(adb: Path, data_dir: Path) -> bool:
    endpoint = adb_endpoint(os.environ)
    # A remote or non-TCP daemon is managed by its owner.
    if endpoint is None or endpoint[0] not in {"127.0.0.1", "localhost", "::1"}:
        return True
    if server_listening(endpoint):
        return True
    log_root = data_dir / "adb-host"
    log_root.mkdir(parents=True, exist_ok=True)
    private_temp = Path(tempfile.mkdtemp(prefix="session-", dir=log_root))
    # A fresh TEMP avoids changing or deleting an existing, unwritable adb.log.
    env = dict(os.environ, TEMP=str(private_temp), TMP=str(private_temp))
    help_text = ("ADB 服务未能启动，无法发现模拟器。请查看本条诊断中的原因，"
                 "核对 --adb-path，并确认 --data-dir 指向可写目录。")
    try:
        result = subprocess.run(
            [str(adb), "start-server"], env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        diagnostic("adb_server_start_failed", adb=str(adb), log_dir=str(private_temp),
                   reason=str(exc), help=help_text)
        return False
    if result.returncode != 0 or not server_listening(endpoint):
        diagnostic("adb_server_start_failed", adb=str(adb), log_dir=str(private_temp),
                   returncode=result.returncode,
                   reason=result.stderr.decode("utf-8", errors="replace").strip(),
                   help=help_text)
        return False
    diagnostic("adb_server_started", adb=str(adb), log_dir=str(private_temp))
    return True


def install_discovery(toolkit, data_dir: Path, explicit: str | None = None) -> None:
    paths = adb_candidates(explicit)
    original = toolkit.find_adb_devices

    def find(specified_adb=None):
        candidates = adb_candidates(str(specified_adb)) if specified_adb else paths
        for adb in candidates:
            if not ensure_adb_server(adb, data_dir):
                continue
            devices = original(adb)
            if devices:
                diagnostic("adb_devices_found", adb=str(adb), count=len(devices))
                return devices
        if not specified_adb and not explicit:
            devices = original()
            if devices:
                return devices
        diagnostic("adb_devices_empty", adb_paths=[str(p) for p in candidates],
                   help="未发现可连接的设备。请确认模拟器已启动并开启 ADB 调试；"
                        "必要时用 --adb-path 指定该实例的 adb.exe 后重启本次 MCP 服务。")
        return []

    toolkit.find_adb_devices = staticmethod(find)


def install_stop_compat(tasker_type) -> None:
    if not hasattr(tasker_type, "stop"):
        def stop(self):
            job = self.post_stop().wait()
            if not job.succeeded:
                raise RuntimeError("停止战斗任务失败，可能仍有后台操作。请保留现场并检查本次 MCP 日志后结束该会话。")
        tasker_type.stop = stop


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True,
                        help="本次 MCP 数据与诊断目录；请使用可写目录")
    parser.add_argument("--adb-path", default=os.environ.get("MAA_MCP_ADB_PATH"),
                        help="当前模拟器自带的 adb.exe 完整路径；可省略后自动查找")
    args = parser.parse_args()
    # Keep --help functional without importing any third-party dependencies.
    try:
        args.data_dir = args.data_dir.expanduser().resolve()
        args.data_dir.mkdir(parents=True, exist_ok=True)
        if args.adb_path:
            adb_candidates(args.adb_path)
        import platformdirs
        platformdirs.user_data_dir = lambda *a, **k: str(args.data_dir)
        from maa.toolkit import Toolkit
        from maa.tasker import Tasker
        install_discovery(Toolkit, args.data_dir, args.adb_path)
        install_stop_compat(Tasker)
        runpy.run_module("maa_mcp", run_name="__main__")
    except ImportError as exc:
        diagnostic("dependency_missing", module=exc.name,
                   help="MCP 解释器缺少启动依赖，无法提供设备工具。请使用安装了 maa-mcp 的虚拟环境 Python 启动此脚本。")
        raise SystemExit(1) from exc
    except (OSError, ValueError) as exc:
        diagnostic("startup_failed", reason=str(exc),
                   help="MCP 服务未能启动。请按错误说明检查 --adb-path、--data-dir 或 ADB 端口设置后重试。")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
