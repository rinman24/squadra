# flotilla dev-container image — host-agnostic dev toolchain only.
#
# Deliberately carries NO project source, NO baked project venv, and NO host-specific
# tooling (no Azure CLI, no GitHub CLI). It is "language + dev tooling only" so the
# ADO -> GitHub migration (migrate-flotilla plan, Phase 2) needs no image rebuild:
#   - `az` is never installed here — the fleet-host owns board ops, not this container.
#   - `gh` arrives later as a devcontainer FEATURE (Phase 2), not a base-image layer.
#
# The project venv is the in-repo `.venv` (pyproject `venvPath="."`), created at RUNTIME
# by `uv sync --frozen` against the bind-mounted checkout — byte-identical to CI. We do
# NOT bake it: WORKDIR is the bind-mount target, so any image-baked `.venv` would be
# shadowed by the mount at runtime anyway. See .devcontainer/docker-compose.yml and
# scripts/devbox/up.sh for where the runtime sync happens.
FROM python:3.11-bookworm

# No .pyc, unbuffered stdout, longer pip timeout (parity with the gswa dev image).
# UV_PYTHON_DOWNLOADS=never -> uv uses the base image's Python 3.11, never its own.
# UV_PROJECT_ENVIRONMENT is intentionally UNSET so uv targets the in-repo `.venv`.
ENV PYTHONUNBUFFERED=TRUE \
    PYTHONDONTWRITEBYTECODE=TRUE \
    PIP_DEFAULT_TIMEOUT=100 \
    UV_PYTHON_DOWNLOADS=never \
    AZURE_CORE_COLLECT_TELEMETRY=0

# System build/dev prerequisites reused below (apt-repo setup needs curl/gnupg).
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        gnupg; \
    rm -rf /var/lib/apt/lists/*

# Install uv from its pinned, auditable distroless image (no curl-pipe-bash).
# Renovate/Dependabot's dockerfile manager bumps this tag. Matches the gswa pin.
COPY --from=ghcr.io/astral-sh/uv:0.11.21@sha256:ff07b86af50d4d9391d9daf4ff89ce427bc544f9aae87057e69a1cc0aa369946 /uv /uvx /bin/

# Dev toolchain (NO az, NO gh — see header):
#   - nodejs 20 : `pyright` is a pip wrapper that uses the GLOBAL node when present and
#                 otherwise DOWNLOADS node on first run; installing node 20 (the version
#                 the toolchain validates pyright against) keeps `uv run pyright` offline.
#   - tmux      : the fleet drives runner panes over tmux; dev parity.
#   - jq        : JSON tooling for shell hooks / skills.
#   - sudo      : passwordless for `dev` (the non-root + skip-permissions posture below).
#   - git       : pulled from bookworm-backports for a current build (the stable bookworm
#                 git is old enough to nag on some hosts). Host-agnostic — git is git.
# Sources are pinned via signed keyrings (signed-by=), TLS verified (no curl -k).
RUN set -eux; \
    mkdir -p /etc/apt/keyrings; \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg; \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list; \
    echo "deb http://deb.debian.org/debian bookworm-backports main" > /etc/apt/sources.list.d/backports.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        nodejs \
        tmux \
        jq \
        sudo; \
    apt-get install -y --no-install-recommends -t bookworm-backports \
        git; \
    rm -rf /var/lib/apt/lists/*

# Dev-only git ergonomics: a plain `git push` on a new branch auto-creates the same-named
# upstream instead of erroring. push.default stays `simple`, so a push still only targets
# the same-named upstream — friction removal, not a guardrail change. System-wide so it
# holds on every entry path (raw `docker compose up`, VS Code attach, `docker exec`).
RUN git config --system push.autoSetupRemote true

# Non-root user `dev` (uid/gid 1000). REQUIRED, not cosmetic: Claude Code refuses
# `--dangerously-skip-permissions` as root (uid 0), which is the unattended posture this
# image targets. uid 1000 aligns with the host's repo owner so the /workspaces/flotilla
# bind mount needs no chown (chowning a bind mount would mutate the host tree). The
# `.claude` mountpoint is pre-created owned by `dev` so the named volume mounted over it
# (docker-compose.yml) inherits dev ownership instead of root:root. Passwordless sudo is
# the accepted trade-off for the skip-permissions posture (mirrors gswa ADR-0005).
RUN groupadd -g 1000 dev \
    && useradd -u 1000 -g 1000 -m -s /bin/bash dev \
    && echo 'dev ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/dev \
    && chmod 0440 /etc/sudoers.d/dev \
    && install -d -o dev -g dev -m 0700 /home/dev/.claude

# Point `dev`'s tmux config at the tracked devbox conf. The target resolves at runtime
# via the /workspaces/flotilla bind mount, so editing scripts/devbox/tmux.conf takes
# effect on the next tmux server start with no rebuild. A dangling link at build time is
# fine — the bind mount supplies the file at runtime.
RUN ln -s /workspaces/flotilla/scripts/devbox/tmux.conf /home/dev/.tmux.conf \
    && chown -h dev:dev /home/dev/.tmux.conf

# Bake `dev`'s git defaults so they survive on every launch path: trust the bind-mounted
# workspace despite a possible uid mismatch, and disable commit gpg-signing (no key
# in-container). Run as `dev` (HOME=/home/dev) so they land in /home/dev/.gitconfig.
RUN su dev -c 'git config --global safe.directory /workspaces/flotilla' \
    && su dev -c 'git config --global commit.gpgsign false'

# Install Claude Code via Anthropic's native installer, as `dev`. NOT `npm install -g`:
# that lands in a root-owned global dir where the non-root runtime user's background
# auto-updater cannot write. The native installer drops the binary in /home/dev/.local
# (owned by `dev`) and self-updates on the `latest` channel. ~/.local lives in the image
# layer, so it is not shadowed by the `.claude` named volume mounted at runtime. PATH puts
# ~/.local/bin first so `claude` resolves on every entry path.
ENV PATH=/home/dev/.local/bin:${PATH}
RUN su dev -c 'curl -fsSL https://claude.ai/install.sh | bash'

# The bind-mount target. No source is COPYed in (host-agnostic image, see header); the
# checkout arrives at runtime via the bind mount and the in-repo `.venv` is created then.
WORKDIR /workspaces/flotilla

# Drop to the non-root user — the FINAL directive so it is the default for every entry
# path (`docker exec` without -u, VS Code "Reopen in Container", the compose `command`).
# devcontainer.json also sets "remoteUser": "dev" as belt-and-suspenders.
USER dev
