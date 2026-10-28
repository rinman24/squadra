"""Render + install the fleet-host systemd units (ADR-0002 §11, decision 11).

The fleet-host schedules ticks with systemd, not the retired tmux ticker: a
``squadra-<name>.timer`` fires a oneshot ``squadra-<name>.service`` that runs
``squadra fleet-tick`` (crash-only per tick fits ``Type=oneshot``). The unit
*templates* ship as package data under ``_units/`` with ``${...}`` placeholders;
this module renders them against a :class:`UnitContext` (the fleet name, host
paths, the venv, the vault, the cadence) and writes the result to the systemd
unit directory under names derived from ``[fleet] name`` (N34), so a second
fleet on the host installs beside the first instead of over it.

The render is a pure :func:`render_units` (string substitution, no I/O) so it is
unit-tested without a host; :func:`install_units` is the thin writer. Installing
the units does **not** enable them — fleet activation is a deliberate
``systemctl enable --now squadra-<name>.timer`` after the staged smoke (decision
16). :func:`install_units` refuses to overwrite a unit file that renders a
different ``FLEET_HOME``: that file is another fleet's. It also refuses a fleet
whose repository another fleet on the host already serves (N34): issue numbers
are per repository, so two fleets on one repository would work one issue twice.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import re
from string import Template
from typing import Final

from squadra._resources import resolve_unit

# The default per-tick cadence (the */3-minute rhythm the tmux ticker used).
DEFAULT_INTERVAL_SECONDS: Final[int] = 180
DEFAULT_SYSTEMD_DIR: Final[Path] = Path("/etc/systemd/system")
DEFAULT_USER: Final[str] = "azureuser"

# The packaged unit templates, in dependency order; each installs as
# ``squadra-<fleet name>.<suffix>`` (:func:`unit_filename`).
UNIT_TEMPLATES: Final[tuple[str, ...]] = ("squadra.service", "squadra.timer")

# How a rendered service names its fleet home and its clone URL (one line each).
_FLEET_HOME_LINE: Final[re.Pattern[str]] = re.compile(r"^Environment=FLEET_HOME=(.*)$", re.M)
_APP_REPO_URL_LINE: Final[re.Pattern[str]] = re.compile(
    r"^Environment=FLEET_APP_REPO_URL=(.*)$", re.M
)

# The services another fleet on the host may have installed: named ones, and the
# unnamed ``squadra.service`` from before fleet names (N35).
_SERVICE_GLOBS: Final[tuple[str, ...]] = ("squadra.service", "squadra-*.service")

# A file writer seam (path, content) -> None, injected so tests render to a tmp
# dir without root / a real /etc/systemd/system.
UnitWriter = Callable[[Path, str], None]

# The repository a fleet serves, from its fleet home and its clone URL
# (``repo.fleet_repository``), or ``None`` when neither resolves. Injected by the
# composition root; tests pass a table.
RepositoryOf = Callable[[Path, str], str | None]


class UnitConflictError(RuntimeError):
    """Raised when installing would overwrite another fleet's unit file or repository."""


@dataclass(frozen=True, slots=True)
class UnitContext:
    """The host-specific values interpolated into the fleet-host unit templates.

    - ``fleet_name`` — ``[fleet] name``; the unit filenames derive from it.
    - ``venv_bin`` — the fleet's venv ``bin`` dir (holds ``squadra`` + ``python``).
    - ``fleet_home`` — the app-backend checkout the supervisor operates on.
    - ``fleet_root`` — the fleet state dir (``status.json``, ``supervisor.log``).
    - ``key_vault`` — the Key Vault name ``fleet-tick`` reads secrets from.
    - ``app_repo_url`` — the app-backend remote ``fleet-tick`` keeps ``fleet_home``
      synced to (empty disables the host-side auto-clone/update).
    - ``interval_seconds`` — the timer cadence.
    - ``user`` — the unprivileged service account (in the ``docker`` group).
    """

    fleet_name: str
    venv_bin: Path
    fleet_home: Path
    fleet_root: Path
    key_vault: str
    app_repo_url: str = ""
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    user: str = DEFAULT_USER

    def as_mapping(self) -> dict[str, str]:
        """Project the context onto the template's ``${...}`` substitution keys."""
        return {
            "fleet_name": self.fleet_name,
            "venv_bin": str(self.venv_bin),
            "fleet_home": str(self.fleet_home),
            "fleet_root": str(self.fleet_root),
            "key_vault": self.key_vault,
            "app_repo_url": self.app_repo_url,
            "interval_seconds": str(self.interval_seconds),
            "user": self.user,
        }


def unit_filename(template: str, fleet_name: str) -> str:
    """Return ``template``'s installed filename for ``fleet_name`` (``squadra-<name>.<suffix>``)."""
    stem, _, suffix = template.partition(".")
    return f"{stem}-{fleet_name}.{suffix}"


def render_unit(name: str, ctx: UnitContext) -> str:
    """Render one packaged unit template ``name`` against ``ctx`` (pure, no I/O).

    Uses :meth:`string.Template.substitute` so a missing placeholder is a loud
    ``KeyError`` rather than a silently half-rendered unit.
    """
    template: Template = Template(resolve_unit(name).read_text(encoding="utf-8"))
    return template.substitute(ctx.as_mapping())


