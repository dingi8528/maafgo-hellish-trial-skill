#!/usr/bin/env python3
"""Prepare the bundled-style transparent BB WebM and still poster, offline."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


def resolve_ffmpeg(explicit=None):
    if explicit:
        return explicit
    if found := shutil.which("ffmpeg"):
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as exc:
        raise ValueError("找不到 FFmpeg，不能生成人物循环。请安装 FFmpeg 或在独立 Python 环境安装 imageio-ffmpeg，也可用 --ffmpeg 指定已有程序。") from exc


def prepare(source, output, *, ffmpeg=None, ready=False, ping_pong=False):
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_file():
        raise ValueError("人物素材不存在，无法生成循环。请用 --input 指定你已有的透明视频文件。")
    movie, poster = output / "bb-loop.webm", output / "bb-poster.png"
    if source in (movie, poster):
        raise ValueError("输入和输出指向同一文件，不能覆盖源素材。请把 --output-dir 改为另一个目录。")
    if ready and (source.suffix.lower() != ".webm" or ping_pong):
        raise ValueError("直接复制只适用于已做好的透明 WebM 循环，不能同时正倒放。请使用正确的 WebM，或去掉 --ready。")
    output.mkdir(parents=True, exist_ok=True)
    ffmpeg = resolve_ffmpeg(ffmpeg)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def run(arguments):
        result = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *arguments],
                                capture_output=True, timeout=300, creationflags=creationflags)
        if result.returncode:
            raise ValueError("FFmpeg 未能处理透明视频。请检查素材编码与透明通道，保留源素材后重试。" +
                             result.stderr.decode("utf-8", "replace")[-700:])

    if ready:
        shutil.copyfile(source, movie)
    else:
        # This workflow's transparent WebM uses VP9 alpha; other alpha video
        # containers use their normal decoder. Already-looped input needs no reverse.
        decoder = ["-c:v", "libvpx-vp9"] if source.suffix.lower() == ".webm" else []
        video_filter = "fps=24,scale=640:-2,format=rgba"
        if ping_pong:
            video_filter += ",split[f][r];[r]reverse[rev];[f][rev]concat=n=2:v=1:a=0[out]"
            filtering = ["-filter_complex", video_filter, "-map", "[out]"]
        else:
            filtering = ["-vf", video_filter]
        run([*decoder, "-i", str(source), *filtering, "-an", "-c:v", "libvpx-vp9",
             "-pix_fmt", "yuva420p", "-auto-alt-ref", "0", "-b:v", "0", "-crf", "35", str(movie)])
    run(["-c:v", "libvpx-vp9", "-i", str(movie), "-frames:v", "1", "-update", "1", str(poster)])
    result = {"movie": movie.name, "poster": poster.name, "muted": True,
              "source_already_looped": ready, "ping_pong": ping_pong,
              "sha256": hashlib.sha256(movie.read_bytes()).hexdigest()}
    (output / "bb-loop-manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--ffmpeg")
    parser.add_argument("--ready", action="store_true", help="复制已完成的透明WebM循环，避免再次反转或编码")
    parser.add_argument("--ping-pong", action="store_true", help="将单向透明素材加一次倒放；现成循环不要勾选")
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.input, args.output_dir, ffmpeg=args.ffmpeg,
                                 ready=args.ready, ping_pong=args.ping_pong), ensure_ascii=False))
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
