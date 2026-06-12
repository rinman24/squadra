"""Fleet status-file convention.

One ``status.json`` per slice at ``<fleet-root>/<issue-id>/status.json`` is the
micro view of a slice runner: runtime phase, parked state, worker roster and
liveness heartbeat. The fleet root lives under the target repo's
``.claude/fleet`` (``FLEET_HOME``); on a bind-mounted checkout it survives
container restart, and it is excluded from git there.

Two writers touch the file concurrently — the runner wrapper's deterministic
60s heartbeat loop and the agent updating ``phase``/``parked_state`` at
transitions — so every read-modify-write happens under a sidecar ``flock`` and
lands via tmp-file + ``os.replace``. Readers never observe partial JSON;
interleaved writers never lose fields.

Shell callers (the runner wrapper, skills) use the CLI::

    python -m flotilla.status init|update|heartbeat|show --issue-id N ...
"""

import argparse
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
import dataclasses
from dataclasses import dataclass
from datetime import UTC, datetime
import fcntl
import json
import os
from pathlib import Path
import sys
from typing import Final, Literal, cast

from flotilla.constants import FLEET_ROOT, STATUS_FILENAME

Phase = Literal["claiming", "seams", "tdd", "qa", "parked", "done"]
ParkedState = Literal["needs-decision", "qa-ready", "awaiting-pr-approval", "failed"]

PHASES: Final[tuple[Phase, ...]] = ("claiming", "seams", "tdd", "qa", "parked", "done")
PARKED_STATES: Final[tuple[ParkedState, ...]] = (
    "needs-decision",
    "qa-ready",
    "awaiting-pr-approval",
    "failed",
)

_LOCK_FILENAME: Final[str] = ".status.lock"


class FleetStatusError(ValueError):
    """Raised when a status file or a status update violates the convention."""


@dataclass(frozen=True, slots=True)
class FleetStatus:
    """Schema of a slice's ``status.json`` — field set fixed by the addendum."""

    issue_id: int
    runner_id: str
    branch: str
    worktree: str
    pr_url: str | None
    phase: Phase
    parked_state: ParkedState | None
    worker_roster: tuple[str, ...]
    started_at: str
    last_heartbeat: str
    attempt: int
    last_error: str | None


class _UnsetType:
    """Sentinel distinguishing "leave unchanged" from an explicit ``None``."""


UNSET: Final[_UnsetType] = _UnsetType()


@dataclass(frozen=True, slots=True)
class StatusUpdate:
    """Partial update applied to ``status.json`` under the write lock."""

    phase: Phase | _UnsetType = UNSET
    parked_state: ParkedState | None | _UnsetType = UNSET
    pr_url: str | None | _UnsetType = UNSET
    worker_roster: tuple[str, ...] | _UnsetType = UNSET
    last_heartbeat: str | _UnsetType = UNSET
    attempt: int | _UnsetType = UNSET
    last_error: str | None | _UnsetType = UNSET


_SCHEMA_KEYS: Final[tuple[str, ...]] = (
    "issue_id",
    "runner_id",
    "branch",
    "worktree",
    "pr_url",
    "phase",
    "parked_state",
    "worker_roster",
    "started_at",
    "last_heartbeat",
    "attempt",
    "last_error",
)


def slice_dir(issue_id: int, fleet_root: Path = FLEET_ROOT) -> Path:
    """Return the per-slice artifact directory ``<fleet-root>/<issue-id>``."""
    return fleet_root / str(issue_id)


def status_path(issue_id: int, fleet_root: Path = FLEET_ROOT) -> Path:
    """Return the path of the slice's ``status.json``."""
    return slice_dir(issue_id, fleet_root) / STATUS_FILENAME


