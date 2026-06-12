"""Resolve flotilla's packaged data (the ``_scripts/`` and ``_templates/`` dirs).

The supervisor's tmux launcher, the ``flotilla`` dispatcher, and the tick entry
point all live *inside* the installed package, so they must locate
``runner-wrap.sh`` / ``fleet-tick.sh`` / ``fleetctl.sh`` via
``importlib.resources`` — never a path relative to the repo flotilla is
operating on (``FLEET_HOME``). Wheels can drop the executable bit, so a resolved
script is ``chmod +x`` before it is handed back.

The same invariant applies to the scaffolding templates (``_templates/``): the
``flotilla init`` scaffolder reads them as package data via ``importlib.
resources``. Templates are *read*, not executed, so they are not ``chmod +x``.
"""

import importlib.resources
from pathlib import Path
import stat

_SCRIPTS_DIRNAME: str = "_scripts"
_TEMPLATES_DIRNAME: str = "_templates"


def scripts_dir() -> Path:
    """Return the directory holding flotilla's packaged shell glue."""
    return Path(str(importlib.resources.files("flotilla").joinpath(_SCRIPTS_DIRNAME)))


def resolve_script(name: str) -> Path:
    """Return an executable absolute path to the packaged script ``name``.

    Raises
    ------
    FileNotFoundError
        If the package does not ship a script of that name.
    """
    path: Path = scripts_dir() / name
    if not path.is_file():
        raise FileNotFoundError(f"flotilla: packaged script not found: {path}")
    mode: int = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def templates_dir() -> Path:
    """Return the directory holding flotilla's packaged scaffolding templates."""
    return Path(str(importlib.resources.files("flotilla").joinpath(_TEMPLATES_DIRNAME)))


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
        raise FileNotFoundError(f"flotilla: packaged template not found: {path}")
    return path
