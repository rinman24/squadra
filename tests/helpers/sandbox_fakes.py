"""An in-memory ``SandboxAccess`` fake for the sandbox contract + dry-run suites.

Public so test files can annotate fixture parameters with the concrete type
(Pyright cannot infer fixture types; see Testing Conventions in CLAUDE.md).
Instances are provided by fixtures in ``tests/contract/conftest.py``.

:class:`FakeSandbox` conforms structurally to
:class:`squadra.sandbox.SandboxAccess`: it holds a seedable per-project
``SandboxStatus`` and canned logs / exec results, and records every mutation
(``launch`` / ``teardown`` / ``exec``) in order, so tests assert on neutral
behavior — never a docker-native invocation. It models the agent-as-command
lifecycle in memory: a launched project transitions ``absent → running``, a
teardown ``→ absent``.
"""

from dataclasses import dataclass, field

from squadra.domain import (
    ExecResult,
    SandboxAbsent,
    SandboxRunning,
    SandboxSpec,
    SandboxStatus,
)


@dataclass
class FakeSandbox:
    """Configurable in-memory ``SandboxAccess``; records every mutation in order.

    ``statuses`` maps a compose project name to its current
    :class:`~squadra.domain.SandboxStatus`; unseeded projects read as
    :class:`~squadra.domain.SandboxAbsent`. ``launches`` / ``teardowns`` record
    the specs they were called with, and ``execs`` records ``(project,
    command)``; ``fail_launch`` / ``fail_teardown`` are project-name sets that
    make those mutations report failure.
    """

    statuses: dict[str, SandboxStatus] = field(default_factory=dict[str, SandboxStatus])
    logs_by_project: dict[str, str] = field(default_factory=dict[str, str])
    exec_result: ExecResult = ExecResult(exit_code=0, stdout="")
    launches: list[SandboxSpec] = field(default_factory=list[SandboxSpec])
    teardowns: list[SandboxSpec] = field(default_factory=list[SandboxSpec])
    inspects: list[str] = field(default_factory=list[str])
    execs: list[tuple[str, tuple[str, ...]]] = field(
        default_factory=list[tuple[str, tuple[str, ...]]]
    )
    fail_launch: set[str] = field(default_factory=set[str])
    fail_teardown: set[str] = field(default_factory=set[str])

    def seed(self, project: str, status: SandboxStatus) -> None:
        """Seed a project's current observed status."""
        self.statuses[project] = status

    def inspect_count_is_zero(self) -> bool:
        """Whether the orchestrator never inspected any container this tick."""
        return self.inspects == []

    def seed_logs(self, project: str, logs: str) -> None:
        """Seed a project's captured agent logs."""
        self.logs_by_project[project] = logs

    # --- SandboxAccess surface ------------------------------------------------

    def launch(self, spec: SandboxSpec) -> bool:
        """Record the launch; project becomes running unless configured to fail."""
        self.launches.append(spec)
        if spec.project in self.fail_launch:
            return False
        self.statuses[spec.project] = SandboxRunning()
        return True

    def inspect(self, spec: SandboxSpec) -> SandboxStatus:
        """Return the project's seeded status (absent when unseeded); record the read."""
        self.inspects.append(spec.project)
        return self.statuses.get(spec.project, SandboxAbsent())

    def logs(self, spec: SandboxSpec) -> str:
        """Return the project's seeded logs (empty when unseeded)."""
        return self.logs_by_project.get(spec.project, "")

    def teardown(self, spec: SandboxSpec) -> bool:
        """Record the teardown; project becomes absent unless configured to fail."""
        self.teardowns.append(spec)
        if spec.project in self.fail_teardown:
            return False
        self.statuses[spec.project] = SandboxAbsent()
        return True

    def exec(self, spec: SandboxSpec, command: tuple[str, ...]) -> ExecResult:
        """Record the exec and return the configured result."""
        self.execs.append((spec.project, command))
        return self.exec_result
