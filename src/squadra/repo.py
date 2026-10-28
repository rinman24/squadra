"""Host-side app-repo bootstrap: keep ``FLEET_HOME`` a current checkout.

When the fleet-host was hand-provisioned, cloud-init never fired, so the
app-backend checkout the supervisor operates on (``FLEET_HOME``) had to be
cloned by hand. This module
removes that manual step: ``squadra fleet-tick`` calls :func:`ensure_app_repo`
before each tick, so the host self-heals to a fresh checkout — clone if absent,
fetch (+ reset to the remote base) if present.

Auth is whatever :mod:`squadra.remote_auth` resolves for the remote's host
(an Azure DevOps PAT from ``$AZURE_DEVOPS_EXT_PAT``, the gh CLI's github.com
credential, or the host's ssh key); no secret is ever written to ``.git/config``
or argv. :class:`TargetRemote` resolves ``FLEET_HOME``'s remote and its
credential once, for both the supervisor's preflight and the claim's worktree
fetch, so the two cannot authenticate differently. Every
host-side git argv here is built through :mod:`squadra.git_host`, so it pins
``core.hooksPath=/dev/null`` (and, on a checkout op, a narrowly-scoped
``safe.directory``) — the centralized #193 hardening that keeps a planted hook
from executing during a host-side op.
"""

from collections.abc import Callable, Mapping, Sequence
import os
from pathlib import Path
import re
import subprocess
from typing import Final
from urllib.parse import unquote

from squadra.git_host import host_git_argv
from squadra.remote_auth import (
    RemoteAuthProbe,
    RemoteCredential,
    credential_for,
    remote_git_argv,
    remote_host,
)

DEFAULT_BASE_BRANCH: Final[str] = "main"

# A remote auth probe (`git ls-remote`) must fail fast, never hang: bound it so a
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

# Azure DevOps remotes, each capturing (org, project, repo): HTTPS on dev.azure.com
# (the project segment is absent when the repo is named after its project), HTTPS on
# the legacy <org>.visualstudio.com (optionally under DefaultCollection), and SSH on
# ssh.dev.azure.com or vs-ssh.visualstudio.com (``v3/<org>/<project>/<repo>``).
_ADO_REMOTES: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        r"^https?://(?:[^@/]+@)?dev\.azure\.com/([^/]+)/(?:([^/]+)/)?_git/([^/]+?)/?$", re.I
    ),
    re.compile(
        r"^https?://(?:[^@/]+@)?([^./]+)\.visualstudio\.com/(?:defaultcollection/)?"
        r"(?:([^/]+)/)?_git/([^/]+?)/?$",
        re.I,
    ),
    re.compile(
        r"^(?:ssh://)?[^@/]+@(?:ssh\.dev\.azure\.com|vs-ssh\.visualstudio\.com)[:/]v3/"
        r"([^/]+)/([^/]+)/([^/]+?)/?$",
        re.I,
    ),
)
# Any other remote whose path is ``<owner>/<name>(.git)``: GitHub's three forms,
# HTTPS, ``ssh://`` and scp-like (the same shapes ``github_repository_of`` reads).
_OWNER_NAME_REMOTE: Final[re.Pattern[str]] = re.compile(
    r"^(?:[a-z+]+://(?:[^@/]+@)?[^/]+/|[^@:/]+@[^:/]+:)([^/]+)/([^/]+?)(?:\.git)?/?$"
)


