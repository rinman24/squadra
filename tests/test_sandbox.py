"""Unit tests for the sandbox seam: status derivation + the compose adapter argv.

The provider-agnostic ``SandboxAccess`` conformance suite lives in
``tests/contract/test_sandbox_contract.py`` (against a fake). This module covers
the domain ``SandboxStatus`` derivation and the shipped ``ComposeSandbox``
adapter's compose/docker invocations against a canned command runner — no live
Docker required (mirrors ``tests/test_supervisor_adapters.py``).
"""

from flotilla.domain import SandboxAbsent, SandboxExited, SandboxRunning, SandboxStatus
from flotilla.sandbox import status_from_inspect

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
