#!/usr/bin/env python3
"""Download Bilibili audio and transcribe with local MLX STT."""

from __future__ import annotations

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


def run_cmd(cmd: list[str], extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def detect_urls(yt_dlp_bin: str, url: str) -> list[str]:
    info_cmd = [yt_dlp_bin, "--flat-playlist", "--dump-single-json", url]
    info_res = run_cmd(info_cmd)
    if info_res.returncode != 0:
        return [url]

    try:
        payload = json.loads(info_res.stdout)
    except json.JSONDecodeError:
        return [url]

    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        return [url]

    urls: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_url = entry.get("url")
        if not entry_url and entry.get("id"):
            entry_url = f"https://www.bilibili.com/video/{entry['id']}"
        if entry_url:
            urls.append(str(entry_url))
    return urls or [url]


def parse_downloaded_path(stdout: str) -> str:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    return lines[-1] if lines else ""


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


def resolve_run_dir(source_url: str) -> tuple[Path, str]:
    explicit_run_dir = first_env("BILIBILI_WATCHER_RUN_DIR", "VIDEO_WATCHER_RUN_DIR")
    if explicit_run_dir:
        run_dir = Path(explicit_run_dir).expanduser()
        return run_dir, run_dir.name

    base_dir = Path(
        first_env("BILIBILI_WATCHER_OUTPUT_BASE_DIR", "VIDEO_WATCHER_OUTPUT_BASE_DIR")
        or str(DEFAULT_OUTPUT_BASE_DIR)
    ).expanduser()
    run_name = first_env("BILIBILI_WATCHER_RUN_NAME", "VIDEO_WATCHER_RUN_NAME")
    if not run_name:
        run_name = extract_bvid(source_url) or "bilibili-watch"
    run_id = f"{safe_name(run_name, 'bilibili-watch')}_{timestamp_tag()}"
    return base_dir / run_id, run_id


def write_transcript_file(
    transcript_dir: Path,
    title: str,
    source_url: str,
    audio_path: str,
    text: str,
    stt_error: dict[str, Any] | None,
) -> Path:
    filename = f"{safe_name(title)}.txt"
    path = unique_target_path(transcript_dir, filename)
    content = [
        f"Title: {title}",
        f"URL: {source_url}",
        f"Audio: {audio_path}",
        "",
    ]
    if stt_error:
        content.append("[TRANSCRIBE_ERROR]")
        content.append(json.dumps(stt_error, ensure_ascii=False, indent=2))
        content.append("")
    if text:
        content.append(text)
    path.write_text("\n".join(content), encoding="utf-8")
    return path


def transcribe_with_stt(
    stt_python: str,
    stt_script: str,
    audio_path: str,
    ffmpeg_bin: str,
) -> tuple[str, dict[str, Any] | None]:
    stt_cmd = [stt_python, stt_script, audio_path, "--json"]
    result = run_cmd(stt_cmd, extra_env={"STT_FFMPEG_BIN": ffmpeg_bin})
    if result.returncode != 0:
        return "", {
            "code": "TRANSCRIBE_FAILED",
            "message": "STT script failed",
            "retryable": True,
            "details": {
                "return_code": result.returncode,
                "stderr": result.stderr.strip(),
            },
        }

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return result.stdout.strip(), None

    if payload.get("ok"):
        text = payload.get("data", {}).get("text") if isinstance(payload.get("data"), dict) else ""
        return str(text or ""), None

    return "", payload.get("error") if isinstance(payload, dict) else None


def first_env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def main(argv: list[str]) -> int:
    if len(argv) < 1:
        print(json.dumps(error_payload("BAD_REQUEST", "Usage: watch.py <BILIBILI_URL>", False), ensure_ascii=False))
        return 1

    source_url = argv[0]
    yt_dlp_bin = resolve_binary("YT_DLP_BIN", ["yt-dlp"])
    ffmpeg_bin = resolve_binary("FFMPEG_BIN", ["ffmpeg"])
    if not yt_dlp_bin or not ffmpeg_bin:
        print(
            json.dumps(
                error_payload(
                    "DEPENDENCY_MISSING",
                    "yt-dlp or ffmpeg not found",
                    False,
                    details={"YT_DLP_BIN": bool(yt_dlp_bin), "FFMPEG_BIN": bool(ffmpeg_bin)},
                ),
                ensure_ascii=False,
            )
        )
        return 1

    script_dir = Path(__file__).resolve().parent
    stt_script = first_env(
        "BILIBILI_WATCHER_STT_SCRIPT",
        "STT_QWEN_SCRIPT",
    ) or str(script_dir / "stt.py")
    stt_python = first_env(
        "BILIBILI_WATCHER_STT_PYTHON",
        "STT_QWEN_PYTHON",
    ) or sys.executable
    if not Path(stt_script).exists():
        print(
            json.dumps(
                error_payload(
                    "DEPENDENCY_MISSING",
                    "STT script not found",
                    False,
                    details={"stt_script": stt_script},
                ),
                ensure_ascii=False,
            )
        )
        return 1

    explicit_audio_dir = first_env("BILIBILI_WATCHER_OUTPUT_DIR", "VIDEO_WATCHER_OUTPUT_DIR")
    if explicit_audio_dir:
        audio_dir = Path(explicit_audio_dir).expanduser()
        run_dir = audio_dir.parent
        run_id = run_dir.name
    else:
        run_dir, run_id = resolve_run_dir(source_url)
        audio_dir = run_dir / "audio"
    transcript_dir = run_dir / "transcriptions"
    try:
        audio_dir.mkdir(parents=True, exist_ok=True)
        transcript_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(
            json.dumps(
                error_payload(
                    "OUTPUT_DIR_ERROR",
                    "Failed to prepare run directory",
                    False,
                    details={"path": str(run_dir), "reason": str(exc)},
                ),
                ensure_ascii=False,
            )
        )
        return 1

    cookies_browser = first_env("BILIBILI_WATCHER_COOKIES_BROWSER", "VIDEO_WATCHER_COOKIES_BROWSER") or "safari"
    urls_to_process = detect_urls(yt_dlp_bin, source_url)
    results: list[dict[str, Any]] = []

    for target_url in urls_to_process:
        audio_cmd = [
            yt_dlp_bin,
            "--ffmpeg-location",
            ffmpeg_bin,
            "-f",
            "ba",
            "-x",
            "--audio-format",
            "m4a",
            "--cookies-from-browser",
            cookies_browser,
            "--no-progress",
            "--print",
            "after_move:filepath",
            "-o",
            f"{audio_dir}/%(title)s.%(ext)s",
            target_url,
        ]
        dl_res = run_cmd(audio_cmd)
        if dl_res.returncode != 0:
            results.append(
                {
                    "url": target_url,
                    "text": "",
                    "error": {
                        "code": "DOWNLOAD_FAILED",
                        "message": "Failed to download audio",
                        "retryable": True,
                        "details": {
                            "return_code": dl_res.returncode,
                            "stderr": dl_res.stderr.strip(),
                        },
                    },
                }
            )
            continue

        audio_path = parse_downloaded_path(dl_res.stdout)
        if not audio_path:
            results.append(
                {
                    "url": target_url,
                    "text": "",
                    "error": {
                        "code": "OUTPUT_NOT_FOUND",
                        "message": "yt-dlp did not return an output path",
                        "retryable": True,
                        "details": {},
                    },
                }
            )
            continue

        transcript, stt_error = transcribe_with_stt(stt_python, stt_script, audio_path, ffmpeg_bin)
        title = Path(audio_path).stem
        transcript_path = write_transcript_file(
            transcript_dir=transcript_dir,
            title=title,
            source_url=target_url,
            audio_path=audio_path,
            text=transcript,
            stt_error=stt_error,
        )
        item = {
            "title": title,
            "url": target_url,
            "audio_path": audio_path,
            "transcript_path": str(transcript_path),
            "text": transcript,
        }
        if stt_error:
            item["error"] = stt_error
        results.append(item)

    summary = {
        "source_url": source_url,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "audio_dir": str(audio_dir),
        "transcript_dir": str(transcript_dir),
        "items": results,
    }
    (run_dir / "run.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            ok_payload(summary),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
