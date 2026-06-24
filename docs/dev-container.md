# squadra dev container

squadra runs in its own dev container — a self-contained `docker compose` stack (own
image, own Claude-home volume, no database, no ports). It is **container-scoped**: the
host scripts never create, start, or deallocate the VM they run on.

## What you get

- `python:3.11-bookworm` + `uv` (pinned), `ruff` / `pyright` (strict) / `pytest`, plus
  `git`, `tmux`, `jq`, Node 20 (so `pyright` runs offline), and the `claude` CLI.
- A non-root `dev` user (uid 1000) with passwordless sudo, so Claude Code's
  `--dangerously-skip-permissions` runs.
- The repo bind-mounted at `/workspaces/squadra`; the in-repo `.venv` is created at
  runtime by `uv sync` (it is not baked into the image).
- A persistent `squadra_claude_home` volume for Claude auth + memory.

The base image is deliberately **host-agnostic** — no Azure CLI, no project source baked
in. `gh` is layered on via a devcontainer feature
(`ghcr.io/devcontainers/features/github-cli`), not the image. That kept the ADO → GitHub
migration image-rebuild-free.

## Prerequisites

- A host (VM or workstation) with Docker Engine + the compose plugin installed, and you
  are connected to it.
- A clone of this repo on the host. If you are reading this, you have one.

## Daily driver: VS Code "Reopen in Container"

Open the squadra folder on the VM in VS Code (Remote-SSH or the same tunnel you use for
the host) and run **Dev Containers: Reopen in Container**. VS Code reads
`.devcontainer/devcontainer.json`, builds the image, starts the `squadra` service, and
runs `uv sync` (the `postCreateCommand`). You land in `/workspaces/squadra` as `dev`
with the venv ready.

## Host scripts (terminal equivalent)

Thin `docker compose` wrappers for terminal use on the VM host. They pin the compose
project name to `squadra` so they share one stack with VS Code:

```bash
scripts/devbox/up.sh        # build + start + uv sync   (--dry-run, --yes)
scripts/devbox/stop.sh      # compose down (NOT VM deallocate) (--dry-run, --yes)
scripts/devbox/rebuild.sh   # rebuild image + recreate   (--no-cache, --force-recreate, --yes, --dry-run)
```

`--dry-run` prints the exact `docker compose` commands without running them. All three
confirm before acting (skip with `--yes`).

They work with no configuration. To override a default (e.g. point at a clone elsewhere
on the host), copy the template and edit it:

```bash
cp scripts/devbox/config.example.sh scripts/devbox/config.local.sh
```

`config.local.sh` is gitignored and sourced after the tracked `config.sh`.

### Project name pinning

The compose project name is pinned to `squadra` in **both** places —
`name: squadra` in `.devcontainer/docker-compose.yml` and `-p squadra` in the scripts.
Keep them equal; otherwise VS Code and the scripts would spawn two separate stacks. With
it fixed, VS Code and the scripts share one stack and never spawn two on the same daemon.

## Claude Code auth

The first time the container is created, `squadra_claude_home` is empty. Start `claude`
inside the container and authenticate once; auth and memory then persist across rebuilds
(the volume survives `stop.sh` / `rebuild.sh`).

## Validate

Inside the container (VS Code terminal or `docker compose -p squadra exec squadra bash`):

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```

All four should be green. The hermetic shell tests need only `bash` + `tmux` and a
stubbed `claude` (no network) — all present in the image.

## Pushing to GitHub from inside the container

Pushing uses HTTPS (this container has no SSH key). Authenticate once with the `gh`
device flow:

```bash
gh auth login
```

Choose **GitHub.com** → **HTTPS** → **Login with a web browser**. `gh` prints a one-time
device code; copy it, open the displayed URL on any machine, paste the code, and
authorize. `gh` then configures git's credential helper, so `git push` and
`gh pr create` work for the rest of the session.

`gh` is present in the container via the devcontainer feature
(`ghcr.io/devcontainers/features/github-cli`). `commit.gpgsign=false` is already handled
by `postCreate`, so commits don't try to sign.

## Relationship to the VM

- `stop.sh` is `compose down` — it stops the squadra container only. It does **not**
  deallocate the VM. To stop billing for the whole box, deallocate the VM through your
  host's own tooling.
- squadra is idle unless you are testing; watch memory if you run heavy test loads.
