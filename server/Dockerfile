# Build context is the repository root (see docker-compose.yml), because this is a uv
# workspace: the server package depends on the local `beam-protocol` package via
# `[tool.uv.sources]`, so both `protocol/` and `server/` must be visible to the build.

FROM python:3.12-slim AS base

# --- builder: resolve and install dependencies with uv --------------------------
FROM base AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.6 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Copy only the dependency manifests first so this layer is cached across code
# changes that don't touch dependencies.
COPY pyproject.toml uv.lock ./
COPY protocol/pyproject.toml protocol/pyproject.toml
COPY server/pyproject.toml server/pyproject.toml
COPY cli/pyproject.toml cli/pyproject.toml

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-workspace --package beam-server

# Now bring in the actual source and install the workspace packages themselves.
COPY protocol protocol
COPY server server

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --package beam-server

# --- runtime: a slim image with only the built virtualenv ------------------------
FROM base AS runtime

RUN groupadd --system --gid 1000 beam \
    && useradd --system --uid 1000 --gid beam --create-home beam

WORKDIR /app
COPY --from=builder --chown=beam:beam /app/.venv /app/.venv
COPY --from=builder --chown=beam:beam /app/server /app/server
COPY --from=builder --chown=beam:beam /app/protocol /app/protocol

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER beam
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=2)" || exit 1

# --ws-ping-interval/--ws-ping-timeout implement the heartbeat in docs/protocol.md §3:
# uvicorn's websockets implementation owns the actual ping/pong control frames, not the
# application (see beam_server.config.Settings.ws_ping_interval_seconds, which these
# should track if ever changed).
CMD ["uvicorn", "beam_server.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--ws-ping-interval", "20", "--ws-ping-timeout", "45"]
