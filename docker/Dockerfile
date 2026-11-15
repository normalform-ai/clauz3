# syntax=docker/dockerfile:1
FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    PATH="/home/clauz3/.local/bin:/app/.venv/bin:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    jq \
    nodejs \
    npm \
    ripgrep \
    tini \
    && rm -rf /var/lib/apt/lists/*

# Install ACP adapters and Claude Code CLI for browser workspace sessions
RUN npm install -g \
    @anthropic-ai/claude-code \
    @agentclientprotocol/claude-agent-acp@0.89.1 \
    && npm cache clean --force

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Create non-root clauz3 operator user and workspace directories
RUN groupadd --gid 1000 clauz3 \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash clauz3 \
    && mkdir -p /app /workspace /home/clauz3/.local/share/clauz3 \
    && chown -R clauz3:clauz3 /app /workspace /home/clauz3

WORKDIR /app
COPY --chown=clauz3:clauz3 pyproject.toml uv.lock README.md ./
COPY --chown=clauz3:clauz3 src ./src

USER clauz3
RUN uv sync --frozen --no-dev

COPY --chown=clauz3:clauz3 --chmod=0755 docker/entrypoint.sh /usr/local/bin/clauz3-dot-entrypoint

WORKDIR /workspace
EXPOSE 8080

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/clauz3-dot-entrypoint"]
