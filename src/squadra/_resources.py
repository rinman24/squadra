"""Resolve squadra's packaged data (the ``_scripts/`` and ``_templates/`` dirs).

The supervisor's tmux launcher, the ``squadra`` dispatcher, and the tick entry
point all live *inside* the installed package, so they must locate
``runner-wrap.sh`` / ``fleet-tick.sh`` / ``fleetctl.sh`` via
``importlib.resources`` — never a path relative to the repo squadra is
operating on (``FLEET_HOME``). Wheels can drop the executable bit, so a resolved
script is ``chmod +x`` before it is handed back.

The same invariant applies to the scaffolding templates (``_templates/``): the
``squadra init`` scaffolder reads them as package data via ``importlib.
resources``. Templates are *read*, not executed, so they are not ``chmod +x``.
"""

import importlib.resources
from pathlib import Path
import stat

_SCRIPTS_DIRNAME: str = "_scripts"
_TEMPLATES_DIRNAME: str = "_templates"
_UNITS_DIRNAME: str = "_units"


def scripts_dir() -> Path:
    """Return the directory holding squadra's packaged shell glue."""
    return Path(str(importlib.resources.files("squadra").joinpath(_SCRIPTS_DIRNAME)))


def resolve_script(name: str) -> Path:
    """Return an executable absolute path to the packaged script ``name``.

    Raises
    ------
    FileNotFoundError
        If the package does not ship a script of that name.
    """
    path: Path = scripts_dir() / name
    if not path.is_file():
        raise FileNotFoundError(f"squadra: packaged script not found: {path}")
    mode: int = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def templates_dir() -> Path:
    """Return the directory holding squadra's packaged scaffolding templates."""
    return Path(str(importlib.resources.files("squadra").joinpath(_TEMPLATES_DIRNAME)))


def resolve_template(name: str) -> Path:
    """Return an absolute path to the packaged template ``name``.

    Templates are read (not executed), so — unlike :func:`resolve_script` — the
    executable bit is left untouched.

    Raises
    ------
    FileNotFoundError
        If the package does not ship a template of that name.
    """
    path: Path = templates_dir() / name
    if not path.is_file():
        raise FileNotFoundError(f"squadra: packaged template not found: {path}")
    return path


def units_dir() -> Path:
    """Return the directory holding squadra's packaged systemd unit templates."""
    return Path(str(importlib.resources.files("squadra").joinpath(_UNITS_DIRNAME)))


def resolve_unit(name: str) -> Path:
    """Return an absolute path to the packaged systemd unit template ``name``.

    Unit templates are read and rendered (not executed), so — like
    :func:`resolve_template` — the executable bit is left untouched.

    Raises
    ------
    FileNotFoundError
        If the package does not ship a unit template of that name.
    """
    path: Path = units_dir() / name
    if not path.is_file():
        raise FileNotFoundError(f"squadra: packaged unit template not found: {path}")
    return path
