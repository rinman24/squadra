"""The sandbox ResourceAccess seam (ADR-0002 decisions 1 & 5).

``SandboxAccess`` is the provider-neutral contract the supervisor's orchestration
(F4) depends on to run one slice's Claude agent in a per-slice ephemeral Docker
compose project — the **agent-as-command, one-shot, inspect-driven** model:
``launch`` does build + ``compose up -d`` and returns immediately (the
non-blocking tick is preserved), the container's lifecycle *is* the agent's
lifecycle, and ``inspect`` derives the agent's liveness/exit from ``docker
inspect``. The seam **replaces the old ``Launcher`` protocol** and absorbs its
``pid_alive`` liveness into ``inspect`` (the ``runner.pid`` sidecar is dropped).

The seam speaks the neutral :class:`~flotilla.domain.SandboxStatus` /
:class:`~flotilla.domain.ExecResult` vocabulary and takes a
:class:`~flotilla.domain.SandboxSpec`; it never returns Docker-native strings.
``ComposeSandbox`` is the concrete compose-backed adapter (a later behavior in
this slice) and ``DryRunSandbox`` the write-blocking dry-run decorator (mirroring
:class:`flotilla.supervisor.ReadOnlyBoard`).
"""

from typing import Protocol, cast

from flotilla.domain import (
    ExecResult,
    SandboxAbsent,
    SandboxExited,
    SandboxRunning,
    SandboxSpec,
    SandboxStatus,
)


class SandboxAccess(Protocol):
    """The provider-neutral sandbox operations the orchestration depends on.

    Replaces the ``Launcher`` protocol. ``launch`` / ``teardown`` / ``exec`` are
    the mutations; ``inspect`` / ``logs`` are the reads (so the dry-run decorator
    blocks exactly the mutations). All take a :class:`SandboxSpec` — the per-slice
    compose project — so one adapter instance serves every concurrent slice.
    """

    def launch(self, spec: SandboxSpec) -> bool:
        """Build and start the slice's sandbox (``compose up -d``), non-blocking.

        Returns ``False`` when the build or the up failed (the ``build-failed``
        failure edge); ``True`` once the agent container has been created. The
        agent's *progress* is observed later via :meth:`inspect`, not awaited
        here — the tick stays non-blocking (ADR-0002 §5).
        """
        ...

    def inspect(self, spec: SandboxSpec) -> SandboxStatus:
        """Return the agent container's observed state (the agent's liveness).

        ``running`` / ``exited(code)`` / ``absent`` — derived from ``docker
        inspect``. In the agent-as-command model the exit code *is* the agent's
        exit code. This subsumes the old ``pid_alive`` liveness check.
        """
        ...

    def logs(self, spec: SandboxSpec) -> str:
        """Return the agent container's captured logs (``compose logs``)."""
        ...

    def teardown(self, spec: SandboxSpec) -> bool:
        """Remove the whole compose project + volumes (``compose down -v``).

        Returns ``False`` when teardown left resources behind (the
        ``teardown-failed`` edge — an orthogonal, non-blocking leak).
        """
        ...

    def exec(self, spec: SandboxSpec, command: tuple[str, ...]) -> ExecResult:
        """Run a one-off ``command`` in the running agent container (``docker exec``)."""
        ...


# --- docker inspect → SandboxStatus derivation --------------------------------

_RUNNING_STATUS: str = "running"


def status_from_inspect(state: object) -> SandboxStatus:
    """Project a ``docker inspect`` ``.State`` object onto a neutral status.

    ``state`` is the parsed ``.State`` mapping (``{"Status": "...", "ExitCode":
    n, ...}``); ``None`` (no such container) projects to
    :class:`~flotilla.domain.SandboxAbsent`. A ``running`` status is
    :class:`~flotilla.domain.SandboxRunning`; any other status (``exited`` /
    ``dead`` / ``created`` after the one-shot finishes) is treated as an exit and
    carries the ``.State.ExitCode`` as :class:`~flotilla.domain.SandboxExited`
    (defaulting to ``0`` only when the field is genuinely absent).
    """
    if not isinstance(state, dict):
        return SandboxAbsent()
    fields: dict[str, object] = cast("dict[str, object]", state)
    status: object = fields.get("Status")
    if status == _RUNNING_STATUS:
        return SandboxRunning()
    exit_code: object = fields.get("ExitCode")
    return SandboxExited(exit_code=exit_code if isinstance(exit_code, int) else 0)
