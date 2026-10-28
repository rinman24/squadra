"""Host-side git remote auth: the one boundary that answers "authenticate to this remote".

Every host-side git op that talks to a remote — the app-repo bootstrap clone and
fetch, the claim's worktree fetch, the supervisor's ``ls-remote`` preflight, and
(when wired) the increment push — takes its credential from
:func:`credential_for`, keyed on the remote URL's **host and transport**, never
on ``[board].provider``. The board and the repository are independent: an Azure
DevOps board over a GitHub repository is a valid fleet, and its git ops need
GitHub's credential.

The credential is resolved per remote:

- **Azure DevOps over HTTPS** (``dev.azure.com``, ``<org>.visualstudio.com``):
  the PAT in ``$AZURE_DEVOPS_EXT_PAT`` via an env-var credential helper. The
  helper carries only the variable *reference*, never its value, so the PAT
  never reaches argv or ``.git/config``.
- **github.com over HTTPS**: the gh CLI's own git credential helper
  (``gh auth git-credential``), the same auth the GitHub board adapter uses
  (``GH_TOKEN``/``GITHUB_TOKEN``, else the ``gh auth login`` session). squadra
  adds no secret of its own for it.
- **SSH to either host** (``git@github.com:…``, ``ssh.dev.azure.com``,
  ``vs-ssh.visualstudio.com``): credential helpers do not apply to SSH, so the
  credential is the host's ambient ssh key (ssh-agent / ``~/.ssh``) and no
  ``-c`` override is added.

Both HTTPS credentials first reset every inherited helper (``credential.helper=``)
so the op uses exactly the named credential and never a keychain or other
helper the host happens to have. Any other remote (another host, plain
``http``, a local path) has no credential: :func:`credential_for` returns
``None`` and the caller fails closed.

:func:`remote_git_argv` is the one argv builder for an authenticated host-side
op. It wraps :func:`squadra.git_host.host_git_argv`, so the #193 hooks guard
travels with every op; a future push MUST be built as
``remote_git_argv(credential, "push", "origin", branch, work_dir=worktree)``.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import re
from typing import Final
from urllib.parse import SplitResult, urlsplit

from squadra.git_host import host_git_argv


class CredentialKind(StrEnum):
    """Which credential host-side git presents to a remote."""

    ADO_PAT = "ado-pat"
    GITHUB_GH = "github-gh"
    SSH = "ssh"


@dataclass(frozen=True, slots=True)
class RemoteCredential:
    """How host-side git authenticates to one remote.

    ``git_config`` is the ``-c`` overrides that supply the credential (empty for
    SSH); ``host`` is the remote's host, never its full URL (an HTTPS URL can
    carry a token in its userinfo, so it is not logged).
    """

    kind: CredentialKind
    host: str
    git_config: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RemoteAuthProbe:
    """The outcome of one preflight against the target remote.

    ``credential`` is ``None`` when no remote resolved or its host has no
    credential (the probe then fails closed without running ``ls-remote``).
    ``host`` is the remote's host when one parsed, for the operator's message.
    """

    ok: bool
    host: str | None = None
    credential: RemoteCredential | None = None


# Reset any inherited helper, then one that reads the PAT from the environment.
ADO_PAT_HELPER: Final[tuple[str, ...]] = (
    "-c",
    "credential.helper=",
    "-c",
    'credential.helper=!f() { echo username=pat; echo "password=$AZURE_DEVOPS_EXT_PAT"; }; f',
)

# Reset any inherited helper, then the gh CLI's (it reads GH_TOKEN, else gh's login).
GITHUB_GH_HELPER: Final[tuple[str, ...]] = (
    "-c",
    "credential.helper=",
    "-c",
    "credential.helper=!gh auth git-credential",
)

_GITHUB_HOST: Final[str] = "github.com"
_ADO_HTTPS_HOST: Final[str] = "dev.azure.com"
_LEGACY_ADO_SUFFIX: Final[str] = ".visualstudio.com"
_ADO_SSH_HOSTS: Final[frozenset[str]] = frozenset({"ssh.dev.azure.com", "vs-ssh.visualstudio.com"})

# scp-like SSH: ``[user@]host:path``, where the path does not start with ``//``.
_SCP_LIKE: Final[re.Pattern[str]] = re.compile(r"^(?:[^@/:]+@)?([^@/:]+):(?!//)")


def remote_host(url: str) -> tuple[str, str] | None:
    """Return ``(transport, host)`` of a remote URL, lowercased, or ``None``.

    ``transport`` is ``"https"``, ``"http"`` or ``"ssh"``; scp-like ``user@host:path``
    reads as ``"ssh"``. A local path, ``file://`` or any other scheme is ``None``.
    """
    raw: str = url.strip()
    if "://" in raw:
        parts: SplitResult = urlsplit(raw)
        scheme: str = parts.scheme.lower()
        host: str | None = parts.hostname
        if scheme not in {"https", "http", "ssh"} or not host:
            return None
        return scheme, host.lower()
    scp: re.Match[str] | None = _SCP_LIKE.match(raw)
    if scp is None:
        return None
    return "ssh", scp.group(1).lower()


def credential_for(remote_url: str) -> RemoteCredential | None:
    """Return the credential host-side git uses for ``remote_url``, or ``None``.

    Keyed on the remote's host and transport alone (see the module docstring).
    ``None`` — an unknown host, plain ``http``, a local path — means there is no
    credential to use and the caller must fail closed.
    """
    parsed: tuple[str, str] | None = remote_host(remote_url)
    if parsed is None:
        return None
    transport, host = parsed
    if transport == "https":
        if host == _ADO_HTTPS_HOST or host.endswith(_LEGACY_ADO_SUFFIX):
            return RemoteCredential(CredentialKind.ADO_PAT, host, ADO_PAT_HELPER)
        if host == _GITHUB_HOST:
            return RemoteCredential(CredentialKind.GITHUB_GH, host, GITHUB_GH_HELPER)
        return None
    if transport == "ssh" and (host == _GITHUB_HOST or host in _ADO_SSH_HOSTS):
        return RemoteCredential(CredentialKind.SSH, host, ())
    return None


def remote_git_argv(
    credential: RemoteCredential, *args: str, work_dir: str | Path | None = None
) -> list[str]:
    """Build a hardened host-side ``git`` argv that authenticates with ``credential``.

    The hooks guard (and, with ``work_dir``, the narrow ``safe.directory`` and
    ``-C``) come first, then the credential's ``-c`` overrides, then ``args``.
    """
    return host_git_argv(*credential.git_config, *args, work_dir=work_dir)
