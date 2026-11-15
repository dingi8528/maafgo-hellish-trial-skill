#!/usr/bin/env python3
"""Turn a selected MaaFGO diagnostic log into bounded, factual live captions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time

from trial_live import LiveError, publish_event


TURN = re.compile(r"\[AutoBattle\] Turn (\d+)\s*\|\s*scene=([A-Z_]+)")
PHASE = re.compile(r"\[试炼策略\] 职阶确认血条\s*\d+→(\d+)")
SUBMITTED = re.compile(r"\[试炼策略\] 已确认提交 .*?血条=(\d+)，剩余颜色攻击=(\d+)")


def caption_for_line(line):
    """A log observation never confirms game missions or predicts a win."""
    match = TURN.search(line)
    if match:
        turn, scene = match.groups()
        labels = {"MAIN_BATTLE": "技能阶段", "COMMAND_SELECTION": "指令卡阶段",
                  "ANIMATION": "动画阶段", "RESULT": "结算阶段"}
        return {"kind": "status", "status": "running", "turn": "T" + turn,
                "title": f"前辈，来到第 {turn} 个战斗回合啦", "detail":
                f"日志已识别为{labels.get(scene, '待复核的场景')}；按实际状态继续核对方案。"}
    match = PHASE.search(line)
    if match:
        phase = match.group(1)
        return {"kind": "status", "phase": "阶段 " + phase,
                "title": "进入下一个阶段啦", "detail":
                "日志已确认敌人阶段变化；本阶段需要满足的条件仍以当期任务和现场为准。"}
    match = SUBMITTED.search(line)
    if match:
        phase, remaining = match.groups()
        return {"kind": "commentary", "phase": "阶段 " + phase,
                "title": "这组指令已经提交", "detail":
                f"策略记录的剩余颜色攻击数为 {remaining}。这只是本场记录，还要结合执行与任务页核对。"}
    if "[AutoBattle] Victory." in line:
        return {"kind": "status", "status": "paused", "title": "日志识别到了胜利", "detail":
                "先回任务页核对哪些条件真正保留，不能仅凭胜利就把全部任务打勾。"}
    if "[AutoBattle] Defeat." in line:
        return {"kind": "status", "status": "paused", "title": "这场先停下来复盘", "detail":
                "日志识别到战败；保存现场后按当前授权处理，不自动消耗续关资源。"}
    if "[试炼策略]" in line and any(marker in line for marker in ("[ERROR]", "error:", "异常")):
        return {"kind": "warning", "status": "paused", "title": "策略检查遇到问题", "detail":
                "先保留现场和日志，等待 AI 核对原因；展示页不会替你继续点击游戏。"}
    return None


class LogCursor:
    """Only this exact selected file, bounded reads, no directory auto-selection."""
    def __init__(self, source, from_start=False):
        self.source = Path(source)
        self.offset = None
        self.identity = None
        self.pending = b""
        self.from_start = from_start

    def read(self):
        try:
            stat = self.source.stat()
        except FileNotFoundError:
            return []
        identity = (stat.st_dev, stat.st_ino)
        if self.offset is None:
            self.offset = max(0, stat.st_size - 65536) if self.from_start else stat.st_size
            self.identity = identity
        elif self.identity != identity or stat.st_size < self.offset:
            self.offset, self.pending, self.identity = 0, b"", identity
        with self.source.open("rb") as stream:
            stream.seek(self.offset)
            chunk = stream.read(65536)
            self.offset = stream.tell()
        rows = (self.pending + chunk).split(b"\n")
        self.pending = rows.pop()[-8192:]
        return [row[-8192:].decode("utf-8", "replace").rstrip("\r") for row in rows]


def feed_once(cursor, session):
    count = 0
    for line in cursor.read():
        event = caption_for_line(line)
        if event:
            publish_event(session, event)
            count += 1
    return count


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path,
                        help="本次 Agent 的 debug/recognition-<PID>.log；不自动挑选其他会话")
    parser.add_argument("--from-start", action="store_true", help="首次读取最多最后64KiB，可能回放已有日志")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--max-seconds", type=float, default=3600)
    args = parser.parse_args()
    if not .25 <= args.interval <= 30 or not 1 <= args.max_seconds <= 86400:
        parser.error("读取间隔需为0.25～30秒，运行上限需为1～86400秒，不能无限读取。")
    cursor = LogCursor(args.source, args.from_start)
    deadline = time.monotonic() + args.max_seconds
    try:
        total = 0
        while True:
            total += feed_once(cursor, args.session)
            if args.once or time.monotonic() >= deadline:
                break
            time.sleep(min(args.interval, max(0, deadline - time.monotonic())))
        print(json.dumps({"published_events": total, "device_actions": 0}, ensure_ascii=False))
    except KeyboardInterrupt:
        pass
    except (OSError, LiveError) as exc:
        print(f"本地播报读取失败，无法继续更新文案。请检查选定日志及会话目录后重试；游戏流程不会因此被重新执行。原因：{exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
