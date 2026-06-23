"""Tests for host-side git hardening (Issue #193) — the sandbox-escape control.

Two layers:

1. Unit tests of :mod:`flotilla.git_host` argv construction — every host-side
   git argv pins ``core.hooksPath=/dev/null``; a checkout op additionally carries
   a ``safe.directory`` scoped to that exact path and **never** the ``*`` wildcard.

2. End-to-end regression tests against a **real** temp git repo with an
   agent-planted hook, driven through the production adapters
   (:class:`flotilla.worktree.GitWorktreeAccess`,
   :class:`flotilla.cleanup.DeterministicCleanup`) with their real ``_run_quiet``
   runner — no fake git. They assert a planted ``reference-transaction`` hook does
   **not** fire when a host-side ref-touching op (worktree-add branch create,
   branch delete) runs, and that a planted ``pre-push`` hook does **not** fire on a
   host-side ``git push`` built through ``host_git_argv`` — the credential-holding
   op the wired push path must use. Each has a control proving the hook *does* fire
   without the guard, so the tests cannot silently pass for the wrong reason.
"""

from collections.abc import Sequence
from pathlib import Path
import subprocess

from flotilla.cleanup import DeterministicCleanup
from flotilla.git_host import HOOKS_GUARD, host_git_argv, with_hooks_guard
from flotilla.worktree import GitWorktreeAccess

# --- unit: argv construction -------------------------------------------------


def test_host_git_argv_pins_hooks_path_first() -> None:
    argv: list[str] = host_git_argv("status")
    assert argv[:3] == ["git", "-c", "core.hooksPath=/dev/null"]
    assert HOOKS_GUARD == ("-c", "core.hooksPath=/dev/null")


def test_host_git_argv_scopes_safe_directory_to_the_exact_path() -> None:
    argv: list[str] = host_git_argv("fetch", "origin", work_dir="/fleet/home")
    assert "-c" in argv and "safe.directory=/fleet/home" in argv
    # the scope is the exact path — NEVER the global wildcard.
    assert "safe.directory=*" not in argv
    # and it runs against that path.
    assert argv[-4:] == ["-C", "/fleet/home", "fetch", "origin"]


def test_host_git_argv_omits_safe_directory_without_a_work_dir() -> None:
    # ls-remote / clone touch no local checkout, so no dubious-ownership exemption.
    argv: list[str] = host_git_argv("ls-remote", "https://example/repo")
    assert not any(tok.startswith("safe.directory") for tok in argv)
    assert "-C" not in argv
    # the hooks guard is still pinned.
    assert "core.hooksPath=/dev/null" in argv


def test_with_hooks_guard_interleaves_extra_config_after_the_guard() -> None:
    argv: list[str] = with_hooks_guard(("-c", "credential.helper=x"), "clone", "url", "dest")
    assert argv[:3] == ["git", "-c", "core.hooksPath=/dev/null"]
    assert argv[3:] == ["-c", "credential.helper=x", "clone", "url", "dest"]
    assert "safe.directory=*" not in argv


# --- end-to-end regression: a planted hook must not fire host-side -----------

_HOOK_MARKER: str = "HOOK_FIRED"


def _run_quiet(args: Sequence[str]) -> int:
    """Production-style runner: run argv, discard output, return the exit code."""
    return subprocess.run(list(args), capture_output=True, check=False).returncode


def _git(*args: str) -> None:
    """Run a setup-helper git command (real git), raising on failure."""
    subprocess.run(["git", *args], check=True, capture_output=True)


def _init_repo(path: Path) -> None:
    """Create a real checkout with an ``origin`` remote and one commit.

    A bare ``origin`` is created alongside and the checkout clones from it, so a
    host-side ``fetch origin`` (the first step of ``GitWorktreeAccess.create``)
    succeeds — the test exercises the real adapter path, not a short-circuit.
    """
    origin: Path = path.parent / f"{path.name}-origin.git"
    _git("init", "-q", "--bare", str(origin))
    _git("clone", "-q", str(origin), str(path))
    for key, value in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
        _git("-C", str(path), "config", key, value)
    _git("-C", str(path), "commit", "--allow-empty", "-qm", "init")
    _git("-C", str(path), "push", "-q", "origin", "HEAD")


def _plant_reference_transaction_hook(repo: Path) -> Path:
    """Plant a hook that touches a marker file whenever a ref transaction runs.

    ``reference-transaction`` fires on any ref update — a branch create
    (``worktree add -b``) or a branch delete (``branch -D``) triggers it — so it
    is a faithful stand-in for a malicious agent-planted hook on the host-side
    git path. Returns the marker path the hook would create.
    """
    hooks_dir: Path = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    marker: Path = repo / _HOOK_MARKER
    hook: Path = hooks_dir / "reference-transaction"
    hook.write_text(f'#!/bin/sh\necho FIRED > "{marker}"\n')
    hook.chmod(0o755)
    return marker


