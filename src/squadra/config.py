"""The fleet's effective run configuration — layered, validate-against-board.

``SquadraConfig`` is the frozen, per-tick configuration the supervisor, the
engines, and the composition root consume. It is assembled with modern layered
precedence::

    built-in defaults  <  squadra.toml  <  FLEET_* env  <  CLI flag

Only the un-defaultable is required: the ``provider`` (defaulted to ``ado`` for
minimum adoption friction), ``[board.states]`` *unless* the provider's
process is inferable (``ado`` defaults to ADO-Basic's ``To Do/Doing/Done``;
other providers must declare their states), and ``[board].claim_scope``.
Everything else has a default.

Claim scope is the one deliberate exception to "default everything": it has no
default and no env layer, and loading fails closed (``ConfigError``) until the
operator declares what on the board is claimable (WSQ1). A board pointed at by
a forgotten config line must stop at this check, not reach every queued item.

Safety is **validate-against-board**, not mandatory typing: ``BoardAccess.
validate_config()`` resolves the configured state names / base branch against
the live board at ``squadra init --check`` and at each tick's startup, and
fails loud — see :mod:`squadra.board`. Operational/secret knobs
(``FLEET_MAX_RUNNERS``, the intervals, ``FLEET_MAX_ATTEMPTS``, ``FLEET_MODEL``/
``FLEET_EFFORT``, ``FLEET_HOME``/``FLEET_ROOT``/``FLEET_PYTHON``, the PAT) stay
env-only with today's defaults (resolved in :mod:`squadra.constants`).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import tomllib
from typing import Final, cast

from squadra.constants import (
    DEFAULT_TAG_PREFIX,
    FLEET_EFFORT,
    FLEET_MAX_RUNNERS,
    FLEET_MODEL,
    FLEET_ROOT,
    HEARTBEAT_INTERVAL_SECONDS,
    MAX_ATTEMPTS,
    STALENESS_THRESHOLD_SECONDS,
)
from squadra.domain import Lifecycle, Tags

CONFIG_FILENAME: Final[str] = "squadra.toml"
DEFAULT_PROVIDER: Final[str] = "ado"
DEFAULT_BASE_BRANCH: Final[str] = "main"
DEFAULT_BRANCH_TEMPLATE: Final[str] = "feat/increment-{id}-{slug}"
DEFAULT_WORKTREE_DIR: Final[str] = ".claude/worktrees"
DEFAULT_RUNNER_SKILL: Final[str] = "/afk-increment-runner"
DEFAULT_TDD_SKILL: Final[str] = "/tdd"
DEFAULT_QA_SKILL: Final[str] = "/qa"
DEFAULT_CLEANUP_SKILL: Final[str] = "/cleanup-merged-branches"

# ADO's Basic process is the one provider whose states are inferable, so it is
# the only one that may omit ``[board.states]`` (design note, Configuration §).
ADO_BASIC_STATES: Final[Mapping[Lifecycle, tuple[str, ...]]] = {
    Lifecycle.QUEUED: ("To Do",),
    Lifecycle.ACTIVE: ("Doing",),
    Lifecycle.DONE: ("Done",),
}

_LIFECYCLE_BY_TOML_KEY: Final[Mapping[str, Lifecycle]] = {
    "queued": Lifecycle.QUEUED,
    "active": Lifecycle.ACTIVE,
    "done": Lifecycle.DONE,
}


class ConfigError(ValueError):
    """Raised when squadra.toml or the resolved configuration is malformed."""


class ClaimScope(Enum):
    """What on the board the fleet may claim (``[board].claim_scope``, required).

    - :attr:`PARENTS` — only queued items whose parent is in ``parent_scope_ids``.
    - :attr:`WHOLE_BOARD` — every queued item; ``parent_scope_ids`` must be empty.
    """

    PARENTS = "parents"
    WHOLE_BOARD = "whole-board"


@dataclass(frozen=True, slots=True)
class SquadraConfig:
    """One tick's effective configuration (defaults < toml < env < flag, frozen)."""

    # [board]
    provider: str
    base_branch: str
    tag_prefix: str
    claim_scope: ClaimScope
    parent_scope_ids: tuple[int, ...]
    states: Mapping[Lifecycle, tuple[str, ...]]
    # [pipeline]
    branch_template: str
    worktree_dir: str
    runner_skill: str
    tdd_skill: str
    qa_skill: str
    cleanup_skill: str
    # operational (env-only knobs, resolved in squadra.constants)
    fleet_root: Path
    fleet_home: Path
    cap: int
    max_attempts: int
    model: str
    effort: str
    heartbeat_interval_seconds: int
    staleness_threshold_seconds: int

    @property
    def tags(self) -> Tags:
        """The fleet tag vocabulary under this config's prefix."""
        return Tags(self.tag_prefix)


