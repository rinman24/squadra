"""Host-side secret bootstrap for the fleet-host (ADR-0002 §11, decision 11).

On the dedicated fleet-host VM the supervisor authenticates as the VM's
*managed identity* and reads the fleet's two secrets from Azure Key Vault: the
Anthropic API key the contained agent needs, and the Azure DevOps PAT the
supervisor's :class:`~squadra.board.BoardAccess` uses for host-side
board/remote writes. No secret is baked into an image, a unit file, or the
environment of the VM at provisioning time — they are pulled at tick time.

Two boundaries are load-bearing (ADR-0002 §11):

- **The PAT stays supervisor-side.** It is placed in the supervisor process's
  own environment (for ``BoardAccess``) and is *never* put on any agent-facing
  surface. :func:`agent_environ` is the projection that enforces this — it
  carries ONLY ``ANTHROPIC_API_KEY``; the negative-property contract test pins
  the PAT's absence.
- **Secrets never touch disk.** They are fetched into the *process* environment
  and the fleet-tick entry execs the tick in-process — there is no systemd
  ``EnvironmentFile`` written to the filesystem.

The fetch seam (:class:`SecretAccess`) is a provider-neutral ``Protocol`` and
the concrete :class:`AzKeyVaultSecrets` adapter injects its ``az`` command
runner (the test seam, mirroring :class:`squadra.sandbox.ComposeSandbox`'s
``DockerRun``), so unit tests need no live ``az``/IMDS/Key Vault.
"""

from collections.abc import Callable, MutableMapping, Sequence
from dataclasses import dataclass
import os
import subprocess
from typing import Final, Protocol

# The two environment variables the fleet's secrets land in.
ANTHROPIC_API_KEY_ENV: Final[str] = "ANTHROPIC_API_KEY"
ADO_PAT_ENV: Final[str] = "AZURE_DEVOPS_EXT_PAT"

# Key Vault secret names (the live coordinates).
DEFAULT_ANTHROPIC_SECRET_NAME: Final[str] = "anthropic-api-key"
DEFAULT_ADO_PAT_SECRET_NAME: Final[str] = "fleet-ado-pat"

# Env knobs that point the fleet-tick bootstrap at the VM's vault / secret names.
KEY_VAULT_ENV: Final[str] = "FLEET_KEY_VAULT"
ANTHROPIC_SECRET_NAME_ENV: Final[str] = "FLEET_ANTHROPIC_SECRET_NAME"
ADO_PAT_SECRET_NAME_ENV: Final[str] = "FLEET_ADO_PAT_SECRET_NAME"


class SecretFetchError(RuntimeError):
    """Raised when managed-identity login or a Key Vault secret read fails."""


class SecretAccess(Protocol):
    """The provider-neutral secret read the fleet-host bootstrap depends on."""

    def get(self, name: str) -> str:
        """Return the value of secret ``name``, or raise :class:`SecretFetchError`."""
        ...


