"""The provider-agnostic ``SandboxAccess`` conformance suite.

Mirrors ``tests/contract/test_board_contract.py``: every test asserts only
neutral seam behavior — the agent-as-command lifecycle expressed in
:class:`~squadra.domain.SandboxStatus` / :class:`~squadra.domain.ExecResult`,
never a docker-native invocation. Any implementation that passes this suite
satisfies the seam contract.

Two implementations are exercised:

- the in-memory :class:`~tests.helpers.sandbox_fakes.FakeSandbox` (the ``sandbox``
  fixture), which models the lifecycle in memory, and
- the SHIPPED :class:`squadra.sandbox.ComposeSandbox` driven by a canned docker
  runner (``test_compose_adapter_*``) — proving the real adapter, not just the
  fake, satisfies the seam (the pattern of ``test_ado_adapter_conformance.py``).
  No live Docker is required by any test here.
"""

from collections.abc import Sequence
import json
import subprocess

from squadra.domain import (
    ExecResult,
    SandboxAbsent,
    SandboxExited,
    SandboxRunning,
    SandboxSpec,
    SandboxStatus,
)
from squadra.sandbox import ComposeSandbox, SandboxAccess
from tests.helpers.sandbox_fakes import FakeSandbox

# sandbox, sandbox_spec are provided by tests/contract/conftest.py


# --- launch / inspect lifecycle -----------------------------------------------


def test_unlaunched_sandbox_inspects_as_absent(
    sandbox: FakeSandbox, sandbox_spec: SandboxSpec
) -> None:
    access: SandboxAccess = sandbox
    assert isinstance(access.inspect(sandbox_spec), SandboxAbsent)


def test_launch_reports_success_and_the_sandbox_becomes_running(
    sandbox: FakeSandbox, sandbox_spec: SandboxSpec
) -> None:
    access: SandboxAccess = sandbox
    assert access.launch(sandbox_spec) is True
    assert isinstance(access.inspect(sandbox_spec), SandboxRunning)


def test_launch_failure_is_reported(sandbox: FakeSandbox, sandbox_spec: SandboxSpec) -> None:
    sandbox.fail_launch.add(sandbox_spec.project)
    access: SandboxAccess = sandbox
    assert access.launch(sandbox_spec) is False


def test_exited_sandbox_carries_its_exit_code(
    sandbox: FakeSandbox, sandbox_spec: SandboxSpec
) -> None:
    sandbox.seed(sandbox_spec.project, SandboxExited(exit_code=42))
    access: SandboxAccess = sandbox
    status: SandboxStatus = access.inspect(sandbox_spec)
    assert status == SandboxExited(exit_code=42)


# --- teardown -----------------------------------------------------------------


def test_teardown_reports_success_and_the_sandbox_becomes_absent(
    sandbox: FakeSandbox, sandbox_spec: SandboxSpec
) -> None:
    access: SandboxAccess = sandbox
    access.launch(sandbox_spec)
    assert access.teardown(sandbox_spec) is True
    assert isinstance(access.inspect(sandbox_spec), SandboxAbsent)


def test_teardown_failure_is_reported(sandbox: FakeSandbox, sandbox_spec: SandboxSpec) -> None:
    sandbox.fail_teardown.add(sandbox_spec.project)
    access: SandboxAccess = sandbox
    access.launch(sandbox_spec)
    assert access.teardown(sandbox_spec) is False


# --- logs / exec --------------------------------------------------------------


def test_logs_return_the_captured_agent_output(
    sandbox: FakeSandbox, sandbox_spec: SandboxSpec
) -> None:
    sandbox.seed_logs(sandbox_spec.project, "agent says hi\n")
    access: SandboxAccess = sandbox
    assert access.logs(sandbox_spec) == "agent says hi\n"


def test_exec_returns_an_exit_code_and_stdout(
    sandbox: FakeSandbox, sandbox_spec: SandboxSpec
) -> None:
    sandbox.exec_result = ExecResult(exit_code=0, stdout="READY\n")
    access: SandboxAccess = sandbox
    result: ExecResult = access.exec(sandbox_spec, ("echo", "READY"))
    assert result == ExecResult(exit_code=0, stdout="READY\n")


# --- the shipped ComposeSandbox satisfies the same neutral contract -----------


class _CannedDocker:
    """Canned ``docker`` result keyed by the subcommand appearing in the argv."""

    def __init__(self, results: dict[str, tuple[int, str]]) -> None:
        self._results = results

    def __call__(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        joined: str = " ".join(args)
        for verb, (code, out) in self._results.items():
            if verb in joined:
                return subprocess.CompletedProcess(list(args), code, out, "")
        return subprocess.CompletedProcess(list(args), 0, "", "")


def test_compose_adapter_launch_then_inspect_is_running(sandbox_spec: SandboxSpec) -> None:
    running = json.dumps([{"State": {"Status": "running", "ExitCode": 0}}])
    adapter: SandboxAccess = ComposeSandbox(
        run=_CannedDocker({"up": (0, ""), " ps ": (0, "cid\n"), "inspect": (0, running)})
    )
    assert adapter.launch(sandbox_spec) is True
    assert isinstance(adapter.inspect(sandbox_spec), SandboxRunning)


def test_compose_adapter_inspect_exited_carries_code(sandbox_spec: SandboxSpec) -> None:
    exited = json.dumps([{"State": {"Status": "exited", "ExitCode": 9}}])
    adapter: SandboxAccess = ComposeSandbox(
        run=_CannedDocker({" ps ": (0, "cid\n"), "inspect": (0, exited)})
    )
    assert adapter.inspect(sandbox_spec) == SandboxExited(exit_code=9)


def test_compose_adapter_inspect_no_container_is_absent(sandbox_spec: SandboxSpec) -> None:
    adapter: SandboxAccess = ComposeSandbox(run=_CannedDocker({" ps ": (0, "\n")}))
    assert isinstance(adapter.inspect(sandbox_spec), SandboxAbsent)


def test_compose_adapter_teardown_and_failure(sandbox_spec: SandboxSpec) -> None:
    ok: SandboxAccess = ComposeSandbox(run=_CannedDocker({"down": (0, "")}))
    assert ok.teardown(sandbox_spec) is True
    leaked: SandboxAccess = ComposeSandbox(run=_CannedDocker({"down": (1, "")}))
    assert leaked.teardown(sandbox_spec) is False


def test_compose_adapter_logs_and_exec(sandbox_spec: SandboxSpec) -> None:
    adapter: SandboxAccess = ComposeSandbox(
        run=_CannedDocker({"logs": (0, "tail\n"), "exec": (0, "out\n")})
    )
    assert adapter.logs(sandbox_spec) == "tail\n"
    assert adapter.exec(sandbox_spec, ("true",)) == ExecResult(exit_code=0, stdout="out\n")
