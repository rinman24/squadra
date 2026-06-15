"""Unit tests for the sandbox seam: status derivation + the compose adapter argv.

The provider-agnostic ``SandboxAccess`` conformance suite lives in
``tests/contract/test_sandbox_contract.py`` (against a fake). This module covers
the domain ``SandboxStatus`` derivation and the shipped ``ComposeSandbox``
adapter's compose/docker invocations against a canned command runner — no live
Docker required (mirrors ``tests/test_supervisor_adapters.py``).
"""

from collections.abc import Sequence
import json
from pathlib import Path
import subprocess

import pytest

from flotilla.domain import (
    ExecResult,
    SandboxAbsent,
    SandboxExited,
    SandboxRunning,
    SandboxSpec,
    SandboxStatus,
)
from flotilla.sandbox import ComposeSandbox, DryRunSandbox, status_from_inspect
from tests.helpers.sandbox_fakes import FakeSandbox


def _spec() -> SandboxSpec:
    return SandboxSpec(
        item_id=141,
        project="flotilla-slice-141",
        compose_file=Path("/work/.flotilla/compose.yaml"),
        worktree=Path("/work"),
    )


class _CannedDocker:
    """Canned ``docker`` stdout/exit keyed by the subcommand in the argv.

    Records every argv so tests assert the exact compose/docker invocation, and
    returns a configurable ``CompletedProcess`` per subcommand — no live Docker
    (mirrors ``tests/contract/test_ado_adapter_conformance.py``'s ``_CannedAz``).
    """

    def __init__(self, *, results: dict[str, tuple[int, str]] | None = None) -> None:
        self._results = results or {}
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        arglist: list[str] = list(args)
        self.calls.append(arglist)
        # the docker verb is the first token after the global compose flags; key
        # on whichever recognized verb appears in the argv.
        joined: str = " ".join(arglist)
        for verb, (code, out) in self._results.items():
            if verb in joined:
                return subprocess.CompletedProcess(arglist, code, out, "")
        return subprocess.CompletedProcess(arglist, 0, "", "")


# --- ComposeSandbox.launch ----------------------------------------------------


def test_launch_builds_and_ups_the_compose_project() -> None:
    docker = _CannedDocker(results={"up": (0, "")})
    sandbox = ComposeSandbox(run=docker)
    assert sandbox.launch(_spec()) is True
    argv: list[str] = docker.calls[-1]
    joined: str = " ".join(argv)
    # the runner receives args WITHOUT the "docker" prefix (it prepends it, like
    # AzCliAdo's run prepends "az"); project-scoped, agent-as-command up -d --build
    assert argv[0] == "compose"
    assert "-p flotilla-slice-141" in joined
    assert "-f /work/.flotilla/compose.yaml" in joined
    assert "up" in argv
    assert "-d" in argv
    assert "--build" in argv


def test_launch_reports_failure_when_build_or_up_fails() -> None:
    docker = _CannedDocker(results={"up": (1, "")})
    sandbox = ComposeSandbox(run=docker)
    # a non-zero up/build is the build-failed failure edge
    assert sandbox.launch(_spec()) is False


# --- ComposeSandbox.inspect ---------------------------------------------------


def test_inspect_running_container_reports_running() -> None:
    state = json.dumps([{"State": {"Status": "running", "ExitCode": 0}}])
    docker = _CannedDocker(results={" ps ": (0, "abc123\n"), "inspect": (0, state)})
    sandbox = ComposeSandbox(run=docker)
    assert isinstance(sandbox.inspect(_spec()), SandboxRunning)


def test_inspect_exited_container_carries_the_exit_code() -> None:
    state = json.dumps([{"State": {"Status": "exited", "ExitCode": 2}}])
    docker = _CannedDocker(results={" ps ": (0, "abc123\n"), "inspect": (0, state)})
    sandbox = ComposeSandbox(run=docker)
    assert sandbox.inspect(_spec()) == SandboxExited(exit_code=2)


def test_inspect_no_container_reports_absent() -> None:
    # compose ps -q prints nothing when the agent container does not exist
    docker = _CannedDocker(results={" ps ": (0, "\n")})
    sandbox = ComposeSandbox(run=docker)
    assert isinstance(sandbox.inspect(_spec()), SandboxAbsent)


def test_inspect_targets_the_agent_service() -> None:
    docker = _CannedDocker(results={" ps ": (0, "")})
    sandbox = ComposeSandbox(run=docker)
    sandbox.inspect(_spec())
    ps_call: list[str] = docker.calls[0]
    assert "ps" in ps_call
    assert "-q" in ps_call
    assert "agent" in ps_call


# --- ComposeSandbox.logs ------------------------------------------------------


def test_logs_returns_the_compose_logs_stdout() -> None:
    docker = _CannedDocker(results={"logs": (0, "hello from agent\n")})
    sandbox = ComposeSandbox(run=docker)
    assert sandbox.logs(_spec()) == "hello from agent\n"
    argv: list[str] = docker.calls[-1]
    assert "logs" in argv
    assert "agent" in argv


# --- ComposeSandbox.teardown --------------------------------------------------


def test_teardown_composes_down_with_volumes() -> None:
    docker = _CannedDocker(results={"down": (0, "")})
    sandbox = ComposeSandbox(run=docker)
    assert sandbox.teardown(_spec()) is True
    argv: list[str] = docker.calls[-1]
    assert "down" in argv
    assert "-v" in argv
    assert "-p flotilla-slice-141" in " ".join(argv)


