#!/usr/bin/env python3
"""Measure a bounded HTTP sample without downloading or installing a package."""
import argparse
import json
import math
import re
import socket
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def public_source(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("测速地址必须是有效的 HTTP 或 HTTPS 链接。")
    if parsed.username or parsed.password:
        raise ValueError("测速链接不能包含账号或密码，请使用公开下载链接。")
    # Do not echo signed query strings, fragments or potentially private paths.
    return f"{parsed.scheme}://{parsed.hostname}" + (f":{parsed.port}" if parsed.port else "")


def response_total(headers, status):
    content_range = headers.get("Content-Range", "")
    matched = re.fullmatch(r"bytes\s+\d+-\d+/(\d+)", content_range.strip(), re.I)
    if matched:
        return int(matched.group(1)), "content_range"
    # On a 206 response Content-Length is the sample length, not the file size.
    if status == 200:
        value = headers.get("Content-Length", "")
        if value.isdigit():
            return int(value), "content_length_200"
    return None, "unknown"


def probe(url, *, max_bytes=2 * 1024 * 1024, seconds=5.0, total_bytes=None):
    source = public_source(url)
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("测速字节数必须填写大于零的整数。")
    if total_bytes is not None and (
            isinstance(total_bytes, bool) or not isinstance(total_bytes, int) or total_bytes <= 0):
        raise ValueError("已知文件大小必须填写大于零的整数。")
    try:
        seconds = float(seconds)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("测速时间必须填写大于零的有限秒数。") from None
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("测速时间必须填写大于零的有限秒数。")
    started = time.perf_counter()
    deadline = started + seconds
    count = 0
    total, total_source = total_bytes, "provided" if total_bytes is not None else "unknown"
    status, error = "sample_complete", None
    request = Request(url, headers={
        "User-Agent": "MaaFGO-Trial-Skill/1.0", "Accept-Encoding": "identity",
        "Range": f"bytes=0-{max_bytes - 1}",
    })
    try:
        with urlopen(request, timeout=min(seconds, 5.0)) as response:
            public_source(response.geturl())
            observed_total, observed_source = response_total(response.headers, response.status)
            if observed_total is not None:
                total, total_source = observed_total, observed_source
            while count < max_bytes:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    status = "time_limit"
                    break
                # HTTPResponse.read1 performs at most one underlying read. A normal
                # read(n) can wait for n bytes on a server that trickles data.
                try:
                    response.fp.raw._sock.settimeout(max(0.01, min(remaining, 1.0)))
                except (AttributeError, OSError):
                    pass
                chunk = response.read1(min(16 * 1024, max_bytes - count))
                if not chunk:
                    break
                count += len(chunk)
    except (TimeoutError, socket.timeout):
        status, error = "time_limit", "timeout"
    except HTTPError as exc:
        status, error = "request_failed", f"http_{exc.code}"
    except (URLError, OSError, ValueError) as exc:
        # Error strings can contain the signed URL or a configured proxy password.
        status, error = "request_failed", type(exc).__name__
    elapsed = time.perf_counter() - started
    rate = count / elapsed if count and elapsed > 0 else None
    failed = status == "request_failed" or error is not None
    eta = max(0, total - count) / rate if total is not None and rate and not failed else None
    slow = rate is None or rate < 128 * 1024 or (eta is not None and eta > 180)
    requires_choice = failed or slow
    if failed:
        estimate_note = "请求失败；已有速率只反映失败前收到的样本，无法估算完整下载的剩余时间。"
        next_action = "下载请求失败，测速未完成。请检查网络和下载地址，再让用户选择重试、手动下载，或提供其信任的下载源；不要自动修改代理或切换镜像。"
    else:
        estimate_note = "短时样本估算；后续速率可能变化。" if eta is not None else "文件总大小或有效速率未知，无法估算剩余时间。"
        next_action = "请让用户选择继续等待、手动下载，或提供其信任的下载源；不要自动修改代理或切换镜像。" if slow else "告知实测速率与预计时间，再开始正式下载并持续观察进度。"
    return {
        "source": source, "status": status, "error": error,
        "sample_bytes": count, "elapsed_seconds": round(elapsed, 3),
        "bytes_per_second": round(rate, 1) if rate else None,
        "total_bytes": total, "total_size_source": total_source,
        "estimated_remaining_seconds": round(eta, 1) if eta is not None else None,
        "estimate_note": estimate_note,
        "slow": slow, "requires_user_choice": requires_choice,
        "next_action": next_action,
        "limits": {"max_sample_bytes": max_bytes, "sample_seconds": seconds},
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="公开 HTTP/HTTPS 下载地址；不会把查询参数打印到报告")
    parser.add_argument("--bytes", type=int, default=2 * 1024 * 1024, dest="max_bytes")
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--total-bytes", type=int, default=None, help="已知整包大小；没有可靠响应头时使用")
    args = parser.parse_args()
    try:
        result = probe(args.url, max_bytes=args.max_bytes, seconds=args.seconds, total_bytes=args.total_bytes)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
