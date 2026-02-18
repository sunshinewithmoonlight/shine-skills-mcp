#!/usr/bin/env python3
"""Startup preflight checks for codex_mcp.py."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent


def _print_ok(msg: str) -> None:
    print(f"[OK]   {msg}")


def _print_warn(msg: str) -> None:
    print(f"[WARN] {msg}")


def _print_fail(msg: str) -> None:
    print(f"[FAIL] {msg}")


def _run(cmd: list[str], timeout: int = 15) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except Exception as exc:  # pragma: no cover
        return 1, "", str(exc)


def _parse_env_file(path: Path) -> Dict[str, str]:
    data: Dict[str, str] = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        data[key] = val
    return data


def _normalize_base_url(base_url: Optional[str]) -> Optional[str]:
    if not base_url:
        return None
    parsed = urlparse(base_url)
    if parsed.path in ("", "/"):
        return base_url.rstrip("/") + "/v1"
    return base_url


def _load_ccman_provider() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    cfg = Path.home() / ".ccman" / "codex.json"
    if not cfg.exists():
        return None, None, None
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except Exception:
        return None, None, None
    providers = data.get("providers") or []
    current_id = data.get("currentProviderId")
    current = None
    for p in providers:
        if p.get("id") == current_id:
            current = p
            break
    if current is None and providers:
        current = providers[0]
    if current is None:
        return None, None, None
    return current.get("name"), current.get("baseUrl"), current.get("apiKey")


def _check_codex_config() -> None:
    cfg = Path.home() / ".codex" / "config.toml"
    if not cfg.exists():
        _print_warn("~/.codex/config.toml not found")
        return
    text = cfg.read_text(encoding="utf-8")
    ap = re.search(r'^\s*approval_policy\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
    sb = re.search(r'^\s*sandbox_mode\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
    ap_val = ap.group(1) if ap else None
    sb_val = sb.group(1) if sb else None
    if ap_val == "never":
        _print_ok("Codex config approval_policy=never")
    else:
        _print_warn("Codex config approval_policy is not 'never' (recommended)")
    if sb_val == "danger-full-access":
        _print_ok("Codex config sandbox_mode=danger-full-access")
    else:
        _print_warn("Codex config sandbox_mode is not 'danger-full-access' (recommended)")


def _check_python_target() -> tuple[Optional[str], bool]:
    candidates = [
        ROOT / ".venv312" / "bin" / "python",
        ROOT / ".venv" / "bin" / "python",
    ]
    target: Optional[Path] = None
    for c in candidates:
        if c.exists():
            target = c
            break
    if target is None:
        which = shutil.which("python3")
        target = Path(which) if which else None
    if target is None:
        _print_fail("No Python executable found")
        return None, False

    rc, out, err = _run(
        [
            str(target),
            "-c",
            "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
        ]
    )
    if rc != 0:
        _print_fail(f"Failed to query Python version: {err or out}")
        return str(target), False
    try:
        major, minor = map(int, out.split("."))
    except Exception:
        _print_fail(f"Unexpected Python version output: {out}")
        return str(target), False
    if (major, minor) < (3, 10):
        _print_fail(f"Python too old ({out}). Need >= 3.10")
        return str(target), False
    _print_ok(f"Python OK ({out}) at {target}")
    return str(target), True


def _check_python_deps(py_exec: str) -> bool:
    rc, out, err = _run(
        [py_exec, "-c", "import agents, openai, dotenv; print('deps-ok')"], timeout=20
    )
    if rc == 0:
        _print_ok("Python deps installed (openai, openai-agents, python-dotenv)")
        return True
    _print_fail(f"Missing Python deps in selected env: {err or out}")
    return False


def _check_cmd(cmd: str, required: bool = True) -> bool:
    if shutil.which(cmd):
        _print_ok(f"Command found: {cmd}")
        return True
    if required:
        _print_fail(f"Missing required command: {cmd}")
        return False
    _print_warn(f"Optional command missing: {cmd}")
    return True


def _check_file_write() -> bool:
    probe = ROOT / ".preflight_write_probe.tmp"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        _print_ok(f"Writable directory: {ROOT}")
        return True
    except Exception as exc:
        _print_fail(f"Cannot write in {ROOT}: {exc}")
        return False


def _check_api_source() -> tuple[bool, Optional[str]]:
    env_file = _parse_env_file(ROOT / ".env")
    env_key = env_file.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")
    env_base = env_file.get("OPENAI_BASE_URL") or os.getenv("OPENAI_BASE_URL")
    cc_name, cc_base, cc_key = _load_ccman_provider()

    api_key = env_key or cc_key
    base_url = _normalize_base_url(env_base or cc_base)

    if not api_key:
        _print_fail("No API key found (.env OPENAI_API_KEY or ccman current provider)")
        return False, None

    if env_key:
        _print_ok("API key source: .env / environment")
    elif cc_key:
        _print_ok(f"API key source: ccman provider ({cc_name or 'current'})")

    if base_url:
        _print_ok(f"Base URL: {base_url}")
    else:
        _print_ok("Base URL: default OpenAI API")
    return True, base_url


def _check_network(base_url: Optional[str]) -> bool:
    curl = shutil.which("curl")
    if not curl:
        _print_warn("curl not found, skip connectivity check")
        return True
    endpoint = (base_url or "https://api.openai.com/v1").rstrip("/") + "/models"
    rc, out, err = _run(
        [curl, "-sS", "-o", "/dev/null", "-w", "%{http_code}", "-m", "15", endpoint],
        timeout=20,
    )
    if rc != 0:
        _print_fail(f"Network check failed to {endpoint}: {err or out}")
        return False
    if out and out != "000":
        _print_ok(f"Network reachable: {endpoint} (HTTP {out})")
        return True
    _print_fail(f"Network unreachable: {endpoint}")
    return False


def _check_codex_help() -> bool:
    rc, out, err = _run(["npx", "-y", "codex", "mcp-server", "--help"], timeout=30)
    if rc == 0 and "mcp-server" in out:
        _print_ok("Codex MCP command is callable")
        return True
    _print_fail(f"Codex MCP command failed: {err or out}")
    return False


def main() -> int:
    print("Preflight checks for MCP startup")
    print("--------------------------------")
    failed = 0
    default_model = os.getenv("CODEX_DEFAULT_MODEL", "gpt-5.3-codex")
    _print_ok(f"Default model: {default_model}")

    if not _check_cmd("npx", required=True):
        failed += 1
    if not _check_cmd("codex", required=True):
        failed += 1
    _check_cmd("ccman", required=False)

    py_exec, py_ok = _check_python_target()
    if not py_ok:
        failed += 1
    elif py_exec and not _check_python_deps(py_exec):
        failed += 1

    if not _check_file_write():
        failed += 1

    api_ok, base_url = _check_api_source()
    if not api_ok:
        failed += 1
    elif not _check_network(base_url):
        failed += 1

    _check_codex_config()

    if not _check_codex_help():
        failed += 1

    print("--------------------------------")
    _print_warn(
        "Manual macOS permission check: grant Full Disk Access to Terminal/iTerm, node, and Python if writes still fail."
    )
    if failed:
        _print_fail(f"Preflight failed ({failed} blocking issue(s))")
        return 1
    _print_ok("Preflight passed. You can run: .venv312/bin/python codex_mcp.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
