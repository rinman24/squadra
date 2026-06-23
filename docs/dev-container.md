# flotilla dev container

flotilla runs in its own dev container — a second `docker compose` stack on the shared
**gswa-devbox** Azure VM, fully independent of gswa's stack (own image, own Claude-home
volume, no database, no ports). It is **container-scoped**: it never creates, starts, or
deallocates the VM. VM lifecycle stays owned by gswa's `scripts/devbox/*`.

## What you get

- `python:3.11-bookworm` + `uv` (pinned), `ruff` / `pyright` (strict) / `pytest`, plus
  `git`, `tmux`, `jq`, Node 20 (so `pyright` runs offline), and the `claude` CLI.
- A non-root `dev` user (uid 1000) with passwordless sudo, so Claude Code's
  `--dangerously-skip-permissions` runs.
- The repo bind-mounted at `/workspaces/flotilla`; the in-repo `.venv` is created at
  runtime by `uv sync` (it is not baked into the image).
- A persistent `flotilla_claude_home` volume for Claude auth + memory, isolated from
  gswa's `claude_home`.

The image is deliberately **host-agnostic** — no Azure CLI, no GitHub CLI, no project
source. That is why the upcoming ADO → GitHub move needs no image rebuild.

## Prerequisites

- The **gswa-devbox** VM is up and you are connected to it. Bring it up / connect with
  gswa's `scripts/devbox/up.sh` and `connect-*.sh` (in the gswa-backend repo). Docker
  Engine + the compose plugin are installed there as part of that provisioning.
- A clone of this repo on the VM. If you are reading this, you have one.

## Daily driver: VS Code "Reopen in Container"

Open the flotilla folder on the VM in VS Code (Remote-SSH or the same tunnel you use for
gswa) and run **Dev Containers: Reopen in Container**. VS Code reads
`.devcontainer/devcontainer.json`, builds the image, starts the `flotilla` service, and
runs `uv sync` (the `postCreateCommand`). You land in `/workspaces/flotilla` as `dev`
with the venv ready.

## Host scripts (terminal equivalent)

Thin `docker compose` wrappers for terminal use on the VM host. They pin the compose
project name to `flotilla` so they share one stack with VS Code:

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

The compose project name is pinned to `flotilla` in **both** places —
`name: flotilla` in `.devcontainer/docker-compose.yml` and `-p flotilla` in the scripts.
Keep them equal; otherwise VS Code and the scripts would spawn two separate stacks. With
it fixed, flotilla's stack never collides with gswa's on the shared Docker daemon.

## Claude Code auth

The first time the container is created, `flotilla_claude_home` is empty. Start `claude`
inside the container and authenticate once; auth and memory then persist across rebuilds
(the volume survives `stop.sh` / `rebuild.sh`).

## Validate

Inside the container (VS Code terminal or `docker compose -p flotilla exec flotilla bash`):

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```

All four should be green. The hermetic shell tests need only `bash` + `tmux` and a
stubbed `claude` (no network) — all present in the image.

## Pushing to Azure DevOps from inside the container

While flotilla is still on ADO, pushing uses HTTPS + a PAT (this container has no SSH
key). Pass the PAT into the container by exporting it on the host before `up.sh` (compose
forwards `AZURE_DEVOPS_EXT_PAT`), then configure the repo-local credential helper once:

```bash
git config --local credential.helper \
  '!f() { echo "username=pat"; echo "password=${AZURE_DEVOPS_EXT_PAT}"; }; f'
```

After the GitHub migration (migrate-flotilla plan, Phase 2) this is replaced by
`gh auth login`.

## Relationship to the VM and gswa

- `stop.sh` is `compose down` — it stops the flotilla container only. It does **not**
  deallocate the VM. To stop billing for the whole box, use gswa's `scripts/devbox/stop.sh`.
- flotilla and gswa share the VM's CPU/RAM (D4s_v4: 4 vCPU / 16 GB). flotilla is idle
  unless you are testing; watch memory if you run heavy test loads in both at once.
