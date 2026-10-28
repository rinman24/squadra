"""Unit tests for the host-side git credential boundary (``squadra.remote_auth``, SQ7).

The credential follows the target remote's host and transport, never the board
provider. These tests pin the per-host resolution, fail-closed on any other
remote, and the property the preflight exists for: the supervisor's probe
(:meth:`squadra.repo.TargetRemote.probe`) and the claim's worktree fetch
(:meth:`squadra.worktree.GitWorktreeAccess.create`) present the same credential
for ADO HTTPS, GitHub HTTPS and SSH remotes. No live network: git runners are
stubbed, and the one real-git test only reads a local ``origin``.
"""

from collections.abc import Callable, Sequence
from pathlib import Path
import subprocess

import pytest

from squadra import repo, supervisor
from squadra.config import SquadraConfig
from squadra.remote_auth import (
    ADO_PAT_HELPER,
    GITHUB_GH_HELPER,
    CredentialKind,
    RemoteAuthProbe,
    RemoteCredential,
    credential_for,
    remote_git_argv,
)
from squadra.repo import TargetRemote
from squadra.supervisor import build_seams
from squadra.worktree import GitWorktreeAccess
from tests.helpers.fleet_fakes import FakeBoard

_ADO_HTTPS = "https://your-org@dev.azure.com/your-org/example-project/_git/app-backend"
_GITHUB_HTTPS = "https://github.com/owner/app.git"
_GITHUB_SSH = "git@github.com:owner/app.git"
_ADO_SSH = "git@ssh.dev.azure.com:v3/your-org/example-project/app-backend"


# --- resolution by host and transport -------------------------------------------


@pytest.mark.parametrize(
    ("url", "kind", "host", "git_config"),
    [
        (_ADO_HTTPS, CredentialKind.ADO_PAT, "dev.azure.com", ADO_PAT_HELPER),
        (
            "https://your-org.visualstudio.com/DefaultCollection/proj/_git/app",
            CredentialKind.ADO_PAT,
            "your-org.visualstudio.com",
            ADO_PAT_HELPER,
        ),
        (_GITHUB_HTTPS, CredentialKind.GITHUB_GH, "github.com", GITHUB_GH_HELPER),
        ("https://GitHub.com/Owner/App", CredentialKind.GITHUB_GH, "github.com", GITHUB_GH_HELPER),
        (_GITHUB_SSH, CredentialKind.SSH, "github.com", ()),
        ("ssh://git@github.com/owner/app.git", CredentialKind.SSH, "github.com", ()),
        (_ADO_SSH, CredentialKind.SSH, "ssh.dev.azure.com", ()),
        (
            "your-org@vs-ssh.visualstudio.com:v3/your-org/proj/app",
            CredentialKind.SSH,
            "vs-ssh.visualstudio.com",
            (),
        ),
    ],
)
def test_credential_follows_the_remote_host(
    url: str, kind: CredentialKind, host: str, git_config: tuple[str, ...]
) -> None:
    assert credential_for(url) == RemoteCredential(kind, host, git_config)


@pytest.mark.parametrize(
    "url",
    [
        "https://gitlab.example.com/owner/app.git",  # an unknown HTTPS host
        "http://github.com/owner/app.git",  # never send a credential over plain http
        "http://dev.azure.com/your-org/proj/_git/app",
        "git@gitlab.example.com:owner/app.git",  # an unknown SSH host
        "/srv/git/app.git",  # a local path
        "file:///srv/git/app.git",
        "",
    ],
)
def test_any_other_remote_has_no_credential(url: str) -> None:
    assert credential_for(url) is None


def test_ado_helper_is_byte_for_byte_the_pre_sq7_helper() -> None:
    # An ADO remote's auth is unchanged: the same reset-then-env-var helper.
    assert ADO_PAT_HELPER == (
        "-c",
        "credential.helper=",
        "-c",
        'credential.helper=!f() { echo username=pat; echo "password=$AZURE_DEVOPS_EXT_PAT"; }; f',
    )


def test_github_helper_resets_inherited_helpers_then_uses_gh() -> None:
    # No keychain or other ambient helper can answer in gh's place.
    assert GITHUB_GH_HELPER == (
        "-c",
        "credential.helper=",
        "-c",
        "credential.helper=!gh auth git-credential",
    )


def test_remote_git_argv_keeps_the_hooks_guard_first() -> None:
    credential: RemoteCredential | None = credential_for(_GITHUB_HTTPS)
    assert credential is not None
    argv: list[str] = remote_git_argv(credential, "fetch", "origin", work_dir="/repo")
    assert argv[:7] == [
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "safe.directory=/repo",
        "-C",
        "/repo",
    ]
    assert argv[7:] == [*GITHUB_GH_HELPER, "fetch", "origin"]


# --- the probe and the claim's fetch share one credential ----------------------


