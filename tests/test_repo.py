"""Unit tests for the host-side app-repo bootstrap (``flotilla.repo``).

Drives :func:`ensure_app_repo` against a canned git runner (no live git) and a
temp ``fleet_home`` to cover the clone-if-absent / fetch-(reset)-if-present
branches, the dry-run no-clobber path, and the load-bearing security properties:
the PAT never appears in argv, and every host-side op pins ``core.hooksPath``.
Also covers the PAT auth probe (:func:`target_remote_url` / :func:`remote_auth_ok`)
the supervisor's claim-only preflight depends on.
"""

from collections.abc import Sequence
from pathlib import Path
import subprocess

from flotilla.repo import (
    ensure_app_repo,
    ensure_app_repo_from_env,
    remote_auth_ok,
    target_remote_url,
)

_REPO_URL = "https://dev.azure.com/genshift/gswa-dev/_git/gswa-backend"


class _CannedGit:
    """Records argv and returns a configurable exit per matched argv substring."""

    def __init__(self, *, results: dict[str, int] | None = None) -> None:
        self._results = results or {}
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        arglist: list[str] = list(args)
        self.calls.append(arglist)
        joined: str = " ".join(arglist)
        for key, code in self._results.items():
            if key in joined:
                return subprocess.CompletedProcess(arglist, code, "", "")
        return subprocess.CompletedProcess(arglist, 0, "", "")

    @property
    def verbs(self) -> list[str]:
        return [next((a for a in c if a in {"clone", "fetch", "reset"}), "") for c in self.calls]


def test_clones_when_absent(tmp_path: Path) -> None:
    git = _CannedGit()
    home: Path = tmp_path / "gswa-backend"

    assert ensure_app_repo(repo_url=_REPO_URL, fleet_home=home, run=git) is True

    assert git.verbs == ["clone"]
    clone: list[str] = git.calls[0]
    assert _REPO_URL in clone
    assert str(home) in clone


def test_fetch_and_reset_when_present(tmp_path: Path) -> None:
    git = _CannedGit()
    (tmp_path / ".git").mkdir()

    assert ensure_app_repo(repo_url=_REPO_URL, fleet_home=tmp_path, run=git) is True

    assert git.verbs == ["fetch", "reset"]
    assert git.calls[-1][-1] == "origin/main"


def test_dry_run_fetches_but_does_not_reset(tmp_path: Path) -> None:
    git = _CannedGit()
    (tmp_path / ".git").mkdir()

    assert ensure_app_repo(repo_url=_REPO_URL, fleet_home=tmp_path, mutate=False, run=git) is True

    assert git.verbs == ["fetch"]  # no reset --hard in a dry-run


def test_failed_fetch_short_circuits(tmp_path: Path) -> None:
    git = _CannedGit(results={"fetch": 1})
    (tmp_path / ".git").mkdir()

    assert ensure_app_repo(repo_url=_REPO_URL, fleet_home=tmp_path, run=git) is False
    assert git.verbs == ["fetch"]  # reset never attempted off a stale base


def test_base_branch_override(tmp_path: Path) -> None:
    git = _CannedGit()
    (tmp_path / ".git").mkdir()

    ensure_app_repo(repo_url=_REPO_URL, fleet_home=tmp_path, base_branch="develop", run=git)

    assert git.calls[-1][-1] == "origin/develop"


def test_pat_never_in_argv_and_hooks_pinned(tmp_path: Path) -> None:
    git = _CannedGit()
    home: Path = tmp_path / "gswa-backend"

    ensure_app_repo(repo_url=_REPO_URL, fleet_home=home, run=git)

    for call in git.calls:
        joined: str = " ".join(call)
        # The credential helper carries only the *variable reference*, never a value.
        assert "$AZURE_DEVOPS_EXT_PAT" in joined
        assert "credential.helper=" in joined
        # Every host-side git op defeats checkout-planted hooks (#193).
        assert "core.hooksPath=/dev/null" in call


def test_from_env_skips_when_unconfigured() -> None:
    git = _CannedGit()
    assert ensure_app_repo_from_env(run=git, environ={}) is None
    assert git.calls == []


def test_from_env_resolves_url_and_home(tmp_path: Path) -> None:
    git = _CannedGit()
    home: Path = tmp_path / "checkout"
    result = ensure_app_repo_from_env(
        run=git,
        environ={"FLEET_APP_REPO_URL": _REPO_URL, "FLEET_HOME": str(home)},
    )
    assert result is True
    assert git.verbs == ["clone"]


# --- PAT auth probe (target_remote_url / remote_auth_ok) ----------------------


class _StubGit:
    """Git runner returning a fixed (returncode, stdout); records argv."""

    def __init__(
        self, *, returncode: int = 0, stdout: str = "", raises: Exception | None = None
    ) -> None:
        self._returncode = returncode
        self._stdout = stdout
        self._raises = raises
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        if self._raises is not None:
            raise self._raises
        return subprocess.CompletedProcess(list(args), self._returncode, self._stdout, "")


def test_target_remote_url_prefers_fleet_home_origin(tmp_path: Path) -> None:
    git = _StubGit(returncode=0, stdout=_REPO_URL + "\n")
    # origin resolves -> the env fallback is never consulted.
    url = target_remote_url(tmp_path, run=git, environ={"FLEET_APP_REPO_URL": "https://other"})
    assert url == _REPO_URL
    call: list[str] = git.calls[0]
    # full host-side argv: hooks guard + narrow safe.directory + -C <path> + verb.
    assert call[0] == "git"
    assert "core.hooksPath=/dev/null" in call
    assert f"safe.directory={tmp_path}" in call
    assert "safe.directory=*" not in call
    ci: int = call.index("-C")
    assert call[ci : ci + 5] == ["-C", str(tmp_path), "remote", "get-url", "origin"]


def test_target_remote_url_falls_back_to_app_repo_url_env(tmp_path: Path) -> None:
    git = _StubGit(returncode=2, stdout="")  # no origin configured
    url = target_remote_url(tmp_path, run=git, environ={"FLEET_APP_REPO_URL": _REPO_URL})
    assert url == _REPO_URL


def test_target_remote_url_none_when_unresolved(tmp_path: Path) -> None:
    git = _StubGit(returncode=2, stdout="")
    assert target_remote_url(tmp_path, run=git, environ={}) is None


def test_remote_auth_ok_true_on_rc_zero() -> None:
    git = _StubGit(returncode=0)
    assert remote_auth_ok(_REPO_URL, run=git) is True
    call: list[str] = git.calls[0]
    assert call[-2:] == ["ls-remote", _REPO_URL]
    joined: str = " ".join(call)
    # The PAT travels via the credential helper's *variable reference*, never a value,
    # and the host-side hooks guard is pinned (matching ensure_app_repo).
    assert "$AZURE_DEVOPS_EXT_PAT" in joined
    assert "credential.helper=" in joined
    assert "core.hooksPath=/dev/null" in call


def test_remote_auth_ok_false_on_rejected_pat() -> None:
    git = _StubGit(returncode=128)  # git ls-remote auth failure
    assert remote_auth_ok(_REPO_URL, run=git) is False


def test_remote_auth_ok_false_on_timeout() -> None:
    git = _StubGit(raises=subprocess.TimeoutExpired(cmd="git", timeout=30))
    assert remote_auth_ok(_REPO_URL, run=git) is False


def test_remote_auth_ok_false_when_git_missing() -> None:
    git = _StubGit(raises=FileNotFoundError("git"))
    assert remote_auth_ok(_REPO_URL, run=git) is False
