#!/usr/bin/env python3
"""MLX STT transcription CLI with structured tool handlers."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ASR_MODEL_ID = "mlx-community/whisper-large-v3-turbo"
DEFAULT_MODEL_ID = os.environ.get("STT_MODEL_ID", ASR_MODEL_ID)
DEFAULT_MODEL_TTL_SECONDS = int(os.environ.get("STT_MODEL_TTL_SECONDS", "600"))
DEFAULT_HF_OFFLINE = os.environ.get("STT_HF_OFFLINE", "1")
DEFAULT_ENABLE_PUNCTUATION = os.environ.get("STT_ENABLE_PUNCTUATION", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
ZH_LANGUAGE_CODES = {"zh", "zh-cn", "zh-tw", "yue", "wuu"}

_MODEL_CACHE: dict[str, Any] = {
    "model": None,
    "model_id": "",
    "loaded_at": 0.0,
    "last_used": 0.0,
}


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


def ensure_allowed_model(model_id: str | None) -> tuple[str, dict[str, Any] | None]:
    resolved = (model_id or DEFAULT_MODEL_ID).strip() or ASR_MODEL_ID
    if resolved != ASR_MODEL_ID:
        return "", error_payload(
            "MODEL_NOT_ALLOWED",
            "Only ASR model is enabled for this skill",
            False,
            {"allowed_model": ASR_MODEL_ID, "requested_model": resolved},
        )
    return resolved, None


def ensure_hf_offline_mode() -> None:
    os.environ.setdefault("HF_HUB_OFFLINE", DEFAULT_HF_OFFLINE)


def resolve_ffmpeg() -> str | None:
    for env_name in ("STT_FFMPEG_BIN", "FFMPEG_BIN"):
        explicit = os.environ.get(env_name, "").strip()
        if explicit:
            path = Path(explicit).expanduser()
            if path.exists() and os.access(path, os.X_OK):
                return str(path)
    return shutil.which("ffmpeg")


def import_runtime_modules() -> tuple[Any, Any, Any, Any]:
    try:
        import numpy as np  # type: ignore
        import mlx.core as mx  # type: ignore
        from mlx_audio.stt.utils import load_model  # type: ignore
        from scipy.io import wavfile  # type: ignore
    except ImportError as exc:
        raise RuntimeError(f"Missing dependency: {exc}") from exc
    return np, mx, load_model, wavfile


def model_cache_hit(model_id: str) -> bool:
    model = _MODEL_CACHE.get("model")
    if not model:
        return False
    if _MODEL_CACHE.get("model_id") != model_id:
        return False
    last_used = float(_MODEL_CACHE.get("last_used") or 0.0)
    return (time.time() - last_used) <= DEFAULT_MODEL_TTL_SECONDS


def load_cached_model(model_id: str) -> tuple[Any, bool]:
    ensure_hf_offline_mode()

    if model_cache_hit(model_id):
        _MODEL_CACHE["last_used"] = time.time()
        return _MODEL_CACHE["model"], True

    _, _, load_model, _ = import_runtime_modules()
    model = load_model(model_id)
    now = time.time()
    _MODEL_CACHE.update(
        {
            "model": model,
            "model_id": model_id,
            "loaded_at": now,
            "last_used": now,
        }
    )
    return model, False


def prepare_audio(audio_path: str, ffmpeg_bin: str) -> tuple[Any, dict[str, Any]]:
    np, mx, _, wavfile = import_runtime_modules()

    if not Path(audio_path).exists():
        raise FileNotFoundError(audio_path)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name

    cmd = [
        ffmpeg_bin,
        "-i",
        audio_path,
        "-ar",
        "16000",
        "-ac",
        "1",
        "-f",
        "wav",
        wav_path,
        "-y",
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "ffmpeg failed")

        sample_rate, data = wavfile.read(wav_path)
        if getattr(data, "ndim", 1) > 1:
            data = data.mean(axis=1)
        if data.dtype == np.int16:
            data = data.astype(np.float32) / 32768.0
        elif data.dtype == np.int32:
            data = data.astype(np.float32) / 2147483648.0
        elif data.dtype != np.float32:
            data = data.astype(np.float32)

        payload = mx.array(data)
        meta = {
            "sample_rate": int(sample_rate),
            "samples": int(data.shape[0]),
        }
        return payload, meta
    finally:
        try:
            Path(wav_path).unlink(missing_ok=True)
        except OSError:
            pass


def extract_text(result: Any) -> str:
    if isinstance(result, dict) and "text" in result:
        return str(result.get("text") or "")
    if hasattr(result, "text"):
        return str(result.text)
    if isinstance(result, list) and result and hasattr(result[0], "text"):
        return str(result[0].text)
    return str(result)


def extract_language(result: Any, fallback: str | None = None) -> str | None:
    if isinstance(result, dict):
        value = result.get("language")
        if value:
            return str(value)
    if hasattr(result, "language"):
        value = getattr(result, "language")
        if value:
            return str(value)
    return fallback


def extract_segments(result: Any) -> list[str]:
    if isinstance(result, dict):
        raw_segments = result.get("segments")
    else:
        raw_segments = getattr(result, "segments", None)

    if not isinstance(raw_segments, list):
        return []

    segments: list[str] = []
    for item in raw_segments:
        if isinstance(item, dict):
            text = str(item.get("text") or "").strip()
        elif hasattr(item, "text"):
            text = str(getattr(item, "text") or "").strip()
        else:
            text = str(item).strip()
        if text:
            segments.append(text)
    return segments


def collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def tighten_cjk_spacing(text: str) -> str:
    text = re.sub(r"\s+([，。！？；：、“”‘’])", r"\1", text)
    text = re.sub(r"([（【《“‘])\s+", r"\1", text)
    text = re.sub(r"\s+([）】》”’])", r"\1", text)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[a-zA-Z0-9])", "", text)
    text = re.sub(r"(?<=[a-zA-Z0-9])\s+(?=[\u4e00-\u9fff])", "", text)
    return text.strip()


def should_restore_zh_punctuation(text: str, language: str | None) -> bool:
    if not DEFAULT_ENABLE_PUNCTUATION:
        return False
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", text))
    if not has_cjk:
        return False
    lang = (language or "").strip().lower()
    if lang and lang not in ZH_LANGUAGE_CODES:
        return False
    return True


def guess_sentence_tail(chunk: str) -> str:
    if chunk.endswith(("吗", "呢", "么", "对不对", "是不是")):
        return "？"
    if chunk.endswith(("啊", "呀", "哇")):
        return "。"
    return "。"


def restore_zh_punctuation_from_segments(segments: list[str]) -> str:
    weak_break_words = {
        "那么",
        "然后",
        "所以",
        "但是",
        "不过",
        "因为",
        "如果",
        "另外",
        "同时",
        "其实",
        "就是",
        "比如",
        "最后",
        "首先",
        "其次",
    }

    cleaned = [
        collapse_whitespace(seg).strip("，。！？；,.!?;: ")
        for seg in segments
        if collapse_whitespace(seg).strip("，。！？；,.!?;: ")
    ]
    if not cleaned:
        return ""

    out: list[str] = []
    clause_count = 0
    sentence_len = 0

    for idx, seg in enumerate(cleaned):
        out.append(seg)
        clause_count += 1
        sentence_len += len(seg)
        is_last = idx == len(cleaned) - 1
        mark = ""

        if is_last:
            mark = guess_sentence_tail(seg)
        elif seg.endswith(("吗", "呢", "么", "对不对", "是不是", "听明白没有")):
            mark = "？"
        elif sentence_len >= 26 and (clause_count >= 4 or seg in weak_break_words):
            mark = "。"
        elif sentence_len >= 36 or clause_count >= 6:
            mark = "。"
        else:
            mark = "，"

        out.append(mark)
        if mark in {"。", "！", "？"}:
            clause_count = 0
            sentence_len = 0

    text = "".join(out)
    text = re.sub(r"([，。！？；])([，。！？；])+", r"\1", text)
    return text


def restore_zh_punctuation_from_dense_text(text: str) -> str:
    dense = collapse_whitespace(text).replace(" ", "")
    if not dense:
        return dense

    dense = re.sub(
        r"(那么|然后|所以|但是|不过|因为|如果|另外|同时|其实|就是|比如|最后|首先|其次)",
        r"\1，",
        dense,
    )
    dense = re.sub(r"(对不对|是不是|听明白没有)(?![，。！？])", r"\1？", dense)

    out: list[str] = []
    sentence_len = 0
    clause_len = 0

    for ch in dense:
        out.append(ch)
        if ch in "。！？；":
            sentence_len = 0
            clause_len = 0
            continue
        if ch in "，、":
            clause_len = 0
            continue

        if re.match(r"[\u4e00-\u9fffA-Za-z0-9]", ch):
            sentence_len += 1
            clause_len += 1

        if clause_len >= 18 and ch in "吧吗呢啊呀哦":
            out.append("，")
            clause_len = 0
        elif sentence_len >= 38:
            out.append("。")
            sentence_len = 0
            clause_len = 0
        elif clause_len >= 24 and ch in "的了是有就也而并且":
            out.append("，")
            clause_len = 0

    restored = "".join(out)
    restored = re.sub(r"([，。！？；])([，。！？；])+", r"\1", restored)
    if restored and restored[-1] not in "。！？":
        restored += "。"
    return restored


def heuristic_restore_zh_punctuation(text: str) -> str:
    parts = [p.strip("，。！？；,.!?;: ") for p in text.split(" ") if p.strip()]
    if not parts:
        return text

    if len(parts) == 1:
        return restore_zh_punctuation_from_dense_text(parts[0])

    weak_break_words = {
        "那么",
        "然后",
        "所以",
        "但是",
        "不过",
        "因为",
        "如果",
        "另外",
        "同时",
        "其实",
        "就是",
        "比如",
        "最后",
        "首先",
        "其次",
        "另外",
    }

    out: list[str] = []
    sentence_len = 0
    for idx, part in enumerate(parts):
        out.append(part)
        sentence_len += len(part)
        is_last = idx == len(parts) - 1
        mark = ""

        if is_last:
            mark = guess_sentence_tail(part)
        elif part in weak_break_words:
            mark = "，"
        elif sentence_len >= 32:
            mark = "。"
        elif sentence_len >= 18 and idx % 5 == 4:
            mark = "，"

        if mark:
            out.append(mark)
            if mark in {"。", "！", "？"}:
                sentence_len = 0

    restored = "".join(out)
    restored = re.sub(r"[，]{2,}", "，", restored)
    restored = re.sub(r"[。]{2,}", "。", restored)
    restored = re.sub(r"[？]{2,}", "？", restored)
    restored = re.sub(r"([，。！？；])([，。！？；])+",
                      r"\1", restored)
    if restored and restored[-1] not in "。！？":
        restored += "。"
    return restored


def post_process_text(
    text: str,
    language: str | None,
    segments: list[str] | None = None,
) -> tuple[str, bool]:
    compact = collapse_whitespace(text)
    if not compact:
        return compact, False

    if should_restore_zh_punctuation(compact, language):
        punct_count = sum(compact.count(ch) for ch in "，。！？；")
        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", compact))
        if punct_count == 0 and cjk_count >= 20:
            if segments:
                compact = restore_zh_punctuation_from_segments(segments)
            else:
                compact = heuristic_restore_zh_punctuation(compact)
            return tighten_cjk_spacing(compact), True
        return tighten_cjk_spacing(compact), False

    return compact, False


def transcribe(
    audio_path: str,
    model_id: str,
    language: str | None,
    output_format: str,
) -> dict[str, Any]:
    ffmpeg_bin = resolve_ffmpeg()
    if not ffmpeg_bin:
        return error_payload(
            "DEPENDENCY_MISSING",
            "ffmpeg not found",
            False,
            {"env_var": "STT_FFMPEG_BIN"},
        )

    if not Path(audio_path).exists():
        return error_payload(
            "INPUT_NOT_FOUND",
            "Audio file does not exist",
            False,
            {"audio_path": audio_path},
        )

    try:
        audio_input, audio_meta = prepare_audio(audio_path, ffmpeg_bin)
    except FileNotFoundError:
        return error_payload(
            "INPUT_NOT_FOUND",
            "Audio file does not exist",
            False,
            {"audio_path": audio_path},
        )
    except RuntimeError as exc:
        return error_payload(
            "AUDIO_PREP_FAILED",
            str(exc),
            True,
            {"audio_path": audio_path},
        )
    except Exception as exc:  # pragma: no cover - defensive catch
        return error_payload(
            "AUDIO_PREP_FAILED",
            str(exc),
            True,
            {"audio_path": audio_path},
        )

    try:
        model, reused = load_cached_model(model_id)
    except Exception as exc:
        return error_payload(
            "MODEL_LOAD_FAILED",
            str(exc),
            True,
            {"model": model_id},
        )

    generate_kwargs: dict[str, Any] = {
        "audio": audio_input,
        "verbose": False,
        "max_tokens": 2048,
    }
    if language:
        generate_kwargs["language"] = language

    started = time.time()
    try:
        result = model.generate(**generate_kwargs)
        detected_language = extract_language(result, language)
        segments = extract_segments(result)
        raw_text = extract_text(result)
        if not raw_text and segments:
            raw_text = " ".join(segments)
        text, punct_restored = post_process_text(raw_text, detected_language, segments)
    except Exception as exc:
        return error_payload(
            "TRANSCRIPTION_FAILED",
            str(exc),
            True,
            {"model": model_id},
        )

    duration_ms = int((time.time() - started) * 1000)
    _MODEL_CACHE["last_used"] = time.time()

    return ok_payload(
        {
            "text": text,
            "model": model_id,
            "model_reused": reused,
            "model_ttl_seconds": DEFAULT_MODEL_TTL_SECONDS,
            "audio": {
                "path": audio_path,
                **audio_meta,
            },
            "language": detected_language,
            "punctuation_restored": punct_restored,
            "output_format": output_format,
            "duration_ms": duration_ms,
        }
    )


def health(model_id: str) -> dict[str, Any]:
    ffmpeg_bin = resolve_ffmpeg()
    runtime_ready = True
    runtime_error = ""
    try:
        import_runtime_modules()
    except RuntimeError as exc:
        runtime_ready = False
        runtime_error = str(exc)

    data = {
        "model": model_id,
        "ffmpeg_available": bool(ffmpeg_bin),
        "ffmpeg_bin": ffmpeg_bin or "",
        "runtime_ready": runtime_ready,
        "runtime_error": runtime_error,
        "model_loaded": bool(_MODEL_CACHE.get("model")),
        "model_cached": model_cache_hit(model_id),
        "model_ttl_seconds": DEFAULT_MODEL_TTL_SECONDS,
    }
    if runtime_ready and ffmpeg_bin:
        return ok_payload(data)
    return error_payload(
        "HEALTH_CHECK_FAILED",
        "One or more runtime dependencies are missing",
        False,
        details=data,
    )


def warmup(model_id: str) -> dict[str, Any]:
    ffmpeg_bin = resolve_ffmpeg()
    if not ffmpeg_bin:
        return error_payload(
            "DEPENDENCY_MISSING",
            "ffmpeg not found",
            False,
            {"env_var": "STT_FFMPEG_BIN"},
        )

    try:
        _, reused = load_cached_model(model_id)
    except Exception as exc:
        return error_payload(
            "MODEL_LOAD_FAILED",
            str(exc),
            True,
            {"model": model_id},
        )

    return ok_payload(
        {
            "model": model_id,
            "model_reused": reused,
            "model_ttl_seconds": DEFAULT_MODEL_TTL_SECONDS,
            "warmed_at": int(time.time()),
        }
    )


def maybe_write_output(text: str, output_path: str | None) -> None:
    if not output_path:
        return
    path = Path(output_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def handle_tool_request(request: dict[str, Any], default_model: str) -> dict[str, Any]:
    tool = str(request.get("tool", "")).strip()
    params = request.get("params") or {}
    if not isinstance(params, dict):
        return error_payload("BAD_REQUEST", "params must be an object", False)

    model_id, model_error = ensure_allowed_model(str(params.get("model") or default_model))
    if model_error:
        return model_error

    if tool == "health":
        return health(model_id)
    if tool == "warmup":
        return warmup(model_id)
    if tool == "transcribe":
        audio_path = str(params.get("audio_path") or "").strip()
        if not audio_path:
            return error_payload(
                "BAD_REQUEST",
                "transcribe requires audio_path",
                False,
            )
        language = params.get("language")
        language_value = str(language) if language is not None else None
        output_format = str(params.get("output_format") or "json")
        return transcribe(audio_path, model_id, language_value, output_format)

    return error_payload("UNKNOWN_TOOL", f"Unsupported tool: {tool}", False)


def serve(default_model: str) -> int:
    for line in sys.stdin:
        raw = line.strip()
        if not raw:
            continue
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            response = error_payload("BAD_REQUEST", "Invalid JSON request", False)
            print(json.dumps(response, ensure_ascii=False), flush=True)
            continue

        if not isinstance(request, dict):
            response = error_payload("BAD_REQUEST", "Request must be a JSON object", False)
            print(json.dumps(response, ensure_ascii=False), flush=True)
            continue

        req_id = request.get("id")
        payload = handle_tool_request(request, default_model)
        if req_id is not None:
            payload = {"id": req_id, **payload}
        print(json.dumps(payload, ensure_ascii=False), flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MLX transcription helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    transcribe_parser = subparsers.add_parser("transcribe", help="Transcribe one file")
    transcribe_parser.add_argument("audio_path")
    transcribe_parser.add_argument("--model", default=DEFAULT_MODEL_ID)
    transcribe_parser.add_argument("--output")
    transcribe_parser.add_argument("--language")
    transcribe_parser.add_argument("--output-format", choices=["text", "json"], default="text")
    transcribe_parser.add_argument("--json", action="store_true", help="Alias of --output-format json")

    health_parser = subparsers.add_parser("health", help="Check runtime readiness")
    health_parser.add_argument("--model", default=DEFAULT_MODEL_ID)

    warmup_parser = subparsers.add_parser("warmup", help="Load model into cache")
    warmup_parser.add_argument("--model", default=DEFAULT_MODEL_ID)

    serve_parser = subparsers.add_parser("serve", help="Start line-delimited JSON tool server")
    serve_parser.add_argument("--model", default=DEFAULT_MODEL_ID)

    return parser


def normalize_legacy_args(argv: list[str]) -> list[str]:
    known = {"transcribe", "health", "warmup", "serve", "-h", "--help"}
    if argv and argv[0] not in known and not argv[0].startswith("-"):
        return ["transcribe", *argv]
    return argv


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(normalize_legacy_args(argv))

    if args.command == "serve":
        model_id, model_error = ensure_allowed_model(args.model)
        if model_error:
            print(json.dumps(model_error, ensure_ascii=False, indent=2))
            return 1
        return serve(model_id)

    if args.command == "health":
        model_id, model_error = ensure_allowed_model(args.model)
        payload = model_error or health(model_id)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload.get("ok") else 1

    if args.command == "warmup":
        model_id, model_error = ensure_allowed_model(args.model)
        payload = model_error or warmup(model_id)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload.get("ok") else 1

    if args.command == "transcribe":
        model_id, model_error = ensure_allowed_model(args.model)
        if model_error:
            print(json.dumps(model_error, ensure_ascii=False, indent=2))
            return 1
        output_format = "json" if args.json else args.output_format
        payload = transcribe(
            audio_path=args.audio_path,
            model_id=model_id,
            language=args.language,
            output_format=output_format,
        )
        if payload.get("ok"):
            text = str(payload["data"].get("text", ""))
            maybe_write_output(text, args.output)
            if output_format == "text":
                print(text)
            else:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        if output_format == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            message = payload.get("error", {}).get("message", "Transcription failed")
            print(message, file=sys.stderr)
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
