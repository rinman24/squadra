"""Host-side app-repo bootstrap: keep ``FLEET_HOME`` a current checkout.

When INFRA #148 hand-provisioned the fleet-host, cloud-init never fired, so the
gswa-backend checkout the supervisor operates on (``FLEET_HOME``) had to be
cloned by hand (memory ``fleet-host-needs-manual-gswa-clone``). This module
removes that manual step: ``squadra fleet-tick`` calls :func:`ensure_app_repo`
before each tick, so the host self-heals to a fresh checkout — clone if absent,
fetch (+ reset to the remote base) if present.

Auth is HTTPS + PAT via an *env-var* credential helper: the PAT is read from the
process environment (``AZURE_DEVOPS_EXT_PAT``, applied by the fleet-tick secret
bootstrap) at git time and is **never** written to ``.git/config`` or any file —
the helper string carries only the variable *reference*, not its value (matching
the repo-local helper pattern in memory ``git-push-https-pat-not-ssh``). Every
host-side git argv here is built through :mod:`squadra.git_host`, so it pins
``core.hooksPath=/dev/null`` (and, on a checkout op, a narrowly-scoped
``safe.directory``) — the centralized #193 hardening that keeps a planted hook
from executing during a host-side op.
"""

from collections.abc import Callable, Mapping, Sequence
import os
from pathlib import Path
import subprocess
from typing import Final

from squadra.git_host import host_git_argv, with_hooks_guard

DEFAULT_BASE_BRANCH: Final[str] = "main"

# A PAT auth probe (`git ls-remote`) must fail fast, never hang: bound it so a
# stalled connection cannot stall the whole tick.
LS_REMOTE_TIMEOUT_SECONDS: Final[float] = 30.0

APP_REPO_URL_ENV: Final[str] = "FLEET_APP_REPO_URL"
FLEET_HOME_ENV: Final[str] = "FLEET_HOME"
BASE_BRANCH_ENV: Final[str] = "FLEET_BASE_BRANCH"

# Git command-runner seam (full argv incl. ``git`` -> completed process),
# injected for tests. The argv is built by :mod:`squadra.git_host` so every op
# carries the host-side hooks guard (and, on a checkout op, a narrow
# ``safe.directory``) — see that module for the #193 threat model.
GitRun = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]

# An env-var credential helper: reset any inherited helper, then one that reads
# the PAT from the environment. No secret is persisted — only the var reference.
_CREDENTIAL_HELPER: Final[tuple[str, ...]] = (
    "-c",
    "credential.helper=",
    "-c",
    'credential.helper=!f() { echo username=pat; echo "password=$AZURE_DEVOPS_EXT_PAT"; }; f',
)


