# Video Frame Expedition for DaVinci Resolve — development tasks, on Windows and macOS (the
# recipes also run on Linux). The Python tools are run through `python -m …`, the same
# everywhere: on Windows, Smart App Control blocks the unsigned .exe launchers generated in the
# virtualenv.

set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]
set shell := ["bash", "-cu"]

py := "uv run --project backend python"
# PowerShell blocks pnpm.ps1 (execution policy): the .cmd shim is called on Windows.
pnpm := if os_family() == "windows" { "pnpm.cmd" } else { "pnpm" }

# Lists the available tasks
default:
    @just --list

# Installs every dependency (backend + frontend) and the pre-commit hook
setup: setup-backend setup-frontend
    {{py}} -m pre_commit install -c scripts/pre-commit-config.yaml

[working-directory('backend')]
setup-backend:
    uv sync

[working-directory('frontend')]
setup-frontend:
    {{pnpm}} install --frozen-lockfile

# ---------------------------------------------------------------- quality

# Every check (lint, typing, architecture, tests), backend + frontend
check: check-backend check-frontend

[working-directory('backend')]
check-backend: lint-backend test-backend

[working-directory('backend')]
lint-backend:
    uv run python -m ruff check .
    uv run python -m ruff format --check .
    uv run python -m mypy
    uv run python -c "from importlinter.cli import lint_imports_command; lint_imports_command()"

[working-directory('backend')]
test-backend *args:
    uv run python -m pytest --cov --cov-report=term-missing:skip-covered {{args}}

# Tests that need LM Studio, the models, the GPU or the internet (on demand)
[working-directory('backend')]
test-live *args:
    uv run python -m pytest -m "lmstudio or models or gpu or network" {{args}}

[working-directory('frontend')]
check-frontend:
    {{pnpm}} run check

# Fixes the style automatically (ruff + prettier)
fmt:
    {{py}} -m ruff check --fix backend
    {{py}} -m ruff format backend
    {{pnpm}} --dir frontend run format

# Audit of the Python dependencies
[working-directory('backend')]
audit:
    uv run python -m pip_audit

# ---------------------------------------------------------------- development

# Backend server in development mode (API + worker + MCP) on :8765
[working-directory('backend')]
dev-backend:
    uv run python -m vfe_vision serve --reload

# Vite server in development mode on :5173 (proxy to :8765)
[working-directory('frontend')]
dev-frontend:
    {{pnpm}} run dev

# Regenerates the OpenAPI schema and the typed TypeScript client
gen-client:
    {{py}} -m vfe_vision openapi --output docs/schemas/openapi.json
    {{pnpm}} --dir frontend run gen:api

# ---------------------------------------------------------------- production

# Builds the frontend, copied into the backend package to be served by `vfe serve`
build:
    {{pnpm}} --dir frontend run build
    {{py}} scripts/copy_frontend_build.py

# Starts the whole application (after `just build`)
serve:
    {{py}} -m vfe_vision serve

# Diagnostics of the environment (ffmpeg, exiftool, LM Studio, GPU or the Mac's memory)
doctor:
    {{py}} -m vfe_vision doctor
