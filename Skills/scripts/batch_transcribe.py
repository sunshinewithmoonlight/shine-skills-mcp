#!/usr/bin/env python3
"""Batch transcription runner for Bilibili URL lists."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_OUTPUT_BASE_DIR = Path.home() / "Downloads" / "bilibili-watcher"


def first_env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch transcribe Bilibili URLs from a text file")
    parser.add_argument("url_file", help="Text file containing URLs, one per line")
    parser.add_argument("--output-dir", default=None, help="Base directory for run folders")
    parser.add_argument("--run-name", default=None, help="Run name used in '<run-name>_<timestamp>'")
    parser.add_argument("--watch-script", default=None, help="Path to watch.py")
    parser.add_argument("--python-bin", default=None, help="Python executable for watch.py")
    return parser.parse_args(argv)


def safe_name(raw_name: str, fallback: str = "untitled") -> str:
    name = raw_name.strip().replace(" ", "-")
    cleaned = "".join(ch if (ch.isalnum() or ch in ("-", "_", ".")) else "-" for ch in name)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-._")
    return (cleaned[:80] or fallback).strip()


def timestamp_tag() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def load_urls(path: Path) -> list[str]:
    urls: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


def unique_target_path(output_dir: Path, filename: str) -> Path:
    target = output_dir / filename
    if not target.exists():
        return target

    stem = Path(filename).stem
    suffix = Path(filename).suffix
    index = 2
    while True:
        candidate = output_dir / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def run_watch(
    python_bin: str,
    watch_script: str,
    url: str,
    extra_env: dict[str, str] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    result = subprocess.run([python_bin, watch_script, url], capture_output=True, text=True, env=env)
    if result.returncode != 0:
        return None, result.stderr.strip()
    try:
        return json.loads(result.stdout), ""
    except json.JSONDecodeError:
        return None, "Invalid JSON output from watch.py"


def resolve_run_dir(input_file: Path, output_dir: str | None, run_name: str | None) -> tuple[Path, str]:
    explicit_run_dir = first_env("BILIBILI_WATCHER_RUN_DIR", "VIDEO_WATCHER_RUN_DIR")
    if explicit_run_dir:
        run_dir = Path(explicit_run_dir).expanduser()
        return run_dir, run_dir.name

    base_dir = Path(
        output_dir
        or first_env("BILIBILI_WATCHER_OUTPUT_BASE_DIR", "VIDEO_WATCHER_OUTPUT_BASE_DIR")
        or str(DEFAULT_OUTPUT_BASE_DIR)
    ).expanduser()
    resolved_name = run_name or first_env("BILIBILI_WATCHER_RUN_NAME", "VIDEO_WATCHER_RUN_NAME") or input_file.stem
    run_id = f"{safe_name(resolved_name, 'bilibili-batch')}_{timestamp_tag()}"
    return base_dir / run_id, run_id


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    input_file = Path(args.url_file).expanduser()
    if not input_file.exists():
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "URL_FILE_NOT_FOUND",
                        "message": "URL file not found",
                        "retryable": False,
                        "details": {"path": str(input_file)},
                    },
                },
                ensure_ascii=False,
            )
        )
        return 1

    urls = load_urls(input_file)
    if not urls:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "URL_LIST_EMPTY",
                        "message": "No URL found in url_file",
                        "retryable": False,
                        "details": {"path": str(input_file)},
                    },
                },
                ensure_ascii=False,
            )
        )
        return 1

    script_dir = Path(__file__).resolve().parent
    watch_script = args.watch_script or first_env("BILIBILI_WATCHER_SCRIPT", "VIDEO_WATCHER_SCRIPT") or str(
        script_dir / "watch.py"
    )
    python_bin = args.python_bin or first_env("BILIBILI_WATCHER_PYTHON", "VIDEO_WATCHER_PYTHON") or sys.executable
    run_dir, run_id = resolve_run_dir(input_file=input_file, output_dir=args.output_dir, run_name=args.run_name)
    transcript_dir = run_dir / "transcriptions"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    watch_extra_env = {"BILIBILI_WATCHER_RUN_DIR": str(run_dir)}

    written = 0
    failed: list[dict[str, str]] = []

    for index, url in enumerate(urls, start=1):
        print(f"Processing {index}/{len(urls)}: {url}", file=sys.stderr)
        payload, watch_error = run_watch(python_bin, watch_script, url, extra_env=watch_extra_env)
        if not payload or not payload.get("ok"):
            failed.append({"url": url, "reason": watch_error or "watch_failed"})
            continue

        data = payload.get("data") or {}
        items = data.get("items") if isinstance(data, dict) else []
        if not isinstance(items, list):
            failed.append({"url": url, "reason": "invalid_items"})
            continue

        for item in items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "untitled")
            source_url = str(item.get("url") or url)
            text = str(item.get("text") or "")
            transcript_path = str(item.get("transcript_path") or "").strip()
            if transcript_path and Path(transcript_path).exists():
                written += 1
                continue

            filename = f"{safe_name(title)}.txt"
            file_path = unique_target_path(transcript_dir, filename)
            body = [f"Title: {title}", f"URL: {source_url}", ""]
            if item.get("error"):
                body.append("[TRANSCRIBE_ERROR]")
                body.append(json.dumps(item.get("error"), ensure_ascii=False, indent=2))
                body.append("")
            body.append(text)
            file_path.write_text("\n".join(body), encoding="utf-8")
            written += 1

    summary = {
        "input_file": str(input_file),
        "run_id": run_id,
        "run_dir": str(run_dir),
        "transcript_dir": str(transcript_dir),
        "total_urls": len(urls),
        "written_files": written,
        "failed_urls": failed,
    }
    (run_dir / "run.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "ok": True,
                "data": summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