class _Origin:
    """Git runner answering ``remote get-url origin`` with a fixed URL (or none)."""

    def __init__(self, url: str | None) -> None:
        self._url = url

    def __call__(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        if self._url is None:
            return subprocess.CompletedProcess(list(args), 2, "", "")
        return subprocess.CompletedProcess(list(args), 0, self._url + "\n", "")


class _Recorder:
    """Git runner recording every argv and exiting 0."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        return subprocess.CompletedProcess(list(args), 0, "", "")

    def code(self, args: Sequence[str]) -> int:
        """The int-returning shape ``GitWorktreeAccess`` runs."""
        return self(args).returncode


def _credential_tokens(argv: Sequence[str], verb: str) -> list[str]:
    """The ``-c`` overrides between the host-side guards and ``verb``."""
    tokens: list[str] = list(argv[: list(argv).index(verb)])
    start: int = tokens.index("-C") + 2 if "-C" in tokens else 3
    return tokens[start:]


@pytest.mark.parametrize(
    ("url", "expected"),
    [(_ADO_HTTPS, ADO_PAT_HELPER), (_GITHUB_HTTPS, GITHUB_GH_HELPER), (_GITHUB_SSH, ())],
)
def test_probe_and_worktree_fetch_present_the_same_credential(
    tmp_path: Path, url: str, expected: tuple[str, ...]
) -> None:
    probe_git = _Recorder()
    remote = TargetRemote(tmp_path, run=_Origin(url), probe_run=probe_git, environ={})
    fetch_git = _Recorder()
    worktree = GitWorktreeAccess(tmp_path, run=fetch_git.code, credential=remote.credential)

    probe: RemoteAuthProbe = remote.probe()
    assert worktree.create(branch="feat/x", worktree=str(tmp_path / "wt")).created is True

    assert probe.ok is True
    assert probe.credential == credential_for(url)
    probe_config: list[str] = _credential_tokens(probe_git.calls[0], "ls-remote")
    fetch_config: list[str] = _credential_tokens(fetch_git.calls[0], "fetch")
    assert probe_config == fetch_config == list(expected)
    assert probe_git.calls[0][-2:] == ["ls-remote", url]


def test_unknown_host_fails_closed_for_probe_and_fetch(tmp_path: Path) -> None:
    probe_git = _Recorder()
    remote = TargetRemote(
        tmp_path,
        run=_Origin("https://gitlab.example.com/owner/app.git"),
        probe_run=probe_git,
        environ={},
    )
    fetch_git = _Recorder()
    worktree = GitWorktreeAccess(tmp_path, run=fetch_git.code, credential=remote.credential)

    assert remote.probe() == RemoteAuthProbe(ok=False, host="gitlab.example.com")
    assert worktree.create(branch="feat/x", worktree=str(tmp_path / "wt")).created is False
    assert probe_git.calls == []  # no ls-remote without a credential
    assert fetch_git.calls == []  # and no fetch: the worktree is never added


def test_no_remote_fails_closed(tmp_path: Path) -> None:
    probe_git = _Recorder()
    remote = TargetRemote(tmp_path, run=_Origin(None), probe_run=probe_git, environ={})
    assert remote.probe() == RemoteAuthProbe(ok=False)
    assert remote.credential() is None
    assert probe_git.calls == []


def test_rejected_credential_fails_the_probe_and_names_it(tmp_path: Path) -> None:
    def _rejected(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(args), 128, "", "Authentication failed")

    remote = TargetRemote(tmp_path, run=_Origin(_GITHUB_HTTPS), probe_run=_rejected, environ={})
    probe: RemoteAuthProbe = remote.probe()
    assert probe.ok is False
    assert probe.credential == credential_for(_GITHUB_HTTPS)


def test_probe_never_carries_the_remote_url_in_its_result(tmp_path: Path) -> None:
    # An HTTPS remote can carry a token in its userinfo; only the host is reported.
    url: str = "https://x-access-token:SECRET@github.com/owner/app.git"
    remote = TargetRemote(tmp_path, run=_Origin(url), probe_run=_Recorder(), environ={})
    assert "SECRET" not in repr(remote.probe())


# --- the board provider does not pick the credential -----------------------------


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True)


@pytest.mark.parametrize(
    ("provider", "origin", "kind"),
    [
        ("github", _ADO_HTTPS, CredentialKind.ADO_PAT),  # a GitHub board over an ADO repo
        ("ado", _GITHUB_HTTPS, CredentialKind.GITHUB_GH),  # an ADO board over a GitHub repo
        ("ado", _ADO_HTTPS, CredentialKind.ADO_PAT),
        ("github", _GITHUB_HTTPS, CredentialKind.GITHUB_GH),
    ],
)
def test_production_seams_pick_the_credential_by_remote_not_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    make_config: Callable[..., SquadraConfig],
    fake_board: FakeBoard,
    provider: str,
    origin: str,
    kind: CredentialKind,
) -> None:
    home: Path = tmp_path / "checkout"
    _git("init", "-q", str(home))
    _git("-C", str(home), "remote", "add", "origin", origin)  # a local read, no network
    probe_git = _Recorder()
    monkeypatch.setattr(repo, "_run_git_probe", probe_git)

    def _board(_config: SquadraConfig) -> FakeBoard:
        return fake_board

    monkeypatch.setattr(supervisor, "build_board", _board)

    seams = build_seams(make_config(provider=provider, fleet_home=home))
    probe: RemoteAuthProbe = seams.remote_auth()

    assert probe.credential is not None
    assert probe.credential.kind is kind
    assert probe_git.calls[0][-2:] == ["ls-remote", origin]
