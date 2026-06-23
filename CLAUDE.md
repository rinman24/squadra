# flotilla — project instructions

flotilla is the packaged, reusable extraction of the AFK vertical-slice fleet
(deterministic supervisor + per-slice runner machinery) originally built in the
`gswa` backend under ADR-0007. It drives an unattended, board-driven Claude
implementation fleet against a **target repository** identified by `FLEET_HOME`.

See `README.md` for how the fleet works and how to run it.

## Stack

- Python ≥ 3.11, **pure standard library** at runtime (no third-party deps).
- Build: **uv + Hatchling**, PEP 621 `[project]`, **src layout** (`src/flotilla/`).
- Tooling: **ruff** (lint) + **pyright (strict)** + **pytest**, all via `uv run`.
- Console scripts: `flotilla` — the unified argparse CLI
  (`init`/`tick`/`start`/`stop`/`status`/`log` + `flotilla slice
  {init|update|heartbeat|show}`); `flotilla-status` is kept as a **deprecated
  alias** until the coupled gswa PR lands. `python -m flotilla.supervisor` and
  `python -m flotilla.status` remain as internal module entry points.

## Layout

```
src/flotilla/
├── __init__.py
├── constants.py        # env-tunable fleet constants (single source of truth)
├── domain.py           # provider-neutral model: Lifecycle, WorkItem, CommentEvent, Tags
├── config.py           # FlotillaConfig + tomllib loader (defaults < toml < env < flag)
├── board.py            # BoardAccess seam + AzCliAdo adapter + provider registry
├── engines.py          # pure claim/reap/finalize/naming decisions (no I/O)
├── status.py           # per-slice status.json convention + ops (`flotilla slice`)
├── supervisor.py       # the deterministic, token-free tick (python -m flotilla.supervisor)
├── cli.py              # unified argparse `flotilla` — the API / composition root
├── _resources.py       # resolve packaged shell glue via importlib.resources (+chmod +x)
└── _scripts/           # PACKAGE DATA: runner-wrap.sh, fleet-tick.sh,
                        #   fleetctl.sh
tests/                  # unit tests + BoardAccess contract suite +
                        #   tests/scripts/run-runner-wrap-tests.sh (hermetic)
```

## Hard invariants

- **Packaged-script resolution.** Anything that invokes the shell glue (the
  supervisor's `SandboxAccess` launch, the `flotilla` CLI's ticker subcommands,
  the tick entry point) MUST resolve it via `flotilla._resources.resolve_script(...)`
  (`importlib.resources.files("flotilla")/"_scripts"`, `chmod +x` on resolve) —
  **never** a path relative to `FLEET_HOME`. The package is not the working repo.
- **`FLEET_HOME`** = the repo the fleet operates on (default: cwd). No hardcoded
  default path.
- **`FLEET_PYTHON`** = an interpreter that has flotilla installed. The supervisor
  injects its own `sys.executable` into runner panes; shells default to `python3`.
- **Strong typing everywhere** — every file must pass `pyright` (strict) with 0
  errors. Modern syntax: `X | None`, `list[X]`, `-> None` always explicit.
- **No silent permission/behavior broadening** in the fleet's claim/reap/finalize
  logic — it is security-sensitive board automation.

## Validation (before hand-off)

```bash
uv run ruff check .            # 0 errors
uv run ruff format --check .   # no reformats pending
uv run pyright                 # strict, 0 errors
uv run pytest                  # unit + hermetic shell tests
```

The hermetic shell test (`tests/scripts/run-runner-wrap-tests.sh`) needs `bash`;
it runs against a stubbed `claude` and a temp fleet root (no network/ADO/tmux).

## Git & PR conventions

- Type-prefixed commit subjects (`feat:`, `refactor:`, `test:`, `fix:`,
  `docs:`, `ci:` …); each commit a coherent, self-contained unit (no WIP).
- Push to Azure DevOps over **HTTPS + PAT** (`AZURE_DEVOPS_EXT_PAT`); this
  container has no SSH key. Commit with `commit.gpgsign=false`.
- **No Claude/Anthropic authorship trailers** on commits, PR bodies, or tags.
- PR descriptions render **Markdown**; ADO work-item fields render **HTML**.
- `main` is gated by build validation (the CI pipeline) + require-PR.
