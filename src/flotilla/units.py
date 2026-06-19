"""Render + install the fleet-host systemd units (ADR-0002 §11, decision 11).

The fleet-host schedules ticks with systemd, not the retired tmux ticker: a
``flotilla.timer`` fires a oneshot ``flotilla.service`` that runs ``flotilla
fleet-tick`` (crash-only per tick fits ``Type=oneshot``). The unit *templates*
ship as package data under ``_units/`` with ``${...}`` placeholders; this module
renders them against a :class:`UnitContext` (host paths, the venv, the vault,
the cadence) and writes the result to the systemd unit directory.

The render is a pure :func:`render_units` (string substitution, no I/O) so it is
unit-tested without a host; :func:`install_units` is the thin writer. Installing
the units does **not** enable them — fleet activation is a deliberate
``systemctl enable --now flotilla.timer`` after the staged smoke (decision 16).
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Final

from flotilla._resources import resolve_unit

# The default per-tick cadence (the */3-minute rhythm the tmux ticker used).
DEFAULT_INTERVAL_SECONDS: Final[int] = 180
DEFAULT_SYSTEMD_DIR: Final[Path] = Path("/etc/systemd/system")
DEFAULT_USER: Final[str] = "azureuser"

# The unit files this module renders + installs, in dependency order.
UNIT_FILENAMES: Final[tuple[str, ...]] = ("flotilla.service", "flotilla.timer")

# A file writer seam (path, content) -> None, injected so tests render to a tmp
# dir without root / a real /etc/systemd/system.
UnitWriter = Callable[[Path, str], None]


@dataclass(frozen=True, slots=True)
class UnitContext:
    """The host-specific values interpolated into the fleet-host unit templates.

    - ``venv_bin`` — the fleet's venv ``bin`` dir (holds ``flotilla`` + ``python``).
    - ``fleet_home`` — the app-backend checkout the supervisor operates on.
    - ``fleet_root`` — the fleet state dir (``status.json``, ``supervisor.log``).
    - ``key_vault`` — the Key Vault name ``fleet-tick`` reads secrets from.
    - ``app_repo_url`` — the app-backend remote ``fleet-tick`` keeps ``fleet_home``
      synced to (empty disables the host-side auto-clone/update).
    - ``parent_scope_ids`` — the comma-separated Epic/Issue claim filter (empty = all).
    - ``interval_seconds`` — the timer cadence.
    - ``user`` — the unprivileged service account (in the ``docker`` group).
    """

    venv_bin: Path
    fleet_home: Path
    fleet_root: Path
    key_vault: str
    app_repo_url: str = ""
    parent_scope_ids: str = ""
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    user: str = DEFAULT_USER

    def as_mapping(self) -> dict[str, str]:
        """Project the context onto the template's ``${...}`` substitution keys."""
        return {
            "venv_bin": str(self.venv_bin),
            "fleet_home": str(self.fleet_home),
            "fleet_root": str(self.fleet_root),
            "key_vault": self.key_vault,
            "app_repo_url": self.app_repo_url,
            "parent_scope_ids": self.parent_scope_ids,
            "interval_seconds": str(self.interval_seconds),
            "user": self.user,
        }


def render_unit(name: str, ctx: UnitContext) -> str:
    """Render one packaged unit template ``name`` against ``ctx`` (pure, no I/O).

    Uses :meth:`string.Template.substitute` so a missing placeholder is a loud
    ``KeyError`` rather than a silently half-rendered unit.
    """
    template: Template = Template(resolve_unit(name).read_text(encoding="utf-8"))
    return template.substitute(ctx.as_mapping())


def render_units(ctx: UnitContext) -> dict[str, str]:
    """Render every fleet-host unit, returning ``{filename: rendered content}``."""
    return {name: render_unit(name, ctx) for name in UNIT_FILENAMES}


def _write_file(path: Path, content: str) -> None:
    """Default :data:`UnitWriter`: write ``content`` to ``path`` (0644)."""
    path.write_text(content, encoding="utf-8")


def install_units(
    ctx: UnitContext,
    *,
    dest: Path = DEFAULT_SYSTEMD_DIR,
    writer: UnitWriter = _write_file,
) -> list[Path]:
    """Render + write the fleet-host units into ``dest``; return the written paths.

    Writing the units neither reloads systemd nor enables the timer — both are
    deliberate operator steps (``systemctl daemon-reload`` then, only when
    activating the fleet, ``systemctl enable --now flotilla.timer``). The writer
    is injected so tests target a temp dir.
    """
    written: list[Path] = []
    for name, content in render_units(ctx).items():
        target: Path = dest / name
        writer(target, content)
        written.append(target)
    return written
