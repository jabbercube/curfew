FROM mcr.microsoft.com/devcontainers/python:1-3.13-bookworm

# uv (binary copy — same approach as api/Dockerfile)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# just (the command runner)
RUN curl --proto '=https' --tlsv1.2 -sSf https://just.systems/install.sh \
    | bash -s -- --to /usr/local/bin

# sqlite3 CLI for inspecting state.sqlite during development
RUN apt-get update \
    && apt-get install -y --no-install-recommends sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Node.js + bun for the web/ SPA. Node provides the runtime that vitest /
# eslint shell out to; bun is the package manager + script runner.
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://bun.sh/install | bash \
    && cp /root/.bun/bin/bun /usr/local/bin/bun
