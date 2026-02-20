#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: quick_watch.sh <bilibili_url>"
  exit 1
fi

URL="$1"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$ROOT_DIR/.venv/bin/python"
YT_DLP_BIN_DEFAULT="$ROOT_DIR/.venv/bin/yt-dlp"
FFMPEG_DEFAULT="$HOME/Library/Application Support/bilibili/ffmpeg/ffmpeg"

HF_ENDPOINT="${HF_ENDPOINT:-https://hf-cdn.sufy.com}" \
YT_DLP_BIN="${YT_DLP_BIN:-$YT_DLP_BIN_DEFAULT}" \
FFMPEG_BIN="${FFMPEG_BIN:-$FFMPEG_DEFAULT}" \
STT_FFMPEG_BIN="${STT_FFMPEG_BIN:-$FFMPEG_DEFAULT}" \
"$VENV_PY" "$ROOT_DIR/scripts/watch.py" "$URL"
