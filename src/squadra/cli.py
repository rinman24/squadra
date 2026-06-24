"""squadra — the unified ``squadra`` CLI (the API / composition root).

This is the single argparse-native entry point that folds every fleet operation
into one surface (ADR-0001 decision 3), superseding ADR-0007's thin
``fleetctl.sh``-execing shim and the separate ``squadra-supervisor`` /
``squadra-status`` console scripts:

- ``squadra init`` — scaffold an annotated ``squadra.toml`` plus the
  consumer-owned runner/cleanup skill templates (via :mod:`squadra.scaffold`),
  and optionally validate the config against the live board (``--check``).
- ``squadra tick`` — run one supervisor tick in-process (no shell). The
  supervisor (and, transitively, the board adapter) is imported lazily inside
  the handler so importing this module for ``squadra init`` / ``squadra slice``
  stays lean and free of the heavyweight tick dependencies.
- ``squadra fleet-tick`` — the fleet-host tick entry (ADR-0002 §11): fetch the
  PAT + ``ANTHROPIC_API_KEY`` from Key Vault via the VM managed identity and
  apply them to this process's env, ensure ``FLEET_HOME`` is a current checkout,
  then run one tick in-process. This is the ``ExecStart`` of the systemd
  ``squadra.service``.
- ``squadra install-units`` — render the packaged systemd unit templates against
  the host's paths/vault/cadence and write them to the systemd unit directory
  (does **not** enable the timer — fleet activation stays a deliberate step).
- ``squadra start | stop | status | log`` — tmux ticker control for hands-on
  local/dev runs; these shell to the packaged ``fleetctl.sh`` resolved from the
  installed package data, with ``FLEET_PYTHON`` defaulted to this interpreter so
  each tick / runner reaches ``squadra.*`` regardless of what ``python3``
  resolves to on PATH. (The fleet-host uses systemd, not this.)
- ``squadra slice {init|update|heartbeat|show}`` — the per-slice
  ``status.json`` ops (the new noun replacing the ``squadra-status`` script).

``deprecated_status_main`` keeps the retired ``squadra-status`` console script
working unchanged: it warns once to stderr, then delegates to ``status.main``.
"""

import argparse
from collections.abc import Sequence
import os
from pathlib import Path
import sys

from squadra import scaffold, status
from squadra._resources import resolve_script
from squadra.config import DEFAULT_PROVIDER, ConfigError, load_config