def new_status(
    issue_id: int,
    runner_id: str,
    branch: str,
    worktree: str,
    attempt: int = 1,
) -> FleetStatus:
    """Build a fresh status for a just-claimed slice (phase ``claiming``)."""
    now: str = _utcnow_iso()
    return FleetStatus(
        issue_id=issue_id,
        runner_id=runner_id,
        branch=branch,
        worktree=worktree,
        pr_url=None,
        phase="claiming",
        parked_state=None,
        worker_roster=(),
        started_at=now,
        last_heartbeat=now,
        attempt=attempt,
        last_error=None,
    )


def write(status: FleetStatus, fleet_root: Path = FLEET_ROOT) -> None:
    """Validate and persist ``status`` wholesale (used by ``init`` and tests)."""
    _validate(status)
    with _status_lock(status.issue_id, fleet_root):
        _write_unlocked(status_path(status.issue_id, fleet_root), status)


def load(issue_id: int, fleet_root: Path = FLEET_ROOT) -> FleetStatus:
    """Load and validate a slice's status; raise ``FileNotFoundError`` if absent."""
    return _read(status_path(issue_id, fleet_root))


def load_or_none(issue_id: int, fleet_root: Path = FLEET_ROOT) -> FleetStatus | None:
    """Load a slice's status, or return ``None`` when no status file exists."""
    try:
        return load(issue_id, fleet_root)
    except FileNotFoundError:
        return None


def update(issue_id: int, changes: StatusUpdate, fleet_root: Path = FLEET_ROOT) -> FleetStatus:
    """Apply ``changes`` to the stored status atomically and return the result."""
    path: Path = status_path(issue_id, fleet_root)
    with _status_lock(issue_id, fleet_root):
        merged: FleetStatus = _apply(_read(path), changes)
        _validate(merged)
        _write_unlocked(path, merged)
    return merged


def add_worker(issue_id: int, worker: str, fleet_root: Path = FLEET_ROOT) -> FleetStatus:
    """Append ``worker`` to the roster (idempotent), under the write lock."""
    path: Path = status_path(issue_id, fleet_root)
    with _status_lock(issue_id, fleet_root):
        current: FleetStatus = _read(path)
        if worker in current.worker_roster:
            return current
        merged: FleetStatus = dataclasses.replace(
            current, worker_roster=(*current.worker_roster, worker)
        )
        _write_unlocked(path, merged)
    return merged


def stamp_heartbeat(issue_id: int, fleet_root: Path = FLEET_ROOT) -> None:
    """Stamp ``last_heartbeat`` with the current UTC time, touching nothing else."""
    update(issue_id, StatusUpdate(last_heartbeat=_utcnow_iso()), fleet_root)


def to_json_dict(status: FleetStatus) -> dict[str, object]:
    """Serialize to a JSON-ready dict with keys in schema order."""
    return {
        "issue_id": status.issue_id,
        "runner_id": status.runner_id,
        "branch": status.branch,
        "worktree": status.worktree,
        "pr_url": status.pr_url,
        "phase": status.phase,
        "parked_state": status.parked_state,
        "worker_roster": list(status.worker_roster),
        "started_at": status.started_at,
        "last_heartbeat": status.last_heartbeat,
        "attempt": status.attempt,
        "last_error": status.last_error,
    }


def from_json_dict(data: Mapping[str, object]) -> FleetStatus:
    """Parse and validate a status dict, rejecting unknown or malformed keys."""
    unknown: set[str] = set(data) - set(_SCHEMA_KEYS)
    if unknown:
        raise FleetStatusError(f"unknown status.json keys: {sorted(unknown)}")
    status = FleetStatus(
        issue_id=_require_int(data, "issue_id"),
        runner_id=_require_str(data, "runner_id"),
        branch=_require_str(data, "branch"),
        worktree=_require_str(data, "worktree"),
        pr_url=_optional_str(data, "pr_url"),
        phase=_as_phase(data.get("phase")),
        parked_state=_as_parked_state(data.get("parked_state")),
        worker_roster=_require_str_tuple(data, "worker_roster"),
        started_at=_require_str(data, "started_at"),
        last_heartbeat=_require_str(data, "last_heartbeat"),
        attempt=_require_int(data, "attempt"),
        last_error=_optional_str(data, "last_error"),
    )
    _validate(status)
    return status