def load_config(
    *,
    fleet_root: Path | None = None,
    fleet_home: Path | None = None,
    config_path: Path | None = None,
    provider: str | None = None,
) -> SquadraConfig:
    """Assemble the effective configuration with layered precedence.

    ``fleet_root`` / ``fleet_home`` / ``provider`` are the CLI-flag layer (the
    highest); ``config_path`` overrides where ``squadra.toml`` is read from
    (default: ``<fleet_home>/squadra.toml`` then ``./squadra.toml``).
    """
    home: Path = fleet_home if fleet_home is not None else _env_fleet_home()
    root: Path = fleet_root if fleet_root is not None else FLEET_ROOT
    raw: Mapping[str, object] = _read_toml(config_path, home)
    board: Mapping[str, object] = _section(raw, "board")
    pipeline: Mapping[str, object] = _section(raw, "pipeline")

    resolved_provider: str = (
        provider or os.environ.get("FLEET_PROVIDER") or _str(board, "provider") or DEFAULT_PROVIDER
    )
    claim_scope: ClaimScope = _resolve_claim_scope(board)
    return SquadraConfig(
        provider=resolved_provider,
        base_branch=os.environ.get("FLEET_BASE_BRANCH")
        or _str(board, "base_branch")
        or DEFAULT_BASE_BRANCH,
        tag_prefix=os.environ.get("FLEET_TAG_PREFIX")
        or _str(board, "tag_prefix")
        or DEFAULT_TAG_PREFIX,
        claim_scope=claim_scope,
        parent_scope_ids=_resolve_parent_scope_ids(board, claim_scope),
        states=_resolve_states(board, resolved_provider),
        branch_template=_str(pipeline, "branch_template") or DEFAULT_BRANCH_TEMPLATE,
        worktree_dir=_str(pipeline, "worktree_dir") or DEFAULT_WORKTREE_DIR,
        runner_skill=_str(pipeline, "runner_skill") or DEFAULT_RUNNER_SKILL,
        tdd_skill=_str(pipeline, "tdd_skill") or DEFAULT_TDD_SKILL,
        qa_skill=_str(pipeline, "qa_skill") or DEFAULT_QA_SKILL,
        cleanup_skill=_str(pipeline, "cleanup_skill") or DEFAULT_CLEANUP_SKILL,
        fleet_root=root,
        fleet_home=home,
        cap=FLEET_MAX_RUNNERS,
        max_attempts=MAX_ATTEMPTS,
        model=FLEET_MODEL,
        effort=FLEET_EFFORT,
        heartbeat_interval_seconds=HEARTBEAT_INTERVAL_SECONDS,
        staleness_threshold_seconds=STALENESS_THRESHOLD_SECONDS,
    )


def _env_fleet_home() -> Path:
    """Resolve ``FLEET_HOME`` (the repo squadra operates on), default cwd."""
    raw: str | None = os.environ.get("FLEET_HOME")
    return Path(raw) if raw is not None and raw.strip() else Path.cwd()


def _read_toml(config_path: Path | None, fleet_home: Path) -> Mapping[str, object]:
    """Read ``squadra.toml`` from the explicit path, the fleet home, or cwd."""
    candidates: list[Path] = (
        [config_path]
        if config_path is not None
        else [fleet_home / CONFIG_FILENAME, Path.cwd() / CONFIG_FILENAME]
    )
    for candidate in candidates:
        if candidate.is_file():
            with candidate.open("rb") as handle:
                try:
                    return cast("dict[str, object]", tomllib.load(handle))
                except tomllib.TOMLDecodeError as exc:
                    raise ConfigError(f"{candidate} is not valid TOML: {exc}") from exc
    return {}


def _section(raw: Mapping[str, object], name: str) -> Mapping[str, object]:
    """Return a top-level table from the parsed TOML, or an empty mapping."""
    value: object = raw.get(name)
    return cast("Mapping[str, object]", value) if isinstance(value, dict) else {}


