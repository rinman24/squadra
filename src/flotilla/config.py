"""The fleet's effective run configuration — layered, validate-against-board.

``FlotillaConfig`` is the frozen, per-tick configuration the supervisor, the
engines, and the composition root consume. It is assembled with modern layered
precedence::

    built-in defaults  <  flotilla.toml  <  FLEET_* env  <  CLI flag

Only the un-defaultable is required: the ``provider`` (defaulted to ``ado`` for
minimum adoption friction) and ``[board.states]`` *unless* the provider's
process is inferable (``ado`` defaults to ADO-Basic's ``To Do/Doing/Done``;
other providers must declare their states). Everything else has a default.

Safety is **validate-against-board**, not mandatory typing: ``BoardAccess.
validate_config()`` resolves the configured state names / base branch against
the live board at ``flotilla init --check`` and at each tick's startup, and
fails loud — see :mod:`flotilla.board`. Operational/secret knobs
(``FLEET_MAX_RUNNERS``, the intervals, ``FLEET_MAX_ATTEMPTS``, ``FLEET_MODEL``/
``FLEET_EFFORT``, ``FLEET_HOME``/``FLEET_ROOT``/``FLEET_PYTHON``, the PAT) stay
env-only with today's defaults (resolved in :mod:`flotilla.constants`).
"""

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import tomllib
from typing import Final, cast

from flotilla.constants import (
    DEFAULT_TAG_PREFIX,
    FLEET_EFFORT,
    FLEET_MAX_RUNNERS,
    FLEET_MODEL,
    FLEET_ROOT,
    HEARTBEAT_INTERVAL_SECONDS,
    MAX_ATTEMPTS,
    STALENESS_THRESHOLD_SECONDS,
)
from flotilla.domain import Lifecycle, Tags

CONFIG_FILENAME: Final[str] = "flotilla.toml"
DEFAULT_PROVIDER: Final[str] = "ado"
DEFAULT_BASE_BRANCH: Final[str] = "main"
DEFAULT_BRANCH_TEMPLATE: Final[str] = "feat/slice-{id}-{slug}"
DEFAULT_WORKTREE_DIR: Final[str] = ".claude/worktrees"
DEFAULT_RUNNER_SKILL: Final[str] = "/afk-slice-runner"
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
    """Raised when flotilla.toml or the resolved configuration is malformed."""


@dataclass(frozen=True, slots=True)
class FlotillaConfig:
    """One tick's effective configuration (defaults < toml < env < flag, frozen)."""

    # [board]
    provider: str
    base_branch: str
    tag_prefix: str
    parent_scope_ids: tuple[int, ...]
    states: Mapping[Lifecycle, tuple[str, ...]]
    # [pipeline]
    branch_template: str
    worktree_dir: str
    runner_skill: str
    tdd_skill: str
    qa_skill: str
    cleanup_skill: str
    # operational (env-only knobs, resolved in flotilla.constants)
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
) -> FlotillaConfig:
    """Assemble the effective configuration with layered precedence.

    ``fleet_root`` / ``fleet_home`` / ``provider`` are the CLI-flag layer (the
    highest); ``config_path`` overrides where ``flotilla.toml`` is read from
    (default: ``<fleet_home>/flotilla.toml`` then ``./flotilla.toml``).
    """
    home: Path = fleet_home if fleet_home is not None else _env_fleet_home()
    root: Path = fleet_root if fleet_root is not None else FLEET_ROOT
    raw: Mapping[str, object] = _read_toml(config_path, home)
    board: Mapping[str, object] = _section(raw, "board")
    pipeline: Mapping[str, object] = _section(raw, "pipeline")

    resolved_provider: str = (
        provider or os.environ.get("FLEET_PROVIDER") or _str(board, "provider") or DEFAULT_PROVIDER
    )
    return FlotillaConfig(
        provider=resolved_provider,
        base_branch=os.environ.get("FLEET_BASE_BRANCH")
        or _str(board, "base_branch")
        or DEFAULT_BASE_BRANCH,
        tag_prefix=os.environ.get("FLEET_TAG_PREFIX")
        or _str(board, "tag_prefix")
        or DEFAULT_TAG_PREFIX,
        parent_scope_ids=_resolve_parent_scope_ids(board),
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
    """Resolve ``FLEET_HOME`` (the repo flotilla operates on), default cwd."""
    raw: str | None = os.environ.get("FLEET_HOME")
    return Path(raw) if raw is not None and raw.strip() else Path.cwd()


def _read_toml(config_path: Path | None, fleet_home: Path) -> Mapping[str, object]:
    """Read ``flotilla.toml`` from the explicit path, the fleet home, or cwd."""
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


def _resolve_parent_scope_ids(board: Mapping[str, object]) -> tuple[int, ...]:
    """Resolve the parent-link claim filter (toml < FLEET_EPIC_IDS env).

    ``parent_scope_ids`` supersedes the legacy ``FLEET_EPIC_IDS`` env var, which
    is still honored as the env-layer override for back-compat.
    """
    env: str | None = os.environ.get("FLEET_EPIC_IDS") or os.environ.get("FLEET_PARENT_SCOPE_IDS")
    if env is not None and env.strip():
        return tuple(int(part) for part in env.split(",") if part.strip())
    declared: object = board.get("parent_scope_ids")
    if declared is None:
        return ()
    if not isinstance(declared, list):
        raise ConfigError(f"[board].parent_scope_ids must be a list of ints, got {declared!r}")
    items: list[object] = cast("list[object]", declared)
    if not all(isinstance(item, int) and not isinstance(item, bool) for item in items):
        raise ConfigError(f"[board].parent_scope_ids must be a list of ints, got {declared!r}")
    return tuple(cast("list[int]", items))


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
