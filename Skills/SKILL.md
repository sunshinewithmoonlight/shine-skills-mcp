---
name: bilibili-watcher
description: 下载 B 站视频或音频并转录为文本。用于处理单个 Bilibili URL、合集 URL、批量 URL 列表，或仅下载视频并返回结构化 JSON 结果的场景。
---

# Bilibili Watcher

## 单链接转录

```bash
.venv/bin/python scripts/watch.py "https://www.bilibili.com/video/BVxxxx"
```

脚本会输出统一 JSON，包含源链接、每个条目的标题、音频路径与转录文本。
`watch.py` 默认调用同目录的 `scripts/stt.py`（内置 MLX STT 转录）。
默认模型：`mlx-community/whisper-large-v3-turbo`。
该 skill 已锁定仅使用上述 ASR 模型，请勿传入其它模型 ID。
默认会在 `~/Downloads/bilibili-watcher/<处理名>_<YYYYMMDD-HHMMSS>/` 下生成：
- `audio/`
- `transcriptions/`
- `run.json`

## 批量转录

先准备 URL 文件（每行一个链接，支持 `#` 注释）：

```text
https://www.bilibili.com/video/BV17M4y1Z797
https://www.bilibili.com/video/BV1KY4y1S7NH
```

执行：

```bash
.venv/bin/python scripts/batch_transcribe.py ./urls.txt
```

脚本会逐条调用 `watch.py`，并把本次任务聚合到同一个 `~/Downloads/bilibili-watcher/<处理名>_<时间>/` 文件夹中。

## 仅下载视频

```bash
.venv/bin/python scripts/downloader.py "https://www.bilibili.com/video/BVxxxx" safari
```

## 运行时配置

支持以下环境变量：
- `BILIBILI_WATCHER_OUTPUT_DIR`
- `BILIBILI_WATCHER_OUTPUT_BASE_DIR`
- `BILIBILI_WATCHER_SCRIPT`
- `BILIBILI_WATCHER_PYTHON`
- `BILIBILI_WATCHER_STT_SCRIPT`
- `BILIBILI_WATCHER_STT_PYTHON`
- `BILIBILI_WATCHER_COOKIES_BROWSER`
- `BILIBILI_WATCHER_RUN_NAME`
- `BILIBILI_WATCHER_RUN_DIR`
- `STT_HF_OFFLINE`
- `STT_FFMPEG_BIN`
- `YT_DLP_BIN`
- `FFMPEG_BIN`

兼容以下旧变量（如果已存在）：
- `VIDEO_WATCHER_OUTPUT_DIR`
- `VIDEO_WATCHER_SCRIPT`
- `VIDEO_WATCHER_PYTHON`
- `STT_QWEN_SCRIPT`
- `STT_QWEN_PYTHON`

## 环境初始化

```bash
uv venv
uv sync
```

## 快速启动

单链接：

```bash
~/.runtime/skills/bilibili-watcher/scripts/quick_watch.sh "https://www.bilibili.com/video/BVxxxx"
```

或使用完整环境变量调用：

```bash
YT_DLP_BIN="$HOME/.runtime/skills/bilibili-watcher/.venv/bin/yt-dlp" \
FFMPEG_BIN="$HOME/Library/Application Support/bilibili/ffmpeg/ffmpeg" \
STT_FFMPEG_BIN="$HOME/Library/Application Support/bilibili/ffmpeg/ffmpeg" \
HF_ENDPOINT="${HF_ENDPOINT:-https://hf-cdn.sufy.com}" \
"$HOME/.runtime/skills/bilibili-watcher/.venv/bin/python" \
"$HOME/.runtime/skills/bilibili-watcher/scripts/watch.py" \
"https://www.bilibili.com/video/BVxxxx"
```

模型预热：

```bash
STT_FFMPEG_BIN="$HOME/Library/Application Support/bilibili/ffmpeg/ffmpeg" \
HF_ENDPOINT="${HF_ENDPOINT:-https://hf-cdn.sufy.com}" \
"$HOME/.runtime/skills/bilibili-watcher/.venv/bin/python" \
"$HOME/.runtime/skills/bilibili-watcher/scripts/stt.py" warmup
```
