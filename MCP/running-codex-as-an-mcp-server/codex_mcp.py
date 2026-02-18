import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from openai import AsyncOpenAI

from agents import Agent, Runner, set_default_openai_api, set_default_openai_client
from agents.mcp import MCPServerStdio

load_dotenv(override=True)
DEFAULT_MODEL = os.getenv("CODEX_DEFAULT_MODEL", "gpt-5.3-codex")


def _load_ccman_provider() -> tuple[str | None, str | None]:
    """Load current Codex provider from ccman config if present."""
    config_path = Path.home() / ".ccman" / "codex.json"
    if not config_path.exists():
        return None, None

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return None, None

    providers = data.get("providers") or []
    current_provider_id = data.get("currentProviderId")
    current = None
    for provider in providers:
        if provider.get("id") == current_provider_id:
            current = provider
            break

    if current is None and providers:
        current = providers[0]
    if current is None:
        return None, None

    return current.get("baseUrl"), current.get("apiKey")


def _normalize_base_url(base_url: str | None) -> str | None:
    if not base_url:
        return None
    parsed = urlparse(base_url)
    # OpenAI SDK usually expects /v1 on OpenAI-compatible endpoints.
    if parsed.path in ("", "/"):
        return base_url.rstrip("/") + "/v1"
    return base_url


env_api_key = os.getenv("OPENAI_API_KEY")
env_base_url = os.getenv("OPENAI_BASE_URL")
ccman_base_url, ccman_api_key = _load_ccman_provider()

api_key = env_api_key or ccman_api_key
base_url = _normalize_base_url(env_base_url or ccman_base_url)

if not api_key:
    raise RuntimeError(
        "OPENAI_API_KEY is missing. Set it in .env, or configure provider via `ccman gmn`."
    )

client_kwargs = {"api_key": api_key}
if base_url:
    client_kwargs["base_url"] = base_url

set_default_openai_client(AsyncOpenAI(**client_kwargs))
set_default_openai_api("responses")


async def main() -> None:
    async with MCPServerStdio(
        name="Codex CLI",
        params={
            "command": "npx",
            "args": [
                "-y",
                "codex",
                "mcp-server",
                "-c",
                f'model="{DEFAULT_MODEL}"',
                "-c",
                'approval_policy="never"',
                "-c",
                'sandbox_mode="danger-full-access"',
            ],
        },
        client_session_timeout_seconds=360000,
    ) as codex_mcp_server:
        developer_agent = Agent(
            name="Game Developer",
            instructions=(
                "You are an expert in building simple games using basic html + css + javascript with no dependencies. "
                "Save your work in a file called index.html in the current directory. "
                "Write files directly via Codex MCP tools."
            ),
            model=DEFAULT_MODEL,
            mcp_servers=[codex_mcp_server],
        )

        await Runner.run(
            developer_agent,
            "Build a fun single-page browser game in about 50 lines. Save it as index.html.",
        )


if __name__ == "__main__":
    asyncio.run(main())