def _utcnow_iso() -> str:
    """Return the current UTC time in ISO 8601 form (seconds precision)."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _validate(status: FleetStatus) -> None:
    """Enforce cross-field invariants of the convention."""
    if status.phase == "parked" and status.parked_state is None:
        raise FleetStatusError("phase 'parked' requires a parked_state")
    if status.phase != "parked" and status.parked_state is not None:
        raise FleetStatusError(
            f"parked_state {status.parked_state!r} requires phase 'parked', got {status.phase!r}"
        )
    if status.attempt < 1:
        raise FleetStatusError(f"attempt must be >= 1, got {status.attempt}")


def _apply(current: FleetStatus, changes: StatusUpdate) -> FleetStatus:
    """Merge the set fields of ``changes`` over ``current``."""
    updates: dict[str, object] = {
        field.name: getattr(changes, field.name)
        for field in dataclasses.fields(changes)
        if not isinstance(getattr(changes, field.name), _UnsetType)
    }
    return dataclasses.replace(current, **updates)


@contextmanager
def _status_lock(issue_id: int, fleet_root: Path) -> Generator[None, None, None]:
    """Hold an exclusive sidecar flock for the slice's status file."""
    directory: Path = slice_dir(issue_id, fleet_root)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / _LOCK_FILENAME).open("w", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read(path: Path) -> FleetStatus:
    """Read and parse a status file (raises ``FileNotFoundError`` if absent)."""
    raw: str = path.read_text(encoding="utf-8")
    try:
        data: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FleetStatusError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise FleetStatusError(f"{path} must contain a JSON object")
    return from_json_dict(cast("Mapping[str, object]", data))


def _write_unlocked(path: Path, status: FleetStatus) -> None:
    """Write atomically via tmp-file + rename; caller holds the lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp: Path = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(to_json_dict(status), indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _require_str(data: Mapping[str, object], key: str) -> str:
    value: object = data.get(key)
    if not isinstance(value, str):
        raise FleetStatusError(f"{key} must be a string, got {value!r}")
    return value


def _optional_str(data: Mapping[str, object], key: str) -> str | None:
    value: object = data.get(key)
    if value is not None and not isinstance(value, str):
        raise FleetStatusError(f"{key} must be a string or null, got {value!r}")
    return value


def _require_int(data: Mapping[str, object], key: str) -> int:
    value: object = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise FleetStatusError(f"{key} must be an integer, got {value!r}")
    return value


def _require_str_tuple(data: Mapping[str, object], key: str) -> tuple[str, ...]:
    value: object = data.get(key)
    if not isinstance(value, list):
        raise FleetStatusError(f"{key} must be a list of strings, got {value!r}")
    items: list[object] = cast("list[object]", value)
    if not all(isinstance(item, str) for item in items):
        raise FleetStatusError(f"{key} must be a list of strings, got {value!r}")
    return tuple(cast("list[str]", items))


def _as_phase(value: object) -> Phase:
    if isinstance(value, str) and value in PHASES:
        return value
    raise FleetStatusError(f"phase must be one of {PHASES}, got {value!r}")


def _as_parked_state(value: object) -> ParkedState | None:
    if value is None:
        return None
    if isinstance(value, str) and value in PARKED_STATES:
        return value
    raise FleetStatusError(f"parked_state must be one of {PARKED_STATES} or null, got {value!r}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the shell-facing CLI; return a process exit code."""
    args: argparse.Namespace = _build_parser().parse_args(argv)
    try:
        return _dispatch(args)
    except FileNotFoundError:
        issue_id: int = args.issue_id
        fleet_root: Path = args.fleet_root
        print(
            f"fleet-status: no status file at {status_path(issue_id, fleet_root)}",
            file=sys.stderr,
        )
        return 2
    except FleetStatusError as exc:
        print(f"fleet-status: {exc}", file=sys.stderr)
        return 2


def _build_parser() -> argparse.ArgumentParser:
    """Build the argparse tree for the four subcommands."""
    parser = argparse.ArgumentParser(
        prog="fleet-status",
        description="Read and write the per-slice fleet status file.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="seed a fresh status.json")
    _add_common_arguments(init_parser)
    init_parser.add_argument("--runner-id", required=True)
    init_parser.add_argument("--branch", required=True)
    init_parser.add_argument("--worktree", required=True)
    init_parser.add_argument("--attempt", type=int, default=1)

    update_parser = subparsers.add_parser("update", help="update lifecycle fields")
    _add_common_arguments(update_parser)
    update_parser.add_argument("--phase", choices=PHASES)
    update_parser.add_argument("--parked-state", choices=(*PARKED_STATES, "none"))
    update_parser.add_argument("--pr-url")
    update_parser.add_argument("--last-error")
    update_parser.add_argument("--add-worker", action="append", default=[], metavar="NAME")

    heartbeat_parser = subparsers.add_parser("heartbeat", help="stamp last_heartbeat")
    _add_common_arguments(heartbeat_parser)

    show_parser = subparsers.add_parser("show", help="print status.json")
    _add_common_arguments(show_parser)

    return parser


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the arguments shared by every subcommand."""
    parser.add_argument("--issue-id", type=int, required=True)
    parser.add_argument("--fleet-root", type=Path, default=FLEET_ROOT)


def _dispatch(args: argparse.Namespace) -> int:
    """Route a parsed command line to its handler."""
    command: str = args.command
    if command == "init":
        return _cmd_init(args)
    if command == "update":
        return _cmd_update(args)
    if command == "heartbeat":
        return _cmd_heartbeat(args)
    return _cmd_show(args)


def _cmd_init(args: argparse.Namespace) -> int:
    write(
        new_status(
            issue_id=args.issue_id,
            runner_id=args.runner_id,
            branch=args.branch,
            worktree=args.worktree,
            attempt=args.attempt,
        ),
        fleet_root=args.fleet_root,
    )
    return 0


def _cmd_update(args: argparse.Namespace) -> int:
    changes: StatusUpdate = _changes_from_args(args)
    workers: list[str] = list(args.add_worker)
    if _is_noop(changes) and not workers:
        raise FleetStatusError("nothing to update — pass at least one field")
    if not _is_noop(changes):
        update(args.issue_id, changes, args.fleet_root)
    for worker in workers:
        add_worker(args.issue_id, worker, args.fleet_root)
    return 0


def _cmd_heartbeat(args: argparse.Namespace) -> int:
    stamp_heartbeat(args.issue_id, args.fleet_root)
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    print(json.dumps(to_json_dict(load(args.issue_id, args.fleet_root)), indent=2))
    return 0


def _changes_from_args(args: argparse.Namespace) -> StatusUpdate:
    """Translate optional CLI flags into a partial ``StatusUpdate``."""
    phase: Phase | _UnsetType = UNSET
    if args.phase is not None:
        phase = _as_phase(args.phase)
    parked_state: ParkedState | None | _UnsetType = UNSET
    if args.parked_state is not None:
        parked_state = None if args.parked_state == "none" else _as_parked_state(args.parked_state)
    pr_url: str | _UnsetType = UNSET if args.pr_url is None else args.pr_url
    last_error: str | _UnsetType = UNSET if args.last_error is None else args.last_error
    return StatusUpdate(
        phase=phase, parked_state=parked_state, pr_url=pr_url, last_error=last_error
    )


def _is_noop(changes: StatusUpdate) -> bool:
    """Report whether ``changes`` sets no field at all."""
    return all(
        isinstance(getattr(changes, field.name), _UnsetType)
        for field in dataclasses.fields(changes)
    )


if __name__ == "__main__":
    raise SystemExit(main())
