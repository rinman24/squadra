"""Host-side app-repo bootstrap: keep ``FLEET_HOME`` a current checkout.

When INFRA #148 hand-provisioned the fleet-host, cloud-init never fired, so the
gswa-backend checkout the supervisor operates on (``FLEET_HOME``) had to be
cloned by hand (memory ``fleet-host-needs-manual-gswa-clone``). This module
removes that manual step: ``flotilla fleet-tick`` calls :func:`ensure_app_repo`
before each tick, so the host self-heals to a fresh checkout — clone if absent,
fetch (+ reset to the remote base) if present.

Auth is HTTPS + PAT via an *env-var* credential helper: the PAT is read from the
process environment (``AZURE_DEVOPS_EXT_PAT``, applied by the fleet-tick secret
bootstrap) at git time and is **never** written to ``.git/config`` or any file —
the helper string carries only the variable *reference*, not its value (matching
the repo-local helper pattern in memory ``git-push-https-pat-not-ssh``). Every
host-side git invocation also pins ``core.hooksPath=/dev/null`` so a hook planted
in the checkout cannot execute during a host-side op (defense aligned with the
flotilla git-hooks hardening, Issue #193; not its full centralization).
"""

from collections.abc import Callable, Mapping, Sequence
import os
from pathlib import Path
import subprocess
from typing import Final

DEFAULT_BASE_BRANCH: Final[str] = "main"

APP_REPO_URL_ENV: Final[str] = "FLEET_APP_REPO_URL"
FLEET_HOME_ENV: Final[str] = "FLEET_HOME"
BASE_BRANCH_ENV: Final[str] = "FLEET_BASE_BRANCH"

# Git command-runner seam (argv -> completed process), injected for tests.
GitRun = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]

# Defeats a checkout-planted hook / agent-set core.hooksPath on host-side ops
# (command-line ``-c`` overrides config). Forward-aligned with Issue #193.
_HOOKS_GUARD: Final[tuple[str, ...]] = ("-c", "core.hooksPath=/dev/null")

# An env-var credential helper: reset any inherited helper, then one that reads
# the PAT from the environment. No secret is persisted — only the var reference.
_CREDENTIAL_HELPER: Final[tuple[str, ...]] = (
    "-c",
    "credential.helper=",
    "-c",
    'credential.helper=!f() { echo username=pat; echo "password=$AZURE_DEVOPS_EXT_PAT"; }; f',
)


def _run_git(args: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    """Run a ``git`` command capturing output; never raises on a non-zero exit."""
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False)


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
        cloned = run(
            [
                *_CREDENTIAL_HELPER,
                *_HOOKS_GUARD,
                "clone",
                repo_url,
                str(fleet_home),
            ]
        )
        return cloned.returncode == 0

    fetched = run([*_CREDENTIAL_HELPER, *_HOOKS_GUARD, "-C", str(fleet_home), "fetch", "origin"])
    if fetched.returncode != 0:
        return False
    if not mutate:
        return True
    reset = run([*_HOOKS_GUARD, "-C", str(fleet_home), "reset", "--hard", f"origin/{base_branch}"])
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
