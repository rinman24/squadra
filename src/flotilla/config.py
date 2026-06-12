"""The supervisor's effective run configuration.

PR1 stub of the config layer: today's constants + ``FLEET_*`` env
``SupervisorConfig`` lives here unchanged, frozen per tick. PR2 grows this into
``FlotillaConfig`` (a tomllib loader with layered precedence
``defaults < flotilla.toml < FLEET_* env < CLI flag`` and validate-against-board).
"""

from dataclasses import dataclass
import os
from pathlib import Path

from flotilla.constants import FLEET_MAX_RUNNERS, FLEET_ROOT, MAX_ATTEMPTS


@dataclass(frozen=True, slots=True)
class SupervisorConfig:
    """One tick's effective configuration (constants + env, frozen per run)."""

    fleet_root: Path
    fleet_home: Path
    cap: int
    max_attempts: int
    epic_ids: tuple[int, ...]


def config_from_env(
    fleet_root: Path | None = None, fleet_home: Path | None = None
) -> SupervisorConfig:
    """Build the tick configuration from constants and the environment."""
    raw_epics: str = os.environ.get("FLEET_EPIC_IDS", "")
    epic_ids: tuple[int, ...] = tuple(int(part) for part in raw_epics.split(",") if part.strip())
    return SupervisorConfig(
        fleet_root=fleet_root if fleet_root is not None else FLEET_ROOT,
        fleet_home=fleet_home
        if fleet_home is not None
        else Path(os.environ.get("FLEET_HOME") or Path.cwd()),
        cap=FLEET_MAX_RUNNERS,
        max_attempts=MAX_ATTEMPTS,
        epic_ids=epic_ids,
    )