_FLEETCTL_SUBCOMMANDS: tuple[str, ...] = ("start", "stop", "status", "log")
_SLICE_SUBCOMMANDS: tuple[str, ...] = ("init", "update", "heartbeat", "show")


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch the ``squadra`` CLI; return a process exit code.

    Parameters
    ----------
    argv
        The argument vector to parse (defaults to ``sys.argv[1:]``).

    Returns
    -------
    int
        The exit code. The ``start``/``stop``/``status``/``log`` path may
        replace this process via :func:`os.execvpe` and not return at all.
    """
    args: list[str] = list(sys.argv[1:] if argv is None else argv)
    parser, known = _build_parser()
    namespace, extra = parser.parse_known_args(args)
    command: str | None = namespace.command
    if command is None:
        parser.print_help(sys.stderr)
        return 2
    # Dispatch tables keep this within the return-count budget: namespace-driven
    # handlers vs pass-through (`extra`) handlers, then the special cases.
    namespace_handlers = {"init": _cmd_init, "install-units": _cmd_install_units}
    extra_handlers = {"tick": _cmd_tick, "fleet-tick": _cmd_fleet_tick}
    if command in namespace_handlers:
        return namespace_handlers[command](namespace)
    if command in extra_handlers:
        return extra_handlers[command](extra)
    if command in known:
        return _cmd_fleetctl(command, extra)
    if command == "slice":
        return _cmd_slice(namespace, extra)
    parser.print_help(sys.stderr)
    return 2


def deprecated_status_main(argv: Sequence[str] | None = None) -> int:
    """Back-compat shim for the retired ``squadra-status`` console script.

    Prints a one-line deprecation notice to stderr, then delegates to
    :func:`squadra.status.main` so existing callers keep working unchanged.
    """
    print(
        "squadra-status is deprecated; use `squadra slice ...`",
        file=sys.stderr,
    )
    return status.main(argv)


def _build_parser() -> tuple[argparse.ArgumentParser, frozenset[str]]:
    """Build the top-level argparse tree and the set of fleetctl subcommands."""
    parser = argparse.ArgumentParser(
        prog="squadra",
        description="Drive the board-driven Claude implementation fleet (ADR-0001).",
    )
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser(
        "init", help="scaffold squadra.toml + the runner/cleanup skill templates"
    )
    init_parser.add_argument(
        "--provider",
        default=DEFAULT_PROVIDER,
        help=f"board provider to scaffold for (default: {DEFAULT_PROVIDER})",
    )
    init_parser.add_argument(
        "--fleet-home",
        type=Path,
        default=None,
        help="repo squadra operates on; squadra.toml is written here (default: cwd)",
    )
    init_parser.add_argument(
        "--force", action="store_true", help="overwrite existing files instead of skipping them"
    )
    init_parser.add_argument(
        "--check",
        action="store_true",
        help="validate the scaffolded config against the live board",
    )

    subparsers.add_parser("tick", help="run one supervisor tick in-process (flags pass through)")
    subparsers.add_parser(
        "fleet-tick",
        help="fleet-host tick: fetch KV secrets + sync the app repo, then tick (flags pass through)",
    )

    install = subparsers.add_parser(
        "install-units",
        help="render + install the fleet-host systemd units (does not enable the timer)",
    )
    install.add_argument("--key-vault", required=True, help="Key Vault name fleet-tick reads from")
    install.add_argument(
        "--fleet-home",
        type=Path,
        required=True,
        help="the app-repo checkout the supervisor operates on (WorkingDirectory)",
    )
    install.add_argument(
        "--venv-bin",
        type=Path,
        default=None,
        help="the fleet venv bin dir (default: this interpreter's directory)",
    )
    install.add_argument(
        "--fleet-root",
        type=Path,
        default=None,
        help="fleet state dir (default: <fleet-home>/.claude/fleet)",
    )
    install.add_argument(
        "--app-repo-url", default="", help="remote fleet-tick keeps fleet-home synced to"
    )
    install.add_argument(
        "--parent-scope-ids", default="", help="comma-separated Epic/Issue claim filter"
    )
    install.add_argument(
        "--interval-seconds", type=int, default=None, help="timer cadence (default: 180)"
    )
    install.add_argument("--user", default=None, help="service account (default: azureuser)")
    install.add_argument(
        "--dest",
        type=Path,
        default=None,
        help="systemd unit directory to write into (default: /etc/systemd/system)",
    )

    for name in _FLEETCTL_SUBCOMMANDS:
        subparsers.add_parser(name, help=f"ticker control: {name} (shells to fleetctl.sh)")
    subparsers.add_parser("slice", help="per-slice status.json ops {init|update|heartbeat|show}")

    return parser, frozenset(_FLEETCTL_SUBCOMMANDS)


def _cmd_init(args: argparse.Namespace) -> int:
    """Scaffold ``squadra.toml`` + skill templates and, with ``--check``, validate.

    Delegates the scaffolding to :func:`squadra.scaffold.init_project` (the
    additive module that owns the annotated ``squadra.toml`` + the consumer-owned
    runner/cleanup skill templates). Existing files are skipped (re-runnable),
    not an error, unless ``--force`` overwrites them.

    Parameters
    ----------
    args
        The parsed ``init`` namespace (``provider``/``fleet_home``/``force``/
        ``check``).

    Returns
    -------
    int
        ``0`` on success; non-zero only on a failed ``--check``.
    """
    provider: str = args.provider
    fleet_home: Path = args.fleet_home if args.fleet_home is not None else Path.cwd()

    results: dict[str, bool] = scaffold.init_project(
        fleet_home, provider=provider, force=args.force
    )
    for name, written in results.items():
        status_word: str = "wrote" if written else "skipped (exists; use --force)"
        print(f"squadra: {status_word} {fleet_home / name}")
    print(
        "next steps: review squadra.toml + the scaffolded skill templates (set "
        "provider / [board.states] / parent_scope_ids), then run `squadra init "
        "--check` to validate against your board and `squadra start` to run the ticker."
    )

    if args.check:
        return _check_config(fleet_home, provider)
    return 0


def _check_config(fleet_home: Path, provider: str) -> int:
    """Build the provider and validate the resolved config against the board."""
    # Imported here (not at module top) so this remains the only place that
    # pulls in the board adapter; keeps `squadra slice` / `init` lean and lets
    # tests stub the seam without a live az CLI.
    from squadra.board import BoardValidationError, build_board  # noqa: PLC0415

    try:
        board = build_board(load_config(fleet_home=fleet_home, provider=provider))
        board.validate_config()
    except (BoardValidationError, ConfigError) as exc:
        print(f"squadra: {exc}", file=sys.stderr)
        return 1
    print("squadra: config OK")
    return 0


def _cmd_tick(extra: Sequence[str]) -> int:
    """Run one supervisor tick in-process, forwarding ``extra`` to the supervisor.

    The supervisor is imported lazily here (never at module load) so importing
    :mod:`squadra.cli` for ``init``/``slice`` stays free of the tick's board
    adapter + tmux dependencies.
    """
    from squadra import supervisor  # noqa: PLC0415

    return supervisor.main(list(extra))


def _cmd_fleet_tick(extra: Sequence[str]) -> int:
    """Run one fleet-host tick: bootstrap KV secrets + sync the app repo, then tick.

    This is the systemd ``squadra.service`` ``ExecStart`` (ADR-0002 §11): it
    authenticates as the VM managed identity, reads the PAT + ``ANTHROPIC_API_KEY``
    from Key Vault, applies them to *this* process's environment (never to disk),
    ensures ``FLEET_HOME`` is a current checkout, and then runs the same in-process
    tick as ``squadra tick`` (``extra`` flags — e.g. ``--dry-run`` — pass through).

    A ``--dry-run`` (or ``FLEET_DRY_RUN``) tick still fetches secrets and clones a
    missing checkout (so the smoke proves Key Vault reachability) but does not
    ``reset --hard`` an existing one.
    """
    from squadra import repo, secrets  # noqa: PLC0415

    vault: str = os.environ.get(secrets.KEY_VAULT_ENV, "").strip()
    if not vault:
        print(
            f"squadra fleet-tick: {secrets.KEY_VAULT_ENV} is not set; cannot fetch "
            "secrets (this entry is for the Key-Vault-backed fleet-host).",
            file=sys.stderr,
        )
        return 2

    try:
        access = secrets.AzKeyVaultSecrets(vault)
        fleet_secrets = secrets.load_fleet_secrets(access, secrets.secret_names_from_env())
    except secrets.SecretFetchError as exc:
        print(f"squadra fleet-tick: {exc}", file=sys.stderr)
        return 1
    secrets.apply_supervisor_environ(fleet_secrets)

    dry_run: bool = "--dry-run" in extra or _env_truthy("FLEET_DRY_RUN")
    synced: bool | None = repo.ensure_app_repo_from_env(mutate=not dry_run)
    if synced is False:
        print("squadra fleet-tick: failed to sync the app repo (FLEET_HOME)", file=sys.stderr)
        return 1

    from squadra import supervisor  # noqa: PLC0415

    return supervisor.main(list(extra))


def _cmd_install_units(args: argparse.Namespace) -> int:
    """Render the packaged systemd units against the host and write them to disk.

    Writing the units neither reloads systemd nor enables the timer — those stay
    deliberate operator steps (``systemctl daemon-reload``; then, only when
    activating the fleet, ``systemctl enable --now squadra.timer``).
    """
    from squadra.units import (  # noqa: PLC0415
        DEFAULT_INTERVAL_SECONDS,
        DEFAULT_SYSTEMD_DIR,
        DEFAULT_USER,
        UnitContext,
        install_units,
    )

    fleet_home: Path = args.fleet_home
    venv_bin: Path = args.venv_bin if args.venv_bin is not None else Path(sys.executable).parent
    fleet_root: Path = (
        args.fleet_root if args.fleet_root is not None else fleet_home / ".claude" / "fleet"
    )
    ctx = UnitContext(
        venv_bin=venv_bin,
        fleet_home=fleet_home,
        fleet_root=fleet_root,
        key_vault=args.key_vault,
        app_repo_url=args.app_repo_url,
        parent_scope_ids=args.parent_scope_ids,
        interval_seconds=(
            args.interval_seconds if args.interval_seconds is not None else DEFAULT_INTERVAL_SECONDS
        ),
        user=args.user if args.user is not None else DEFAULT_USER,
    )
    dest: Path = args.dest if args.dest is not None else DEFAULT_SYSTEMD_DIR
    written: list[Path] = install_units(ctx, dest=dest)
    for path in written:
        print(f"squadra: wrote {path}")
    print(
        "next steps: `sudo systemctl daemon-reload`, smoke a dry-run "
        "(`systemd-run --wait --pipe squadra fleet-tick --dry-run`), then activate "
        "with `sudo systemctl enable --now squadra.timer`."
    )
    return 0


def _env_truthy(name: str) -> bool:
    """Return whether env var ``name`` is set to a truthy value (1/true/yes/on)."""
    raw: str = os.environ.get(name, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _cmd_fleetctl(command: str, extra: Sequence[str]) -> int:
    """Shell to the packaged ``fleetctl.sh`` for a ticker control subcommand.

    Resolves the script from the installed package data and execs ``bash`` with
    ``FLEET_PYTHON`` defaulted to this interpreter (so each tick / runner reaches
    ``squadra.*``). On success ``os.execvpe`` replaces this process and does not
    return; a missing ``bash`` returns exit code ``127``.
    """
    script: str = str(resolve_script("fleetctl.sh"))
    env: dict[str, str] = dict(os.environ)
    env.setdefault("FLEET_PYTHON", sys.executable)
    try:
        os.execvpe("bash", ["bash", script, command, *extra], env)
    except OSError as exc:  # bash missing / not runnable
        print(f"squadra: failed to run {script}: {exc}", file=sys.stderr)
        return 127


def _cmd_slice(args: argparse.Namespace, extra: Sequence[str]) -> int:
    """Delegate a ``slice`` subcommand to the per-slice ``status.json`` CLI.

    The first positional after ``slice`` is the status subcommand
    (``init``/``update``/``heartbeat``/``show``); everything else passes through
    to :func:`squadra.status.main` unchanged.
    """
    if not extra:
        print(
            f"squadra slice: expected a subcommand {list(_SLICE_SUBCOMMANDS)}",
            file=sys.stderr,
        )
        return 2
    return status.main(list(extra))


if __name__ == "__main__":
    raise SystemExit(main())
