"""Unit tests for the host-side app-repo bootstrap (``flotilla.repo``).

Drives :func:`ensure_app_repo` against a canned git runner (no live git) and a
temp ``fleet_home`` to cover the clone-if-absent / fetch-(reset)-if-present
branches, the dry-run no-clobber path, and the load-bearing security properties:
the PAT never appears in argv, and every host-side op pins ``core.hooksPath``.
"""

from collections.abc import Sequence
from pathlib import Path
import subprocess

from flotilla.repo import ensure_app_repo, ensure_app_repo_from_env

_REPO_URL = "https://dev.azure.com/your-org/example-project/_git/app-backend"


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
    home: Path = tmp_path / "app-backend"

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
    home: Path = tmp_path / "app-backend"

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