def _run_git(args: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    """Run a full ``git`` argv capturing output; never raises on a non-zero exit."""
    return subprocess.run(list(args), capture_output=True, text=True, check=False)


def _run_git_probe(args: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    """Run a probe ``git`` argv non-interactively with a hard timeout.

    ``GIT_TERMINAL_PROMPT=0`` turns a rejected/expired credential into a fast
    non-zero exit instead of an interactive username/password prompt that would
    hang an unattended tick, and the timeout bounds a stalled network probe. The
    PAT still reaches git through the env-var credential helper (see
    :data:`_CREDENTIAL_HELPER`); only the *interactive fallback* is disabled.
    """
    return subprocess.run(
        list(args),
        capture_output=True,
        text=True,
        check=False,
        timeout=LS_REMOTE_TIMEOUT_SECONDS,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def ensure_app_repo(
    *,
    repo_url: str,
    fleet_home: Path,
    base_branch: str = DEFAULT_BASE_BRANCH,
    mutate: bool = True,
    run: GitRun = _run_git,
) -> bool:
    """Ensure ``fleet_home`` is a current checkout of ``repo_url``; return success.

    - **Absent** (no ``.git``): ``git clone`` ``repo_url`` into ``fleet_home``
      over HTTPS+PAT (always — a tick cannot run without a checkout, even a
      dry-run one).
    - **Present**: ``git fetch origin``, and when ``mutate`` is true,
      ``git reset --hard origin/<base_branch>`` so the host tracks the remote
      exactly. ``mutate=False`` (a dry-run tick) fetches only — it proves remote
      connectivity without clobbering the working tree.

    The PAT is supplied to git via the env-var credential helper (read from
    ``AZURE_DEVOPS_EXT_PAT`` in the process env); it never reaches argv or disk.
    """
    git_dir: Path = fleet_home / ".git"
    if not git_dir.is_dir():
        # clone targets a fresh dir (no existing checkout to exempt): hooks guard
        # + credential helper, no ``safe.directory``/``-C``.
        cloned = run(with_hooks_guard(_CREDENTIAL_HELPER, "clone", repo_url, str(fleet_home)))
        return cloned.returncode == 0

    fetched = run(host_git_argv(*_CREDENTIAL_HELPER, "fetch", "origin", work_dir=fleet_home))
    if fetched.returncode != 0:
        return False
    if not mutate:
        return True
    reset = run(host_git_argv("reset", "--hard", f"origin/{base_branch}", work_dir=fleet_home))
    return reset.returncode == 0


def ensure_app_repo_from_env(
    *,
    mutate: bool = True,
    run: GitRun = _run_git,
    environ: Mapping[str, str] | None = None,
) -> bool | None:
    """Resolve the app-repo config from the environment and ensure the checkout.

    Returns ``None`` when no ``FLEET_APP_REPO_URL`` (or no ``FLEET_HOME``) is
    configured — i.e. this host opts out of the auto-clone (a non-fleet-host
    tick), which is not a failure. Otherwise returns :func:`ensure_app_repo`'s
    success flag.
    """
    env: dict[str, str] = dict(os.environ if environ is None else environ)
    repo_url: str = env.get(APP_REPO_URL_ENV, "").strip()
    fleet_home_raw: str = env.get(FLEET_HOME_ENV, "").strip()
    if not repo_url or not fleet_home_raw:
        return None
    return ensure_app_repo(
        repo_url=repo_url,
        fleet_home=Path(fleet_home_raw),
        base_branch=env.get(BASE_BRANCH_ENV, "").strip() or DEFAULT_BASE_BRANCH,
        mutate=mutate,
        run=run,
    )


def target_remote_url(
    fleet_home: Path,
    *,
    run: GitRun = _run_git,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Resolve the fetch URL of the repo squadra operates on, or ``None``.

    Prefers ``FLEET_HOME``'s ``origin`` remote — the live checkout's own remote,
    which is the exact thing host-side clone/fetch/push talk to — and falls back
    to ``FLEET_APP_REPO_URL`` (the bootstrap clone URL) when the checkout has no
    usable ``origin`` yet. ``None`` when neither resolves, in which case there is
    no remote to probe. This is provider-agnostic: it returns whatever the target
    repo's origin is, never a hardcoded host.
    """
    env: Mapping[str, str] = os.environ if environ is None else environ
    completed: subprocess.CompletedProcess[str] = run(
        host_git_argv("remote", "get-url", "origin", work_dir=fleet_home)
    )
    if completed.returncode == 0:
        url: str = completed.stdout.strip()
        if url:
            return url
    fallback: str = env.get(APP_REPO_URL_ENV, "").strip()
    return fallback or None


def remote_auth_ok(remote_url: str, *, run: GitRun = _run_git_probe) -> bool:
    """Whether the ambient PAT authenticates against ``remote_url`` over HTTPS.

    Probes the *exact* auth path every host-side git op uses: ``git ls-remote``
    with the env-var PAT credential helper and the hooks guard, so the probe
    cannot pass while a real clone/fetch/push would fail. Any failure mode — a
    rejected or expired PAT, a wrong-scope PAT, an unreachable/missing remote, a
    timeout, or no ``git`` binary — reads as not-OK. The PAT is supplied via the
    credential helper from the environment and never appears in argv.
    """
    try:
        completed: subprocess.CompletedProcess[str] = run(
            with_hooks_guard(_CREDENTIAL_HELPER, "ls-remote", remote_url)
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return completed.returncode == 0
