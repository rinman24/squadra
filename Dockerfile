# squadra dev-container image — host-agnostic dev toolchain only.
#
# Deliberately carries NO project source, NO baked project venv, and NO Azure CLI —
# `az` is never installed here; the fleet-host owns board ops, not this container.
# `gh` IS baked (below): it originally arrived as a devcontainer feature, but billet
# brings this container up with raw `docker compose` and never applies features, so
# anything the container needs day-to-day must live in the image or postCreateCommand.
#
# The image also runs an in-container sshd so billet reaches this repo as a Workspace
# via ProxyJump (see .devcontainer/sshd.conf + dev-entrypoint.sh, from billet's
# templates/workspace/ adoption kit).
#
# The project venv is the in-repo `.venv` (pyproject `venvPath="."`), created at RUNTIME
# by `uv sync --frozen` against the bind-mounted checkout — byte-identical to CI. We do
# NOT bake it: WORKDIR is the bind-mount target, so any image-baked `.venv` would be
# shadowed by the mount at runtime anyway. See .devcontainer/docker-compose.yml and
# scripts/devbox/up.sh for where the runtime sync happens.
FROM python:3.11-bookworm

# No .pyc, unbuffered stdout, longer pip timeout.
# UV_PYTHON_DOWNLOADS=never -> uv uses the base image's Python 3.11, never its own.
# UV_PROJECT_ENVIRONMENT is intentionally UNSET so uv targets the in-repo `.venv`.
ENV PYTHONUNBUFFERED=TRUE \
    PYTHONDONTWRITEBYTECODE=TRUE \
    PIP_DEFAULT_TIMEOUT=100 \
    UV_PYTHON_DOWNLOADS=never \
    AZURE_CORE_COLLECT_TELEMETRY=0 \
    # uv's cache (container overlay fs) and the in-repo .venv (bind mount) live on
    # different filesystems, so the default hardlink mode falls back to copy with a
    # warning on every `uv sync`. Pin copy mode to silence it. Dev-image only — CI runs
    # uv directly on the runner, not this image.
    UV_LINK_MODE=copy

