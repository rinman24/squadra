"""flotilla — the ``fleetctl`` dispatcher console entry point.

``flotilla {start|stop|status|tick|log}`` is a thin wrapper that execs the
packaged ``fleetctl.sh`` (ADR-0007's hands-on ticker control). It resolves the
script from the installed package data and guarantees the interpreter the fleet
shells out to (``FLEET_PYTHON``) is the one flotilla is installed in, so each
supervisor tick / runner reaches ``flotilla.*`` regardless of what ``python3``
resolves to on PATH.

This is the simple shell-dispatching CLI of ADR-0007's extraction step; the
unified, argparse-native CLI that folds the supervisor and status operations in
directly is intentionally deferred.
"""

from collections.abc import Sequence
import os
import sys

from flotilla._resources import resolve_script


def main(argv: Sequence[str] | None = None) -> int:
    """Exec the packaged ``fleetctl.sh`` with the supplied arguments.

    Returns a process exit code only on failure to launch; on success
    ``os.execvpe`` replaces this process with ``bash``.
    """
    args: list[str] = list(sys.argv[1:] if argv is None else argv)
    script: str = str(resolve_script("fleetctl.sh"))
    env: dict[str, str] = dict(os.environ)
    env.setdefault("FLEET_PYTHON", sys.executable)
    try:
        os.execvpe("bash", ["bash", script, *args], env)
    except OSError as exc:  # bash missing / not runnable
        print(f"flotilla: failed to run {script}: {exc}", file=sys.stderr)
        return 127


if __name__ == "__main__":
    raise SystemExit(main())
