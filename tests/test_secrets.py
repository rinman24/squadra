"""Unit tests for the fleet-host secret bootstrap (``flotilla.secrets``).

Covers the ``az``-backed Key Vault adapter against a canned command runner (no
live ``az``/IMDS/Key Vault) and the two env projections — with the load-bearing
negative property that the PAT never reaches the agent-facing env (ADR-0002 §11).
"""

from collections.abc import Sequence
import subprocess

import pytest

from flotilla.secrets import (
    ADO_PAT_ENV,
    ANTHROPIC_API_KEY_ENV,
    AzKeyVaultSecrets,
    FleetSecrets,
    SecretFetchError,
    SecretNames,
    agent_environ,
    apply_supervisor_environ,
    load_fleet_secrets,
    secret_names_from_env,
    supervisor_environ,
)


class _CannedAz:
    """Canned ``az`` stdout/exit keyed by a substring of the joined argv.

    Records every argv so tests assert the exact ``az`` invocation, and returns a
    configurable ``CompletedProcess`` per matched key — no live ``az`` (mirrors
    ``tests/test_sandbox.py``'s ``_CannedDocker``).
    """

    def __init__(self, *, results: dict[str, tuple[int, str, str]] | None = None) -> None:
        self._results = results or {}
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        arglist: list[str] = list(args)
        self.calls.append(arglist)
        joined: str = " ".join(arglist)
        for key, (code, out, err) in self._results.items():
            if key in joined:
                return subprocess.CompletedProcess(arglist, code, out, err)
        return subprocess.CompletedProcess(arglist, 0, "", "")


def _default_secrets() -> FleetSecrets:
    return FleetSecrets(anthropic_api_key="sk-ant-xxx", ado_pat="pat-yyy")


# --- AzKeyVaultSecrets --------------------------------------------------------


def test_get_logs_in_once_then_reads_secret() -> None:
    az = _CannedAz(results={"secret show": (0, "the-value\n", "")})
    access = AzKeyVaultSecrets("fleet-kv", run=az)

    assert access.get("anthropic-api-key") == "the-value"
    assert access.get("fleet-ado-pat") == "the-value"

    logins: list[list[str]] = [c for c in az.calls if "login" in " ".join(c)]
    assert len(logins) == 1  # idempotent login across multiple reads
    assert logins[0] == ["login", "--identity", "--only-show-errors", "--output", "none"]


def test_get_builds_the_keyvault_show_argv() -> None:
    az = _CannedAz(results={"secret show": (0, "v", "")})
    access = AzKeyVaultSecrets("the-vault", run=az)

    access.get("anthropic-api-key")

    show: list[str] = az.calls[-1]
    assert show == [
        "keyvault",
        "secret",
        "show",
        "--vault-name",
        "the-vault",
        "--name",
        "anthropic-api-key",
        "--query",
        "value",
        "--output",
        "tsv",
    ]


def test_login_failure_raises_secret_fetch_error() -> None:
    az = _CannedAz(results={"login": (1, "", "ManagedIdentityCredential unavailable")})
    access = AzKeyVaultSecrets("v", run=az)

    with pytest.raises(SecretFetchError, match="az login --identity failed"):
        access.get("anthropic-api-key")


def test_secret_read_failure_raises_secret_fetch_error() -> None:
    az = _CannedAz(results={"secret show": (1, "", "Forbidden (no get permission)")})
    access = AzKeyVaultSecrets("v", run=az)

    with pytest.raises(SecretFetchError, match="az keyvault secret show"):
        access.get("fleet-ado-pat")


def test_empty_secret_value_raises() -> None:
    az = _CannedAz(results={"secret show": (0, "\n", "")})
    access = AzKeyVaultSecrets("v", run=az)

    with pytest.raises(SecretFetchError, match="is empty"):
        access.get("anthropic-api-key")


# --- load_fleet_secrets -------------------------------------------------------


def test_load_fleet_secrets_reads_both_named_secrets() -> None:
    az = _CannedAz(
        results={
            "--name anthropic-api-key": (0, "sk-ant-xxx", ""),
            "--name fleet-ado-pat": (0, "pat-yyy", ""),
        }
    )
    access = AzKeyVaultSecrets("v", run=az)

    secrets = load_fleet_secrets(access)

    assert secrets == FleetSecrets(anthropic_api_key="sk-ant-xxx", ado_pat="pat-yyy")


def test_secret_names_from_env_overrides_defaults() -> None:
    names = secret_names_from_env(
        {"FLEET_ANTHROPIC_SECRET_NAME": "anthropic-prod", "FLEET_ADO_PAT_SECRET_NAME": "pat-prod"}
    )
    assert names == SecretNames(anthropic="anthropic-prod", ado_pat="pat-prod")


def test_secret_names_from_env_falls_back_to_defaults() -> None:
    assert secret_names_from_env({}) == SecretNames()


# --- env projections — the PAT-exclusion boundary -----------------------------


def test_supervisor_environ_carries_both_secrets() -> None:
    env = supervisor_environ(_default_secrets())
    assert env == {ANTHROPIC_API_KEY_ENV: "sk-ant-xxx", ADO_PAT_ENV: "pat-yyy"}


def test_agent_environ_carries_only_the_api_key() -> None:
    env = agent_environ(_default_secrets())
    assert env == {ANTHROPIC_API_KEY_ENV: "sk-ant-xxx"}


def test_agent_environ_never_exposes_the_pat() -> None:
    # The load-bearing negative property (ADR-0002 §11): the PAT must not reach
    # the agent — neither as a key nor as a value.
    secrets = FleetSecrets(anthropic_api_key="sk-ant-xxx", ado_pat="super-secret-pat")
    env = agent_environ(secrets)

    assert ADO_PAT_ENV not in env
    assert "super-secret-pat" not in env.values()


def test_apply_supervisor_environ_mutates_target_in_place() -> None:
    env: dict[str, str] = {}
    apply_supervisor_environ(_default_secrets(), env)
    assert env[ANTHROPIC_API_KEY_ENV] == "sk-ant-xxx"
    assert env[ADO_PAT_ENV] == "pat-yyy"