# System build/dev prerequisites reused below (apt-repo setup needs curl/gnupg).
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        gnupg; \
    rm -rf /var/lib/apt/lists/*

# Install uv from its pinned, auditable distroless image (no curl-pipe-bash).
# Renovate/Dependabot's dockerfile manager bumps this tag.
COPY --from=ghcr.io/astral-sh/uv:0.11.21@sha256:ff07b86af50d4d9391d9daf4ff89ce427bc544f9aae87057e69a1cc0aa369946 /uv /uvx /bin/

# Dev toolchain (NO az — see header):
#   - nodejs 20        : `pyright` is a pip wrapper that uses the GLOBAL node when present
#                        and otherwise DOWNLOADS node on first run; installing node 20 (the
#                        version the toolchain validates pyright against) keeps
#                        `uv run pyright` offline.
#   - tmux             : the fleet drives runner panes over tmux; dev parity.
#   - jq               : JSON tooling for shell hooks / skills.
#   - sudo             : passwordless for `dev` (the non-root + skip-permissions posture
#                        below); also lets the entrypoint start the system sshd.
#   - openssh-server   : the in-container sshd billet connects through (loopback-only,
#                        hardened by .devcontainer/sshd.conf).
#   - openssh-client   : outbound ssh/agent tooling for agent-forwarded git.
#   - gh               : GitHub CLI, baked because billet never applies devcontainer
#                        features (raw compose) — see header.
#   - git              : pulled from bookworm-backports for a current build (the stable
#                        bookworm git is old enough to nag on some hosts).
# Sources are pinned via signed keyrings (signed-by=), TLS verified (no curl -k).
RUN set -eux; \
    mkdir -p /etc/apt/keyrings; \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg; \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list; \
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /etc/apt/keyrings/githubcli-archive-keyring.gpg; \
    echo "deb [signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list; \
    echo "deb http://deb.debian.org/debian bookworm-backports main" > /etc/apt/sources.list.d/backports.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        nodejs \
        tmux \
        jq \
        sudo \
        openssh-server \
        openssh-client \
        gh; \
    apt-get install -y --no-install-recommends -t bookworm-backports \
        git; \
    rm -rf /var/lib/apt/lists/*

# Code intelligence: pyright-langserver for Claude Code's pyright-lsp plugin (enabled in
# ~/.claude via the chezmoi dotfiles bootstrap). The plugin has no path setting, so the
# binary must be on PATH; Node is already installed above, so a root `npm install -g` puts
# pyright-langserver in /usr/bin — on PATH for the runtime `dev` user. Pinned to the
# exact version in uv.lock (1.1.410) so the editor's pyright matches the repo's
# `uv run pyright` — the authoritative gate — and never diverges from it.
RUN npm install -g pyright@1.1.410 \
    && pyright-langserver --help >/dev/null 2>&1 || true

# NeoVim (dev-only editor). bookworm's apt nvim is 0.7 — too old for the modern
# plugin/LSP ecosystem (lazy.nvim, nvim-lspconfig, treesitter want 0.9+), so pull the
# pinned v0.12.4 release tarball instead of apt and verify its SHA-256 — the same
# digest-pinned reproducibility as the uv image above.
# Editor config (init.lua, plugins) stays with the chezmoi dotfiles bootstrap, not baked
# here: the same install-here / configure-in-dotfiles split as pyright above. x86_64 only
# — the Azure Host build/deploy target is amd64; an arm64 build would need the
# nvim-linux-arm64.tar.gz asset and its own digest.
RUN set -eux; \
    curl -fsSL -o /tmp/nvim.tar.gz \
        https://github.com/neovim/neovim/releases/download/v0.12.4/nvim-linux-x86_64.tar.gz; \
    echo "012bf3fcac5ade43914df3f174668bf64d05e049a4f032a388c027b1ebd78628  /tmp/nvim.tar.gz" | sha256sum -c -; \
    tar -C /opt -xzf /tmp/nvim.tar.gz; \
    ln -s /opt/nvim-linux-x86_64/bin/nvim /usr/local/bin/nvim; \
    rm /tmp/nvim.tar.gz; \
    nvim --version | head -1

# Dev-only git ergonomics: a plain `git push` on a new branch auto-creates the same-named
# upstream instead of erroring. push.default stays `simple`, so a push still only targets
# the same-named upstream — friction removal, not a guardrail change. System-wide so it
# holds on every entry path (raw `docker compose up`, VS Code attach, `docker exec`).
RUN git config --system push.autoSetupRemote true

# Non-root user `dev` (uid/gid 1000). REQUIRED, not cosmetic: Claude Code refuses
# `--dangerously-skip-permissions` as root (uid 0), which is the unattended posture this
# image targets. uid 1000 aligns with the host's repo owner so the /workspace
# bind mount needs no chown (chowning a bind mount would mutate the host tree). The
# `.claude` mountpoint is pre-created owned by `dev` so the named volume mounted over it
# (docker-compose.yml) inherits dev ownership instead of root:root. Passwordless sudo is
# the accepted trade-off for the skip-permissions posture.
RUN groupadd -g 1000 dev \
    && useradd -u 1000 -g 1000 -m -s /bin/bash dev \
    && echo 'dev ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/dev \
    && chmod 0440 /etc/sudoers.d/dev \
    && install -d -o dev -g dev -m 0700 /home/dev/.claude \
    # .ssh pre-created 0700 dev-owned so the runtime authorized_keys bind mount
    # (docker-compose.yml) lands StrictModes-clean instead of in a root-owned dir.
    && install -d -o dev -g dev -m 0700 /home/dev/.ssh

# Outbound SSH for `dev`: with billet's agent forwarding, keyless git over SSH works
# inside the container. accept-new records GitHub's host key on first contact instead of
# an interactive prompt that would hang a scripted push.
RUN printf 'Host github.com\n    StrictHostKeyChecking accept-new\n' > /home/dev/.ssh/config \
    && chown dev:dev /home/dev/.ssh/config \
    && chmod 0644 /home/dev/.ssh/config

# In-container sshd hardening (key-only, non-root, dev-only, agent-forwarding). Debian's
# stock sshd_config Includes /etc/ssh/sshd_config.d/*.conf; runtime host keys come from a
# named volume (see .devcontainer/dev-entrypoint.sh + docker-compose.yml). Copied as root
# (before USER dev) so it lands under /etc.
COPY .devcontainer/sshd.conf /etc/ssh/sshd_config.d/squadra.conf

# Point `dev`'s tmux config at the tracked devbox conf. The target resolves at runtime
# via the /workspace bind mount, so editing scripts/devbox/tmux.conf takes
# effect on the next tmux server start with no rebuild. A dangling link at build time is
# fine — the bind mount supplies the file at runtime.
RUN ln -s /workspace/scripts/devbox/tmux.conf /home/dev/.tmux.conf \
    && chown -h dev:dev /home/dev/.tmux.conf

# Bake `dev`'s git defaults so they survive on every launch path: trust the bind-mounted
# workspace despite a possible uid mismatch, and disable commit gpg-signing (no key
# in-container). Run as `dev` (HOME=/home/dev) so they land in /home/dev/.gitconfig.
RUN su dev -c 'git config --global safe.directory /workspace' \
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
WORKDIR /workspace

# Drop to the non-root user — the FINAL directive so it is the default for every entry
# path (`docker exec` without -u, VS Code "Reopen in Container", the compose `command`).
# devcontainer.json also sets "remoteUser": "dev" as belt-and-suspenders.
USER dev