def render_units(ctx: UnitContext) -> dict[str, str]:
    """Render every fleet-host unit, returning ``{installed filename: rendered content}``."""
    return {unit_filename(name, ctx.fleet_name): render_unit(name, ctx) for name in UNIT_TEMPLATES}


def rendered_fleet_home(content: str) -> Path | None:
    """Return the ``FLEET_HOME`` a rendered unit runs against, or ``None`` if it names none."""
    match: re.Match[str] | None = _FLEET_HOME_LINE.search(content)
    return Path(match.group(1).strip()) if match is not None else None


def _rendered_app_repo_url(content: str) -> str:
    """Return the ``FLEET_APP_REPO_URL`` a rendered unit carries (empty when none)."""
    match: re.Match[str] | None = _APP_REPO_URL_LINE.search(content)
    return match.group(1).strip() if match is not None else ""


def _other_fleets(dest: Path, own_service: str) -> list[tuple[Path, Path, str]]:
    """Return ``(unit, fleet home, clone URL)`` for every other fleet's service in ``dest``.

    A matching file that names no ``FLEET_HOME`` was not rendered by squadra, so
    it is not a fleet.
    """
    units: list[Path] = sorted({path for glob in _SERVICE_GLOBS for path in dest.glob(glob)})
    fleets: list[tuple[Path, Path, str]] = []
    for unit in units:
        if unit.name == own_service:
            continue
        content: str = unit.read_text(encoding="utf-8")
        home: Path | None = rendered_fleet_home(content)
        if home is not None:
            fleets.append((unit, home, _rendered_app_repo_url(content)))
    return fleets


def _check_repository(ctx: UnitContext, dest: Path, repository_of: RepositoryOf) -> None:
    """Refuse when another fleet on the host serves ``ctx``'s repository (N34).

    Fails closed both ways: this fleet's repository must resolve, and so must
    every other fleet's, since one that does not could be the same repository.
    Messages name repositories by :func:`squadra.repo.repository_identity` key,
    never by URL, because an HTTPS URL can carry a token.
    """
    own_service: str = unit_filename(UNIT_TEMPLATES[0], ctx.fleet_name)
    others: list[tuple[Path, Path, str]] = _other_fleets(dest, own_service)
    if not others:
        return
    repository: str | None = repository_of(ctx.fleet_home, ctx.app_repo_url)
    if repository is None:
        raise UnitConflictError(
            f"cannot tell which repository fleet {ctx.fleet_name!r} serves: {ctx.fleet_home} "
            "has no origin squadra can read and no --app-repo-url was given. Another fleet "
            "is installed on this host, and two fleets may not serve one repository; pass "
            "--app-repo-url (a GitHub or Azure DevOps remote) or clone the fleet home first"
        )
    for unit, home, app_repo_url in others:
        theirs: str | None = repository_of(home, app_repo_url)
        if theirs is None:
            raise UnitConflictError(
                f"cannot tell which repository {unit} serves: its fleet home {home} has no "
                "origin squadra can read and the unit names no FLEET_APP_REPO_URL. It may "
                f"serve {repository}; if that fleet is gone, remove {unit.name} and its "
                "timer, else clone its fleet home, then re-run"
            )
        if theirs == repository:
            raise UnitConflictError(
                f"{unit} already serves repository {repository}: one fleet per repository "
                f"(N34), so fleet {ctx.fleet_name!r} may not serve it too. Two fleets on one "
                "repository would claim the same issues; if this fleet replaces that one, "
                f"stop and remove {unit.name} and its timer first"
            )


def _write_file(path: Path, content: str) -> None:
    """Default :data:`UnitWriter`: write ``content`` to ``path`` (0644)."""
    path.write_text(content, encoding="utf-8")


def install_units(
    ctx: UnitContext,
    *,
    repository_of: RepositoryOf,
    dest: Path = DEFAULT_SYSTEMD_DIR,
    writer: UnitWriter = _write_file,
) -> list[Path]:
    """Render + write the fleet-host units into ``dest``; return the written paths.

    Writing the units neither reloads systemd nor enables the timer — both are
    deliberate operator steps (``systemctl daemon-reload`` then, only when
    activating the fleet, ``systemctl enable --now squadra-<name>.timer``). The
    writer is injected so tests target a temp dir.

    Raises :class:`UnitConflictError`, writing nothing, when an existing service
    of this name renders a different ``FLEET_HOME``: it is another fleet's, and
    overwriting it would silently stop that fleet ticking (N34). Re-installing
    over this fleet's own units is allowed. Also raises it, writing nothing, when
    another fleet's service serves this fleet's repository, or when either
    repository cannot be resolved (:func:`_check_repository`). ``repository_of``
    has no default: a default that resolved nothing would refuse every second
    fleet, and one that skipped the check would let two fleets share a repository.
    """
    rendered: dict[str, str] = render_units(ctx)
    for name in rendered:
        existing: Path = dest / name
        if not existing.is_file():
            continue
        home: Path | None = rendered_fleet_home(existing.read_text(encoding="utf-8"))
        if home is not None and home != ctx.fleet_home:
            raise UnitConflictError(
                f"{existing} already runs fleet home {home}, not {ctx.fleet_home}: "
                f"fleet name {ctx.fleet_name!r} is taken on this host; name this fleet "
                "differently in its squadra.toml [fleet] name"
            )
    _check_repository(ctx, dest, repository_of)
    written: list[Path] = []
    for name, content in rendered.items():
        target: Path = dest / name
        writer(target, content)
        written.append(target)
    return written
