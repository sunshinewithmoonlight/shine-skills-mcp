# Running Codex as an MCP Server

This folder follows the OpenAI guide section:
`Use Codex with the Agents SDK` -> `Running Codex as an MCP server`.

## 0) New Mac from zero

Use this if you copy this folder to another Mac and want to run directly.

1. Install base tools (if missing):

```bash
# install Homebrew if missing
command -v brew >/dev/null || /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# install runtime tools
brew install uv node
npm i -g codex
```

2. Optional: install and configure `ccman` provider path:

```bash
npm i -g ccman
ccman gmn
```

3. Enter project and create clean Python environment:

```bash
cd MCP
uv venv --python 3.12 .venv312
.venv312/bin/pip install --upgrade openai openai-agents python-dotenv
```

4. Configure credentials (choose one):

```bash
cp .env.example .env
```

- Option A: set `OPENAI_API_KEY` in `.env`.
- Option B: use `ccman gmn` (script auto-reads `~/.ccman/codex.json`).

5. Run preflight and startup:

```bash
./preflight_check.py
.venv312/bin/python codex_mcp.py
```

Note:
- Do not migrate `.venv` / `.venv312` between Macs. Recreate locally.

## 1) Install dependencies

Requires:
- Python `3.10+`
- `node`/`npx` in PATH (used to launch `codex mcp-server`)

```bash
uv venv --python 3.12 .venv312
.venv312/bin/pip install --upgrade openai openai-agents python-dotenv
```

## 2) Authorization and provider setup

```bash
cp .env.example .env
```

Choose one of the following:
- Set `OPENAI_API_KEY` in `.env`.
- Or run `ccman gmn` first; script will auto-read `~/.ccman/codex.json`.

Optional for OpenAI-compatible providers:
- set `OPENAI_BASE_URL` in `.env` (for example your GMN endpoint).
- set `CODEX_DEFAULT_MODEL` in `.env` if you want to override model.

Default model for this MCP setup:
- `gpt-5.3-codex` (used by both the Agent and `codex mcp-server`)

Recommended Codex config in `~/.codex/config.toml`:

```toml
approval_policy = "never"
sandbox_mode = "danger-full-access"
```

macOS permission note:
- In `System Settings -> Privacy & Security -> Full Disk Access`, allow:
  - your terminal app (`Terminal` or `iTerm`)
  - `node` (used by `npx`)
  - Python runtime (`.venv312/bin/python`)

## 3) Preflight check

Run before startup:

```bash
./preflight_check.py
```

It checks:
- Python version and dependencies
- `npx/codex` command availability
- API key/base URL source (`.env` or `ccman`)
- working directory write access
- endpoint network reachability
- local Codex config values

## 4) Run

```bash
.venv312/bin/python codex_mcp.py
```

This runs a single-agent workflow:
- `Game Developer` uses Codex MCP to generate `index.html` directly.
- Default model is `gpt-5.3-codex`.
- MCP server is launched with `approval_policy=never` and `sandbox_mode=danger-full-access`.

When it finishes, open `index.html` in your browser.