def _str(section: Mapping[str, object], key: str) -> str | None:
    """Return a string scalar from a TOML section, or ``None`` when absent."""
    value: object = section.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"config key {key!r} must be a string, got {value!r}")
    return value


def _resolve_claim_scope(board: Mapping[str, object]) -> ClaimScope:
    """Resolve the required ``[board].claim_scope`` (toml only, no default).

    Missing and empty (the ``squadra init`` scaffold) both fail, naming the two
    options, so scope cannot be skipped without making a choice.
    """
    declared: str | None = _str(board, "claim_scope")
    options: str = " or ".join(f'"{scope.value}"' for scope in ClaimScope)
    if not declared:
        raise ConfigError(
            f"[board].claim_scope is required: set it to {options} "
            '("parents" claims only items under [board].parent_scope_ids; '
            '"whole-board" claims every queued item on the board)'
        )
    try:
        return ClaimScope(declared)
    except ValueError:
        raise ConfigError(f"[board].claim_scope must be {options}, got {declared!r}") from None


def _resolve_parent_scope_ids(
    board: Mapping[str, object], claim_scope: ClaimScope
) -> tuple[int, ...]:
    """Resolve the parent-link claim filter and check it agrees with ``claim_scope``.

    ``"parents"`` needs at least one id; ``"whole-board"`` takes none (ids there
    would be ambiguous). There is no env layer: the toml is the only way in.
    """
    declared: object = board.get("parent_scope_ids")
    ids: tuple[int, ...] = ()
    if declared is not None:
        if not isinstance(declared, list):
            raise ConfigError(f"[board].parent_scope_ids must be a list of ints, got {declared!r}")
        items: list[object] = cast("list[object]", declared)
        if not all(isinstance(item, int) and not isinstance(item, bool) for item in items):
            raise ConfigError(f"[board].parent_scope_ids must be a list of ints, got {declared!r}")
        ids = tuple(cast("list[int]", items))
    if claim_scope is ClaimScope.PARENTS and not ids:
        raise ConfigError(
            '[board].claim_scope = "parents" needs a non-empty [board].parent_scope_ids'
        )
    if claim_scope is ClaimScope.WHOLE_BOARD and ids:
        raise ConfigError(
            '[board].claim_scope = "whole-board" takes no [board].parent_scope_ids '
            f'(got {list(ids)}); use "parents" to claim only under those ids'
        )
    return ids


def _resolve_states(
    board: Mapping[str, object], provider: str
) -> Mapping[Lifecycle, tuple[str, ...]]:
    """Resolve the Lifecycle→native-state mapping (declared, or inferred for ADO).

    ``[board.states]`` declares ``queued``/``active``/``done`` as lists of native
    state names (many-native→one-neutral allowed). It is required unless the
    provider's process is inferable (only ADO-Basic today).
    """
    raw_states: object = board.get("states")
    if raw_states is None:
        if provider == DEFAULT_PROVIDER:
            return ADO_BASIC_STATES
        raise ConfigError(
            f"[board.states] is required for provider {provider!r}: its statuses are "
            "user-defined and cannot be inferred (only ADO-Basic defaults To Do/Doing/Done)"
        )
    if not isinstance(raw_states, dict):
        raise ConfigError(f"[board.states] must be a table, got {raw_states!r}")
    section: Mapping[str, object] = cast("Mapping[str, object]", raw_states)
    resolved: dict[Lifecycle, tuple[str, ...]] = {}
    for key, lifecycle in _LIFECYCLE_BY_TOML_KEY.items():
        names: object = section.get(key)
        if names is None:
            raise ConfigError(f"[board.states] is missing required bucket {key!r}")
        resolved[lifecycle] = _state_names(key, names)
    return resolved


def _state_names(key: str, names: object) -> tuple[str, ...]:
    """Coerce one ``[board.states]`` bucket to a non-empty tuple of names."""
    if isinstance(names, str):
        names = [names]
    if not isinstance(names, list) or not names:
        raise ConfigError(
            f"[board.states].{key} must be a non-empty list of state-name strings, got {names!r}"
        )
    items: list[object] = cast("list[object]", names)
    if not all(isinstance(name, str) and name for name in items):
        raise ConfigError(
            f"[board.states].{key} must be a non-empty list of state-name strings, got {names!r}"
        )
    return tuple(cast("list[str]", items))