def test_teardown_reports_failure_on_nonzero_exit() -> None:
    docker = _CannedDocker(results={"down": (1, "")})
    sandbox = ComposeSandbox(run=docker)
    # a leaked teardown is the teardown-failed edge (a non-blocking leak)
    assert sandbox.teardown(_spec()) is False


# --- ComposeSandbox.exec ------------------------------------------------------


def test_exec_runs_the_command_in_the_agent_service() -> None:
    docker = _CannedDocker(results={"exec": (0, "READY\n")})
    sandbox = ComposeSandbox(run=docker)
    result: ExecResult = sandbox.exec(_spec(), ("cat", "/work/.flotilla/outcome.json"))
    assert result == ExecResult(exit_code=0, stdout="READY\n")
    argv: list[str] = docker.calls[-1]
    assert "exec" in argv
    assert "agent" in argv
    # the command travels through verbatim, after the service
    assert argv[-2:] == ["cat", "/work/.flotilla/outcome.json"]


def test_exec_propagates_a_nonzero_exit_code() -> None:
    docker = _CannedDocker(results={"exec": (3, "boom\n")})
    sandbox = ComposeSandbox(run=docker)
    result: ExecResult = sandbox.exec(_spec(), ("false",))
    assert result.exit_code == 3


# --- status_from_inspect (docker inspect .State → neutral SandboxStatus) ------


def test_status_from_inspect_running_state_is_running() -> None:
    status: SandboxStatus = status_from_inspect({"Status": "running", "ExitCode": 0})
    assert isinstance(status, SandboxRunning)


def test_status_from_inspect_exited_carries_the_exit_code() -> None:
    status: SandboxStatus = status_from_inspect({"Status": "exited", "ExitCode": 137})
    assert status == SandboxExited(exit_code=137)


def test_status_from_inspect_clean_exit_is_zero() -> None:
    status: SandboxStatus = status_from_inspect({"Status": "exited", "ExitCode": 0})
    assert status == SandboxExited(exit_code=0)


def test_status_from_inspect_none_state_is_absent() -> None:
    # docker inspect of a removed/never-created container has no .State
    assert isinstance(status_from_inspect(None), SandboxAbsent)


def test_status_from_inspect_missing_exit_code_defaults_to_zero() -> None:
    # a non-running status with no ExitCode field still projects to an exit
    assert status_from_inspect({"Status": "dead"}) == SandboxExited(exit_code=0)


# --- DryRunSandbox (write-blocking decorator; mirrors ReadOnlyBoard) ----------


def test_dry_run_inspect_passes_through() -> None:
    inner = FakeSandbox()
    inner.seed("flotilla-slice-141", SandboxRunning())
    sandbox = DryRunSandbox(inner)
    assert isinstance(sandbox.inspect(_spec()), SandboxRunning)


def test_dry_run_logs_pass_through() -> None:
    inner = FakeSandbox()
    inner.seed_logs("flotilla-slice-141", "agent output\n")
    sandbox = DryRunSandbox(inner)
    assert sandbox.logs(_spec()) == "agent output\n"


def test_dry_run_launch_is_a_logged_noop(capsys: pytest.CaptureFixture[str]) -> None:
    inner = FakeSandbox()
    sandbox = DryRunSandbox(inner)
    assert sandbox.launch(_spec()) is True  # reports success, mutates nothing
    assert inner.launches == []  # the inner adapter was never touched
    out: str = capsys.readouterr().out
    assert "[dry-run] WOULD" in out
    assert "flotilla-slice-141" in out


def test_dry_run_teardown_is_a_logged_noop(capsys: pytest.CaptureFixture[str]) -> None:
    inner = FakeSandbox()
    sandbox = DryRunSandbox(inner)
    assert sandbox.teardown(_spec()) is True
    assert inner.teardowns == []
    assert "[dry-run] WOULD" in capsys.readouterr().out


def test_dry_run_exec_is_a_logged_noop(capsys: pytest.CaptureFixture[str]) -> None:
    inner = FakeSandbox()
    sandbox = DryRunSandbox(inner)
    result: ExecResult = sandbox.exec(_spec(), ("rm", "-rf", "/work"))
    assert result == ExecResult(exit_code=0, stdout="")  # benign no-op result
    assert inner.execs == []  # the destructive exec never reached the inner adapter
    assert "[dry-run] WOULD" in capsys.readouterr().out


def test_dry_run_blocks_every_mutation_but_no_read(capsys: pytest.CaptureFixture[str]) -> None:
    # the dry-run boundary blocks exactly the three mutations and no read
    inner = FakeSandbox()
    inner.seed("flotilla-slice-141", SandboxRunning())
    sandbox = DryRunSandbox(inner)
    sandbox.launch(_spec())
    sandbox.teardown(_spec())
    sandbox.exec(_spec(), ("true",))
    sandbox.inspect(_spec())
    sandbox.logs(_spec())
    assert (inner.launches, inner.teardowns, inner.execs) == ([], [], [])
    # the running seed was never torn down by the dry-run teardown
    assert isinstance(inner.statuses["flotilla-slice-141"], SandboxRunning)
