#!/usr/bin/env python3
"""Bilibili downloader with configurable binaries and structured output."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_OUTPUT_BASE_DIR = Path.home() / "Downloads" / "bilibili-watcher"


def error_payload(
    code: str,
    message: str,
    retryable: bool,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": details or {},
        },
    }


def ok_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "data": data}


def resolve_binary(env_var: str, fallback_names: list[str]) -> str | None:
    explicit = os.environ.get(env_var, "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if path.exists() and os.access(path, os.X_OK):
            return str(path)
    for name in fallback_names:
        resolved = shutil.which(name)
        if resolved:
            return resolved
    return None


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download a Bilibili video via yt-dlp")
    parser.add_argument("url", help="Video URL")
    parser.add_argument("cookies_browser", nargs="?", default=None)
    parser.add_argument("output_dir", nargs="?", default=None)
    parser.add_argument("--format", default="bestvideo+bestaudio/best")
    return parser.parse_args(argv)


def first_env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def safe_name(raw_name: str, fallback: str = "untitled") -> str:
    name = raw_name.strip().replace(" ", "-")
    cleaned = "".join(ch if (ch.isalnum() or ch in ("-", "_", ".")) else "-" for ch in name)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-._")
    return (cleaned[:80] or fallback).strip()


def timestamp_tag() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def extract_bvid(url: str) -> str | None:
    match = re.search(r"(BV[0-9A-Za-z]{10})", url)
    return match.group(1) if match else None


def resolve_output_dir(args: argparse.Namespace) -> tuple[Path, Path]:
    # Manual path keeps backward compatibility and skips run folder generation.
    manual_output_dir = args.output_dir or first_env("BILIBILI_WATCHER_DOWNLOAD_DIR", "MEMU_DOWNLOAD_OUTPUT_DIR")
    if manual_output_dir:
        output_dir = Path(manual_output_dir).expanduser()
        return output_dir.parent, output_dir

    explicit_run_dir = first_env("BILIBILI_WATCHER_RUN_DIR", "VIDEO_WATCHER_RUN_DIR")
    if explicit_run_dir:
        run_dir = Path(explicit_run_dir).expanduser()
    else:
        base_dir = Path(
            first_env("BILIBILI_WATCHER_OUTPUT_BASE_DIR", "VIDEO_WATCHER_OUTPUT_BASE_DIR")
            or str(DEFAULT_OUTPUT_BASE_DIR)
        ).expanduser()
        run_name = first_env("BILIBILI_WATCHER_RUN_NAME", "VIDEO_WATCHER_RUN_NAME")
        if not run_name:
            run_name = extract_bvid(args.url) or "bilibili-download"
        run_id = f"{safe_name(run_name, 'bilibili-download')}_{timestamp_tag()}"
        run_dir = base_dir / run_id
    return run_dir, run_dir / "videos"


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    yt_dlp_bin = resolve_binary("YT_DLP_BIN", ["yt-dlp"])
    if not yt_dlp_bin:
        print(
            json.dumps(
                error_payload(
                    code="DEPENDENCY_MISSING",
                    message="yt-dlp binary not found",
                    retryable=False,
                    details={"env_var": "YT_DLP_BIN"},
                ),
                ensure_ascii=False,
            )
        )
        return 1

    ffmpeg_bin = resolve_binary("FFMPEG_BIN", ["ffmpeg"])
    if not ffmpeg_bin:
        print(
            json.dumps(
                error_payload(
                    code="DEPENDENCY_MISSING",
                    message="ffmpeg binary not found",
                    retryable=False,
                    details={"env_var": "FFMPEG_BIN"},
                ),
                ensure_ascii=False,
            )
        )
        return 1

    run_dir, output_dir = resolve_output_dir(args)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(
            json.dumps(
                error_payload(
                    code="OUTPUT_DIR_ERROR",
                    message="Failed to prepare output directory",
                    retryable=False,
                    details={"path": str(output_dir), "reason": str(exc)},
                ),
                ensure_ascii=False,
            )
        )
        return 1

    command = [
        yt_dlp_bin,
        "--ffmpeg-location",
        ffmpeg_bin,
        "-f",
        args.format,
        "--merge-output-format",
        "mp4",
        "--no-progress",
        "--print",
        "after_move:filepath",
        "-o",
        f"{output_dir}/%(title)s.%(ext)s",
        args.url,
    ]

    if args.cookies_browser:
        command.extend(["--cookies-from-browser", args.cookies_browser])

    try:
        proc = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        print(
            json.dumps(
                error_payload(
                    code="EXECUTION_FAILED",
                    message="Failed to execute yt-dlp",
                    retryable=False,
                    details={"reason": str(exc)},
                ),
                ensure_ascii=False,
            )
        )
        return 1

    if proc.returncode != 0:
        print(
            json.dumps(
                error_payload(
                    code="DOWNLOAD_FAILED",
                    message="yt-dlp failed",
                    retryable=True,
                    details={
                        "return_code": proc.returncode,
                        "stderr": proc.stderr.strip(),
                    },
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    output_lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    output_path = output_lines[-1] if output_lines else ""

    print(
        json.dumps(
            ok_payload(
                {
                    "url": args.url,
                    "run_dir": str(run_dir),
                    "output_dir": str(output_dir),
                    "output_path": output_path,
                    "command": ["yt-dlp", "..."],
                }
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
