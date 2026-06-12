"""flotilla — the unified ``flotilla`` CLI (the API / composition root).

This is the single argparse-native entry point that folds every fleet operation
into one surface (ADR-0001 decision 3), superseding ADR-0007's thin
``fleetctl.sh``-execing shim and the separate ``flotilla-supervisor`` /
``flotilla-status`` console scripts:

- ``flotilla init`` — scaffold an annotated ``flotilla.toml`` plus the
  consumer-owned runner/cleanup skill templates (via :mod:`flotilla.scaffold`),
  and optionally validate the config against the live board (``--check``).
- ``flotilla tick`` — run one supervisor tick in-process (no shell). The
  supervisor (and, transitively, the board adapter) is imported lazily inside
  the handler so importing this module for ``flotilla init`` / ``flotilla slice``
  stays lean and free of the heavyweight tick dependencies.
- ``flotilla start | stop | status | log`` — tmux ticker control; these shell to
  the packaged ``fleetctl.sh`` resolved from the installed package data, with
  ``FLEET_PYTHON`` defaulted to this interpreter so each tick / runner reaches
  ``flotilla.*`` regardless of what ``python3`` resolves to on PATH.
- ``flotilla slice {init|update|heartbeat|show}`` — the per-slice
  ``status.json`` ops (the new noun replacing the ``flotilla-status`` script).

``deprecated_status_main`` keeps the retired ``flotilla-status`` console script
working unchanged: it warns once to stderr, then delegates to ``status.main``.
"""

import argparse
from collections.abc import Sequence
import os
from pathlib import Path
import sys

from flotilla import scaffold, status
from flotilla._resources import resolve_script
from flotilla.config import DEFAULT_PROVIDER, ConfigError, load_config

_FLEETCTL_SUBCOMMANDS: tuple[str, ...] = ("start", "stop", "status", "log")
_SLICE_SUBCOMMANDS: tuple[str, ...] = ("init", "update", "heartbeat", "show")


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch the ``flotilla`` CLI; return a process exit code.

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
    if command == "init":
        return _cmd_init(namespace)
    if command == "tick":
        return _cmd_tick(extra)
    if command in known:
        return _cmd_fleetctl(command, extra)
    if command == "slice":
        return _cmd_slice(namespace, extra)
    parser.print_help(sys.stderr)
    return 2


def deprecated_status_main(argv: Sequence[str] | None = None) -> int:
    """Back-compat shim for the retired ``flotilla-status`` console script.

    Prints a one-line deprecation notice to stderr, then delegates to
    :func:`flotilla.status.main` so existing callers keep working unchanged.
    """
    print(
        "flotilla-status is deprecated; use `flotilla slice ...`",
        file=sys.stderr,
    )
    return status.main(argv)


def _build_parser() -> tuple[argparse.ArgumentParser, frozenset[str]]:
    """Build the top-level argparse tree and the set of fleetctl subcommands."""
    parser = argparse.ArgumentParser(
        prog="flotilla",
        description="Drive the board-driven Claude implementation fleet (ADR-0001).",
    )
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser(
        "init", help="scaffold flotilla.toml + the runner/cleanup skill templates"
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
        help="repo flotilla operates on; flotilla.toml is written here (default: cwd)",
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
    for name in _FLEETCTL_SUBCOMMANDS:
        subparsers.add_parser(name, help=f"ticker control: {name} (shells to fleetctl.sh)")
    subparsers.add_parser("slice", help="per-slice status.json ops {init|update|heartbeat|show}")

    return parser, frozenset(_FLEETCTL_SUBCOMMANDS)


def _cmd_init(args: argparse.Namespace) -> int:
    """Scaffold ``flotilla.toml`` + skill templates and, with ``--check``, validate.

    Delegates the scaffolding to :func:`flotilla.scaffold.init_project` (the
    additive module that owns the annotated ``flotilla.toml`` + the consumer-owned
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
        print(f"flotilla: {status_word} {fleet_home / name}")
    print(
        "next steps: review flotilla.toml + the scaffolded skill templates (set "
        "provider / [board.states] / parent_scope_ids), then run `flotilla init "
        "--check` to validate against your board and `flotilla start` to run the ticker."
    )

    if args.check:
        return _check_config(fleet_home, provider)
    return 0


def _check_config(fleet_home: Path, provider: str) -> int:
    """Build the provider and validate the resolved config against the board."""
    # Imported here (not at module top) so this remains the only place that
    # pulls in the board adapter; keeps `flotilla slice` / `init` lean and lets
    # tests stub the seam without a live az CLI.
    from flotilla.board import BoardValidationError, build_board  # noqa: PLC0415

    try:
        board = build_board(load_config(fleet_home=fleet_home, provider=provider))
        board.validate_config()
    except (BoardValidationError, ConfigError) as exc:
        print(f"flotilla: {exc}", file=sys.stderr)
        return 1
    print("flotilla: config OK")
    return 0


def _cmd_tick(extra: Sequence[str]) -> int:
    """Run one supervisor tick in-process, forwarding ``extra`` to the supervisor.

    The supervisor is imported lazily here (never at module load) so importing
    :mod:`flotilla.cli` for ``init``/``slice`` stays free of the tick's board
    adapter + tmux dependencies.
    """
    from flotilla import supervisor  # noqa: PLC0415

    return supervisor.main(list(extra))


def _cmd_fleetctl(command: str, extra: Sequence[str]) -> int:
    """Shell to the packaged ``fleetctl.sh`` for a ticker control subcommand.

    Resolves the script from the installed package data and execs ``bash`` with
    ``FLEET_PYTHON`` defaulted to this interpreter (so each tick / runner reaches
    ``flotilla.*``). On success ``os.execvpe`` replaces this process and does not
    return; a missing ``bash`` returns exit code ``127``.
    """
    script: str = str(resolve_script("fleetctl.sh"))
    env: dict[str, str] = dict(os.environ)
    env.setdefault("FLEET_PYTHON", sys.executable)
    try:
        os.execvpe("bash", ["bash", script, command, *extra], env)
    except OSError as exc:  # bash missing / not runnable
        print(f"flotilla: failed to run {script}: {exc}", file=sys.stderr)
        return 127


def _cmd_slice(args: argparse.Namespace, extra: Sequence[str]) -> int:
    """Delegate a ``slice`` subcommand to the per-slice ``status.json`` CLI.

    The first positional after ``slice`` is the status subcommand
    (``init``/``update``/``heartbeat``/``show``); everything else passes through
    to :func:`flotilla.status.main` unchanged.
    """
    if not extra:
        print(
            f"flotilla slice: expected a subcommand {list(_SLICE_SUBCOMMANDS)}",
            file=sys.stderr,
        )
        return 2
    return status.main(list(extra))


if __name__ == "__main__":
    raise SystemExit(main())
