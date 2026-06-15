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

from collections.abc import Callable, Sequence
import json
import subprocess
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


# --- compose-backed adapter ---------------------------------------------------

# The command-runner seam type: a docker argv in, the completed process out. The
# adapter needs both the exit code (launch/teardown success, exec status) and the
# captured stdout (inspect JSON, logs, exec output), so the seam returns the
# whole ``CompletedProcess`` (cf. the auth-probe runner in ``supervisor.py``).
DockerRun = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def _run_docker(args: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    """Run a ``docker`` command, capturing output; never raises on a non-zero exit.

    Non-zero is information the adapter needs (a failed build, a leaked teardown,
    an exec exit status), not an exception — the engine classifies the failure
    edge from the returned code, so ``check=False`` is deliberate.
    """
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False)


class ComposeSandbox:
    """``SandboxAccess`` backed by ``docker compose`` (the agent-as-command model).

    Each call maps a :class:`~flotilla.domain.SandboxSpec` onto a project-scoped
    ``docker compose -p <project> -f <compose_file>`` invocation (the
    target-repo-owned ``.flotilla/`` compose, ADR-0002 §15). ``launch`` builds +
    ``up -d`` the project (the agent service's command *is* the runner, so
    bringing it up *is* starting the agent) and returns immediately; ``inspect``
    derives the agent's liveness/exit from ``docker inspect`` of the agent
    container; ``teardown`` removes the whole project with its volumes. The
    command runner is injected (the DI seam, like ``AzCliAdo``'s ``run``) so unit
    tests use a canned runner — no live Docker.
    """

    def __init__(self, run: DockerRun = _run_docker) -> None:
        """Wire the adapter to its docker command runner (the test seam)."""
        self._run = run

    def _compose(self, spec: SandboxSpec) -> list[str]:
        """Build the project-scoped ``compose`` prefix shared by every compose call."""
        return ["compose", "-p", spec.project, "-f", str(spec.compose_file)]

    def launch(self, spec: SandboxSpec) -> bool:
        """Build + ``compose up -d`` the project; ``False`` if the build/up failed."""
        completed = self._run([*self._compose(spec), "up", "-d", "--build"])
        return completed.returncode == 0

    def inspect(self, spec: SandboxSpec) -> SandboxStatus:
        """Project the agent container's ``docker inspect .State`` onto a status.

        Two reads: ``compose ps -q <agent>`` resolves the container id (empty →
        :class:`~flotilla.domain.SandboxAbsent`, i.e. never launched or torn
        down), then ``docker inspect <id>`` yields ``[0].State`` for
        :func:`status_from_inspect`.
        """
        ps = self._run([*self._compose(spec), "ps", "-q", spec.agent_service])
        container_id: str = ps.stdout.strip()
        if not container_id:
            return SandboxAbsent()
        inspected = self._run(["inspect", container_id])
        return status_from_inspect(_first_state(inspected.stdout))

    def logs(self, spec: SandboxSpec) -> str:
        """Return ``compose logs <agent>`` stdout (the contained run's output)."""
        completed = self._run([*self._compose(spec), "logs", spec.agent_service])
        return completed.stdout

    def teardown(self, spec: SandboxSpec) -> bool:
        """``compose down -v`` the whole project; ``False`` on a leaked teardown."""
        completed = self._run([*self._compose(spec), "down", "-v"])
        return completed.returncode == 0

    def exec(self, spec: SandboxSpec, command: tuple[str, ...]) -> ExecResult:
        """Run ``command`` in the running agent container (``compose exec -T``)."""
        completed = self._run([*self._compose(spec), "exec", "-T", spec.agent_service, *command])
        return ExecResult(exit_code=completed.returncode, stdout=completed.stdout)


def _first_state(payload: str) -> object:
    """Extract ``[0].State`` from a ``docker inspect`` JSON array, or ``None``.

    ``docker inspect`` returns a one-element array; an empty/garbage payload (no
    such container, or a transient blank read) yields ``None`` →
    :class:`~flotilla.domain.SandboxAbsent`, never a crash.
    """
    if not payload.strip():
        return None
    raw: object = json.loads(payload)
    if not isinstance(raw, list) or not raw:
        return None
    first: object = cast("list[object]", raw)[0]
    if not isinstance(first, dict):
        return None
    return cast("dict[str, object]", first).get("State")