def _run_git(args: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    """Run a full ``git`` argv capturing output; never raises on a non-zero exit."""
    return subprocess.run(list(args), capture_output=True, text=True, check=False)


def _run_git_probe(args: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    """Run a probe ``git`` argv non-interactively with a hard timeout.

    ``GIT_TERMINAL_PROMPT=0`` turns a rejected/expired credential into a fast
    non-zero exit instead of an interactive username/password prompt that would
    hang an unattended tick, and the timeout bounds a stalled network probe. The
    credential still reaches git through the remote's credential helper (see
    :mod:`squadra.remote_auth`); only the *interactive fallback* is disabled.
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
      with ``repo_url``'s credential (always — a tick cannot run without a
      checkout, even a dry-run one).
    - **Present**: ``git fetch origin``, and when ``mutate`` is true,
      ``git reset --hard origin/<base_branch>`` so the host tracks the remote
      exactly. ``mutate=False`` (a dry-run tick) fetches only — it proves remote
      connectivity without clobbering the working tree.

    The fetch authenticates as ``origin``'s host requires (``repo_url`` when the
    checkout has no ``origin``). A remote with no credential
    (:func:`squadra.remote_auth.credential_for` is ``None``) fails closed: no
    git remote op runs and the result is ``False``.
    """
    git_dir: Path = fleet_home / ".git"
    if not git_dir.is_dir():
        clone_credential: RemoteCredential | None = credential_for(repo_url)
        if clone_credential is None:
            return False
        # clone targets a fresh dir (no existing checkout to exempt): hooks guard
        # + credential helper, no ``safe.directory``/``-C``.
        cloned = run(remote_git_argv(clone_credential, "clone", repo_url, str(fleet_home)))
        return cloned.returncode == 0

    credential: RemoteCredential | None = TargetRemote(
        fleet_home, run=run, environ={APP_REPO_URL_ENV: repo_url}
    ).credential()
    if credential is None:
        return False
    fetched = run(remote_git_argv(credential, "fetch", "origin", work_dir=fleet_home))
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


def repository_identity(url: str) -> str | None:
    """Return the repository a remote URL names, as a provider-neutral key, or ``None``.

    Two remotes name the same repository exactly when their keys are equal, so
    the key drops what varies between remotes of one repository: the transport,
    any userinfo (an HTTPS remote can carry a token), ``.git``, percent-encoding
    and case (GitHub and Azure DevOps names are case-insensitive).

    - Azure DevOps: ``<org>/<project>/<repo>``, from any of its remote forms.
    - Otherwise ``<owner>/<name>`` for a two-segment path: GitHub's forms. The
      host is not compared, as ``github_repository_of`` does not compare it.

    The two shapes have different segment counts, so they never collide. Any
    other URL (a local path, a nested group) is ``None``: the caller fails
    closed rather than guess.
    """
    raw: str = url.strip()
    for pattern in _ADO_REMOTES:
        ado: re.Match[str] | None = pattern.match(raw)
        if ado is not None:
            org, project, name = ado.group(1), ado.group(2), ado.group(3)
            return unquote(f"{org}/{project or name}/{name}").lower()
    owned: re.Match[str] | None = _OWNER_NAME_REMOTE.match(raw)
    if owned is None:
        return None
    return unquote(f"{owned.group(1)}/{owned.group(2)}").lower()


def fleet_repository(fleet_home: Path, app_repo_url: str, *, run: GitRun = _run_git) -> str | None:
    """Return the repository a fleet serves (:func:`repository_identity`), or ``None``.

    Resolved as :func:`target_remote_url` resolves it — ``fleet_home``'s
    ``origin``, else ``app_repo_url`` — except that the fallback is the fleet's
    own clone URL (from its unit or its ``install-units`` flag), never this
    process's ``FLEET_APP_REPO_URL``.
    """
    url: str | None = target_remote_url(
        fleet_home, run=run, environ={APP_REPO_URL_ENV: app_repo_url}
    )
    return repository_identity(url) if url is not None else None


def _ls_remote_ok(remote_url: str, credential: RemoteCredential, run: GitRun) -> bool:
    """Whether ``git ls-remote`` with ``credential`` succeeds against ``remote_url``."""
    try:
        completed: subprocess.CompletedProcess[str] = run(
            remote_git_argv(credential, "ls-remote", remote_url)
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return completed.returncode == 0


def remote_auth_ok(remote_url: str, *, run: GitRun = _run_git_probe) -> bool:
    """Whether host-side git authenticates against ``remote_url`` with its credential.

    Runs ``git ls-remote`` with :func:`squadra.remote_auth.credential_for`'s
    credential and the hooks guard. A remote with no credential is ``False``
    without running git; so is any git failure — a rejected or expired
    credential, an unreachable remote, a timeout, or no ``git`` binary.
    """
    credential: RemoteCredential | None = credential_for(remote_url)
    if credential is None:
        return False
    return _ls_remote_ok(remote_url, credential, run)


class TargetRemote:
    """The remote ``FLEET_HOME`` talks to, and the credential host-side git uses for it.

    One instance backs both the supervisor's preflight (:meth:`probe`) and the
    claim's worktree fetch (:meth:`credential`), each resolving the remote with
    :func:`target_remote_url` and its credential with
    :func:`squadra.remote_auth.credential_for`. So the probe runs the claim's
    exact auth path and cannot pass while the claim's fetch would authenticate
    some other way. Resolution happens on each call, never cached: a tick sees
    the remote as it is when it claims.
    """

    def __init__(
        self,
        fleet_home: Path,
        *,
        run: GitRun | None = None,
        probe_run: GitRun | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        """Bind to ``fleet_home``, its git runners (the test seams) and an env."""
        self._fleet_home = fleet_home
        self._run: GitRun = run if run is not None else _run_git
        self._probe_run: GitRun = probe_run if probe_run is not None else _run_git_probe
        self._environ = environ

    def _url(self) -> str | None:
        """Resolve the target remote's URL (``origin``, else ``FLEET_APP_REPO_URL``)."""
        return target_remote_url(self._fleet_home, run=self._run, environ=self._environ)

    def credential(self) -> RemoteCredential | None:
        """Resolve the target remote's credential, or ``None`` (no remote, or no credential)."""
        url: str | None = self._url()
        return credential_for(url) if url is not None else None

    def probe(self) -> RemoteAuthProbe:
        """Run the preflight: ``git ls-remote`` against the target remote with its credential.

        Fails closed: no remote, or a host with no credential, is not-OK without
        any network op. The result names the credential tried, for the log.
        """
        url: str | None = self._url()
        if url is None:
            return RemoteAuthProbe(ok=False)
        parsed: tuple[str, str] | None = remote_host(url)
        host: str | None = parsed[1] if parsed is not None else None
        credential: RemoteCredential | None = credential_for(url)
        if credential is None:
            return RemoteAuthProbe(ok=False, host=host)
        ok: bool = _ls_remote_ok(url, credential, self._probe_run)
        return RemoteAuthProbe(ok=ok, host=host, credential=credential)