def test_control_hook_fires_without_the_guard(tmp_path: Path) -> None:
    # Sanity control: with a plain `git branch`, the planted hook DOES fire.
    # If this ever stops firing the regression tests below would pass vacuously.
    repo: Path = tmp_path / "repo"
    _init_repo(repo)
    marker: Path = _plant_reference_transaction_hook(repo)
    subprocess.run(["git", "-C", str(repo), "branch", "control-branch"], check=True)
    assert marker.exists(), "control: planted hook should fire on an unguarded git op"


def test_worktree_create_does_not_fire_a_planted_hook(tmp_path: Path) -> None:
    repo: Path = tmp_path / "repo"
    _init_repo(repo)
    marker: Path = _plant_reference_transaction_hook(repo)

    wt = GitWorktreeAccess(fleet_home=repo, run=_run_quiet)
    # worktree add -b creates a branch ref -> would fire reference-transaction.
    result = wt.create(
        branch="feat/slice-x",
        worktree=str(tmp_path / "wt-x"),
        base_ref="HEAD",
    )

    assert result.created is True
    assert not marker.exists(), "host-side worktree create must NOT run a planted hook"


def test_branch_delete_does_not_fire_a_planted_hook(tmp_path: Path) -> None:
    repo: Path = tmp_path / "repo"
    _init_repo(repo)
    # create a branch to delete, BEFORE planting the hook (so creation is clean)
    subprocess.run(["git", "-C", str(repo), "branch", "feat/doomed"], check=True)
    marker: Path = _plant_reference_transaction_hook(repo)

    cleanup = DeterministicCleanup(fleet_home=repo, run=_run_quiet)
    # branch -D deletes a ref -> would fire reference-transaction.
    assert cleanup.delete_branch("feat/doomed") is True
    assert not marker.exists(), "host-side branch delete must NOT run a planted hook"


def test_host_side_op_survives_dubious_ownership_scenario(tmp_path: Path) -> None:
    # The safe.directory exemption is scoped to the repo path, so a host-side op
    # against a checkout works even though it never globally trusts any repo.
    # (Ownership cannot be changed in-test without root; assert the narrow scope
    # is what reaches git — proven via the argv builder the adapters now use.)
    repo: Path = tmp_path / "repo"
    _init_repo(repo)
    argv: list[str] = host_git_argv("worktree", "prune", work_dir=repo)
    assert f"safe.directory={repo}" in argv
    assert "safe.directory=*" not in argv


# --- end-to-end regression: a planted pre-push hook must not fire on a push ---
#
# push is the credential-holding host-side op at the centre of the #193 threat
# model: the agent commits in the bind-mounted worktree (and could plant a
# ``pre-push`` hook there), then the *supervisor* pushes host-side while holding
# the PAT. ``pre-push`` fires on ``git push`` specifically — the one hook verb the
# ref-transaction tests above do not exercise. The push path, when wired, MUST
# build its argv through ``host_git_argv("push", …, work_dir=worktree)`` (the same
# builder fetch / worktree / branch-delete already use); these tests prove that
# contract neutralizes a planted ``pre-push`` hook, with a control proving the
# hook fires on a bare push so the regression cannot pass vacuously.

_PRE_PUSH_MARKER: str = "PRE_PUSH_FIRED"


def _plant_pre_push_hook(repo: Path) -> Path:
    """Plant a ``pre-push`` hook that touches a marker, as a malicious agent would.

    Returns the marker path the hook would create when ``git push`` runs.
    """
    hooks_dir: Path = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    marker: Path = repo / _PRE_PUSH_MARKER
    hook: Path = hooks_dir / "pre-push"
    hook.write_text(f'#!/bin/sh\necho FIRED > "{marker}"\n')
    hook.chmod(0o755)
    return marker


def test_control_pre_push_hook_fires_on_an_unguarded_push(tmp_path: Path) -> None:
    # Sanity control: a bare `git push` DOES fire the planted pre-push hook. If
    # this ever stops firing the regression below would pass vacuously.
    repo: Path = tmp_path / "repo"
    _init_repo(repo)
    marker: Path = _plant_pre_push_hook(repo)
    _git("-C", str(repo), "commit", "--allow-empty", "-qm", "slice work")

    subprocess.run(
        ["git", "-C", str(repo), "push", "-q", "origin", "HEAD:refs/heads/control"],
        check=True,
    )
    assert marker.exists(), "control: a planted pre-push hook should fire on an unguarded push"


def test_host_side_push_does_not_fire_a_planted_pre_push_hook(tmp_path: Path) -> None:
    repo: Path = tmp_path / "repo"
    _init_repo(repo)
    marker: Path = _plant_pre_push_hook(repo)
    _git("-C", str(repo), "commit", "--allow-empty", "-qm", "slice work")

    # Built through host_git_argv, the push argv pins core.hooksPath=/dev/null, so
    # the pre-push hook planted in the worktree cannot fire in the host context.
    rc: int = _run_quiet(host_git_argv("push", "origin", "HEAD:refs/heads/slice-x", work_dir=repo))

    assert rc == 0, "host-side push must succeed"
    assert not marker.exists(), "host-side push must NOT run a planted pre-push hook"