# The az command-runner seam: an ``az`` argv in, the completed process out (cf.
# ``DockerRun`` in ``sandbox.py`` and ``AzCliAdo``'s runner in ``board.py``).
AzRun = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def _run_az(args: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    """Run an ``az`` command, capturing output; never raises on a non-zero exit.

    A non-zero exit is information the adapter classifies into
    :class:`SecretFetchError` (so the smoke surfaces *why* a fetch failed), not
    an exception to leak raw — hence ``check=False``.
    """
    return subprocess.run(["az", *args], capture_output=True, text=True, check=False)


class AzKeyVaultSecrets:
    """:class:`SecretAccess` backed by ``az`` + the VM's managed identity.

    The first :meth:`get` lazily runs ``az login --identity`` (the VM's
    system-assigned identity → IMDS token), then each read is ``az keyvault
    secret show`` against the configured vault. The command runner is injected
    (the DI test seam) so unit tests use a canned runner — no live ``az`` /
    IMDS / Key Vault.
    """

    def __init__(self, vault: str, *, run: AzRun = _run_az) -> None:
        """Wire the adapter to a vault name and its ``az`` command runner."""
        self._vault = vault
        self._run = run
        self._logged_in = False

    def _login(self) -> None:
        """Authenticate as the VM managed identity once (idempotent per instance)."""
        if self._logged_in:
            return
        completed = self._run(["login", "--identity", "--only-show-errors", "--output", "none"])
        if completed.returncode != 0:
            raise SecretFetchError(
                f"az login --identity failed (managed identity not assigned / IMDS "
                f"unreachable?): {completed.stderr.strip()}"
            )
        self._logged_in = True

    def get(self, name: str) -> str:
        """Read secret ``name`` from the vault (logging in on first use)."""
        self._login()
        completed = self._run(
            [
                "keyvault",
                "secret",
                "show",
                "--vault-name",
                self._vault,
                "--name",
                name,
                "--query",
                "value",
                "--output",
                "tsv",
            ]
        )
        if completed.returncode != 0:
            raise SecretFetchError(
                f"az keyvault secret show {name!r} from vault {self._vault!r} failed "
                f"(identity lacks 'get' on the vault?): {completed.stderr.strip()}"
            )
        value: str = completed.stdout.strip()
        if not value:
            raise SecretFetchError(f"Key Vault secret {name!r} in vault {self._vault!r} is empty")
        return value


@dataclass(frozen=True, slots=True)
class SecretNames:
    """The Key Vault secret names the fleet reads (defaults = live coordinates)."""

    anthropic: str = DEFAULT_ANTHROPIC_SECRET_NAME
    ado_pat: str = DEFAULT_ADO_PAT_SECRET_NAME


@dataclass(frozen=True, slots=True)
class FleetSecrets:
    """The fleet's two resolved secret values (held only in memory)."""

    anthropic_api_key: str
    ado_pat: str


def secret_names_from_env(
    environ: MutableMapping[str, str] | None = None,
) -> SecretNames:
    """Resolve the Key Vault secret names from the environment (else defaults)."""
    env: MutableMapping[str, str] = os.environ if environ is None else environ
    return SecretNames(
        anthropic=env.get(ANTHROPIC_SECRET_NAME_ENV) or DEFAULT_ANTHROPIC_SECRET_NAME,
        ado_pat=env.get(ADO_PAT_SECRET_NAME_ENV) or DEFAULT_ADO_PAT_SECRET_NAME,
    )


def load_fleet_secrets(access: SecretAccess, names: SecretNames | None = None) -> FleetSecrets:
    """Read both fleet secrets through ``access`` (raising on any fetch failure)."""
    resolved: SecretNames = names if names is not None else SecretNames()
    return FleetSecrets(
        anthropic_api_key=access.get(resolved.anthropic),
        ado_pat=access.get(resolved.ado_pat),
    )


def supervisor_environ(secrets: FleetSecrets) -> dict[str, str]:
    """Return the secrets the *supervisor* process needs: the PAT + the API key.

    The PAT is the supervisor's own credential for all host-side board/remote
    writes; the API key is carried here too only so the supervisor can hand it
    down to the agent via the compose ``agent`` service env. See
    :func:`agent_environ` for the narrower set the agent itself may see.
    """
    return {
        ANTHROPIC_API_KEY_ENV: secrets.anthropic_api_key,
        ADO_PAT_ENV: secrets.ado_pat,
    }


def agent_environ(secrets: FleetSecrets) -> dict[str, str]:
    """Return the only secrets the contained agent may see: the Anthropic API key.

    The PAT is deliberately excluded — the agent is commit-only and never
    touches the board or the remote (ADR-0002 §11). This projection *is* the
    enforced boundary; the contract test asserts the PAT neither keys nor values
    into the result.
    """
    return {ANTHROPIC_API_KEY_ENV: secrets.anthropic_api_key}


def apply_supervisor_environ(
    secrets: FleetSecrets,
    environ: MutableMapping[str, str] | None = None,
) -> None:
    """Inject the supervisor's secrets into the process environment, in place.

    Applied by the fleet-tick entry before it runs the tick, so the values live
    only in this process's environment for the lifetime of the tick — never in a
    file on disk (ADR-0002 §11).
    """
    target: MutableMapping[str, str] = os.environ if environ is None else environ
    target.update(supervisor_environ(secrets))
