#!/usr/bin/env python3
"""Offline browser acceptance with a synthetic continuous MediaStream, no device.

Developer-only: requires Chrome/Edge, websockets, and optionally psutil. This
does not bypass or test an actual user's screen-sharing picker or capture MuMu.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from urllib.request import ProxyHandler, Request, build_opener

from trial_live import create_server, demo_session, publish_event


def chrome_path(explicit=None):
    if explicit:
        return explicit
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome", "msedge"):
        if found := shutil.which(name):
            return found
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        root = Path(os.environ.get(variable, "."))
        for part in ("Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe"):
            if (root / part).is_file():
                return str(root / part)
    raise ValueError("找不到测试浏览器，请用 --browser 指定 Chrome 或 Edge 程序。")


FAKE_CAPTURE = r"""(() => {
  window.__testStreams = []; window.__testReject = false;
  window.__captureRequests = [];
  Object.defineProperty(navigator.mediaDevices, 'getDisplayMedia', {configurable:true, value:async options => {
    window.__captureRequests.push(options);
    if(window.__testReject) throw new DOMException('Test canceled', 'NotAllowedError');
    const canvas=document.createElement('canvas'); canvas.width=1280; canvas.height=720;
    const ctx=canvas.getContext('2d'); let frames=0;
    const paint=()=>{frames++;ctx.fillStyle='#151329';ctx.fillRect(0,0,1280,720);
      ctx.fillStyle='#d9c17f';ctx.font='bold 48px sans-serif';ctx.fillText('连续视频 · 离线演示',70,160);
      ctx.fillStyle='#e7dff6';ctx.font='28px sans-serif';ctx.fillText('合成 MediaStream / 非真实游戏画面',70,220);
      ctx.fillText('本地窗口视频展示测试 · '+frames,70,285);
      ctx.fillStyle='#9470c4';ctx.fillRect(70+(frames*5)%1000,390,120,120);
      ctx.fillStyle='#e2cf9b';ctx.fillRect(70,600,Math.min(1100,frames*3),5);
    }; paint(); const timer=setInterval(paint,1000/30);
    const stream=canvas.captureStream(30);
    stream.getVideoTracks()[0].addEventListener('ended',()=>clearInterval(timer));
    window.__testStreams.push({stream,timer}); return stream;
  }});
  return true;
})()"""


def run(output, browser=None, seconds=8):
    from websockets.sync.client import connect
    try:
        import psutil
    except ImportError:
        psutil = None
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    session = output / "session"
    demo_session(session)
    server = create_server(session, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    profile = output / "browser-profile"
    log = (output / "browser.log").open("wb")
    arguments = [chrome_path(browser), "--headless=new", "--mute-audio", "--no-first-run",
                 "--no-default-browser-check", "--remote-debugging-port=0",
                 "--autoplay-policy=no-user-gesture-required", "--window-size=1920,1200",
                 f"--user-data-dir={profile}", f"http://127.0.0.1:{server.server_port}/"]
    if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0:
        arguments.insert(1, "--no-sandbox")
    process = subprocess.Popen(arguments, stdout=log, stderr=subprocess.STDOUT,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    ws = None
    report = {"scope": "synthetic continuous MediaStream; not native MuMu capture",
              "checks": [], "benchmarks": []}
    opener = build_opener(ProxyHandler({}))
    sequence = 0
    try:
        deadline = time.monotonic() + 25
        while not (profile / "DevToolsActivePort").exists():
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("测试浏览器未启动，请查看 browser.log。")
            time.sleep(.1)
        port = (profile / "DevToolsActivePort").read_text().splitlines()[0]
        with opener.open(f"http://127.0.0.1:{port}/json/list", timeout=5) as response:
            target = next(item for item in json.load(response) if item["type"] == "page")
        ws = connect(target["webSocketDebuggerUrl"], proxy=None, max_size=8 * 1024 * 1024)

        def call(method, params=None):
            nonlocal sequence
            sequence += 1
            ws.send(json.dumps({"id": sequence, "method": method, "params": params or {}}))
            while True:
                item = json.loads(ws.recv(timeout=30))
                if item.get("id") == sequence:
                    if "error" in item:
                        raise RuntimeError(item["error"])
                    return item.get("result", {})

        def evaluate(expression):
            result = call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
            if "exceptionDetails" in result:
                raise RuntimeError(result["exceptionDetails"])
            return result.get("result", {}).get("value")

        def wait_for(expression):
            until = time.monotonic() + 12
            while time.monotonic() < until:
                if evaluate(expression): return
                time.sleep(.1)
            raise AssertionError("Browser condition timed out: " + expression)

        call("Emulation.setDeviceMetricsOverride", {"width": 1920, "height": 1080, "deviceScaleFactor": 1, "mobile": False})
        wait_for("document.querySelector('#start-capture') && document.querySelector('#demo-badge').hidden === false")
        report["checks"].append("demo label and backend state loaded")
        evaluate(FAKE_CAPTURE)
        evaluate("document.querySelector('#animate-bb').checked=false;document.querySelector('#animate-bb').dispatchEvent(new Event('change'))")

        def cpu_snapshot():
            if psutil is None: return {}
            result = {}
            try:
                processes = [psutil.Process(process.pid), *psutil.Process(process.pid).children(recursive=True)]
            except psutil.Error: return result
            for item in processes:
                try:
                    times = item.cpu_times()
                    try:
                        unique = item.memory_full_info().uss
                    except (psutil.Error, AttributeError):
                        unique = None
                    result[item.pid] = (times.user + times.system, item.memory_info().rss, unique)
                except psutil.Error: pass
            return result

        def measure(label):
            # Allow first decode, layout and startup work to settle before each sample.
            time.sleep(2)
            before = cpu_snapshot()
            first = evaluate("({time:document.querySelector('#game-stream').currentTime,frames:document.querySelector('#game-stream').getVideoPlaybackQuality().totalVideoFrames})")
            started = time.monotonic()
            time.sleep(seconds)
            duration = time.monotonic() - started
            after = cpu_snapshot()
            last = evaluate("({time:document.querySelector('#game-stream').currentTime,frames:document.querySelector('#game-stream').getVideoPlaybackQuality().totalVideoFrames,dropped:document.querySelector('#game-stream').getVideoPlaybackQuality().droppedVideoFrames,fps:document.querySelector('#frame-age').textContent})")
            cpu = sum(max(0, value[0] - before.get(pid, value)[0]) for pid, value in after.items())
            row = {"label": label, "sample_seconds": round(duration, 2), "browser_process_cpu_single_core_percent": round(cpu / duration * 100, 2) if psutil else None,
                   "process_rss_sum_MiB_shared_pages_may_be_counted_twice": round(sum(value[1] for value in after.values()) / 1048576, 1) if psutil else None,
                   "process_unique_memory_MiB": round(sum(value[2] for value in after.values() if value[2] is not None) / 1048576, 1) if psutil else None,
                   "video_presented_fps": round((last['frames'] - first['frames']) / duration, 1),
                   "media_time_advanced_seconds": round(last['time'] - first['time'], 2), "video_dropped_frames": last['dropped'], "display_label": last['fps']}
            report['benchmarks'].append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)

        measure("page_without_game_stream_or_BB_motion")
        evaluate("document.querySelector('#start-capture').click()")
        wait_for("document.querySelector('#game-stream').srcObject && document.querySelector('#game-stream').readyState>=2")
        measure("continuous_30fps_synthetic_stream_BB_off")
        assert report['benchmarks'][-1]['media_time_advanced_seconds'] > seconds * .7
        assert report['benchmarks'][-1]['video_presented_fps'] > 10
        report['checks'].append("actual continuous MediaStream playback, no JPEG feed")
        evaluate("document.querySelector('#animate-bb').checked=true;document.querySelector('#animate-bb').dispatchEvent(new Event('change'))")
        measure("continuous_30fps_synthetic_stream_BB_on")
        screenshot = call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        (output / "preview.png").write_bytes(base64.b64decode(screenshot['data']))
        evaluate("window.__oldStream=document.querySelector('#game-stream').srcObject;window.__testReject=true;document.querySelector('#start-capture').click()")
        wait_for("!document.querySelector('#start-capture').disabled")
        assert evaluate("document.querySelector('#game-stream').srcObject===window.__oldStream && window.__oldStream.getVideoTracks()[0].readyState==='live'")
        report['checks'].append("canceling replacement keeps old stream")
        evaluate("window.__testReject=false;document.querySelector('#start-capture').click()")
        wait_for("!document.querySelector('#start-capture').disabled")
        assert evaluate("window.__oldStream.getTracks().every(t=>t.readyState==='ended')")
        report['checks'].append("successful replacement stops old tracks")
        evaluate("document.querySelector('#capture-fps').value='15';document.querySelector('#capture-fps').dispatchEvent(new Event('change'))")
        wait_for("!document.querySelector('#capture-fps').disabled")
        assert evaluate("document.querySelector('#game-stream').srcObject.getVideoTracks()[0].readyState==='live'")
        report['checks'].append("rate change keeps continuous sharing")
        evaluate("window.__lastStream=document.querySelector('#game-stream').srcObject;document.querySelector('#stop-capture').click()")
        assert evaluate("document.querySelector('#game-stream').srcObject===null && window.__lastStream.getTracks().every(t=>t.readyState==='ended')")
        report['checks'].append("stop sharing closes every track")
        publish_event(session, {"kind": "commentary", "title": "前辈，播报已经更新啦", "detail": "这是离线测试，不是游戏任务达成。"})
        wait_for("document.querySelector('#commentary-title').textContent==='前辈，播报已经更新啦'")
        report['checks'].append("new backend commentary reaches page")
        assert evaluate("!performance.getEntriesByType('resource').some(r=>r.name.includes('frame.jpg'))")
        report['checks'].append("no JPEG polling")
        evaluate("window.__testReject=false;document.querySelector('#start-capture').click()")
        wait_for("!document.querySelector('#start-capture').disabled")
        evaluate("window.__endedStream=document.querySelector('#game-stream').srcObject;window.__endedStream.getVideoTracks()[0].stop();window.__endedStream.getVideoTracks()[0].dispatchEvent(new Event('ended'))")
        assert evaluate("document.querySelector('#game-stream').srcObject===null")
        report['checks'].append("external sharing-ended event cleans UI")
        evaluate("document.querySelector('#start-capture').click()")
        wait_for("!document.querySelector('#start-capture').disabled")
        evaluate("window.__unloadStream=document.querySelector('#game-stream').srcObject;window.dispatchEvent(new Event('pagehide'))")
        assert evaluate("window.__unloadStream.getTracks().every(t=>t.readyState==='ended')")
        report['checks'].append("pagehide closes sharing tracks")
        report['passed'] = True
    finally:
        children = []
        if psutil:
            try:
                children = psutil.Process(process.pid).children(recursive=True)
            except psutil.Error:
                pass
        if ws:
            try:
                evaluate("window.__testStreams?.forEach(x=>{clearInterval(x.timer);x.stream.getTracks().forEach(t=>t.stop())});document.querySelector('#stop-capture')?.click();true")
                call("Browser.close")
            except Exception: pass
            try: ws.close()
            except Exception: pass
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill();process.wait(timeout=5)
        remaining = []
        if psutil and children:
            _, alive = psutil.wait_procs(children, timeout=3)
            for child in alive:
                try:
                    # psutil's Process identity includes creation time, so this
                    # only targets a child observed under our isolated browser.
                    if child.is_running(): child.terminate()
                except psutil.Error: pass
            _, alive = psutil.wait_procs(alive, timeout=3)
            for child in alive:
                try:
                    if child.is_running(): child.kill()
                except psutil.Error: pass
            _, alive = psutil.wait_procs(alive, timeout=3)
            remaining = [child.pid for child in alive if child.is_running()]
        report['cleanup'] = {"browser_main_exited": process.poll() is not None,
                             "child_processes_checked": bool(psutil), "remaining_owned_children": remaining}
        if remaining: report['passed'] = False
        server.shutdown(); server.server_close(); thread.join(timeout=3);log.close()
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not report.get('passed'):
        raise RuntimeError("本次浏览器检查未完全通过，请查看 report.json 的失败和清理状态。")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--browser")
    parser.add_argument("--benchmark-seconds", type=float, default=8)
    args = parser.parse_args()
    if not 1 <= args.benchmark_seconds <= 30:
        parser.error("每段测试时长需为1～30秒。")
    run(args.output, args.browser, args.benchmark_seconds)


if __name__ == "__main__":
    main()
