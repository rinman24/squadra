"""Host-side git argv construction — the one place every host-side git op is hardened.

Threat model (Issue #193, parent #139). The fleet runs each slice's agent in a
sandbox with the slice worktree bind-mounted as ``/work``. A misbehaving or
prompt-injected agent can plant a git hook (``pre-push``, ``post-checkout``, …)
or set ``core.hooksPath`` to an agent-controlled directory **inside the
worktree**. Later, a **host-side** git op runs against that same worktree — the
supervisor's worktree create / archive / cleanup, the ``base..HEAD`` commit
count, the app-repo bootstrap, and (when wired) the branch push / PR create.
Those host-side ops run in the *supervisor* context, which holds the ADO PAT and
the VM managed identity (Key Vault access). A hook that fires there is a sandbox
escape into the credential-holding host.

Two coupled hardenings, applied together because either alone reopens the hole:

- **Hook neutralization (always).** Every host-side git invocation pins
  ``-c core.hooksPath=/dev/null``. A command-line ``-c`` overrides repository,
  global, and system config, so it defeats both a planted hook *file* (the
  configured hooks dir is now ``/dev/null``, which holds no hooks) and an
  agent-set ``core.hooksPath`` (the command-line value wins). It is set on the
  argv, never written to ``.git/config``, so it cannot be unset by the agent.

- **Dubious-ownership, narrowly (when operating on a checkout).** A host-side
  git op that runs as a different OS user than the ``FLEET_HOME`` / worktree
  owner (seen live on the fleet-host: a root systemd/goss ``git fetch`` against
  the ``azureuser``-owned checkout) trips git's "dubious ownership" guard and
  aborts. We clear it with ``-c safe.directory=<that exact path>`` — **never**
  ``safe.directory=*``. A global wildcard would mark *every* repo on the host
  safe, including an agent-controlled one, which — paired with any future config
  trust — is exactly the kind of broad trust this issue exists to avoid. The
  scope is the single path being operated on, nothing more.

Both are command-line ``-c`` overrides (highest-precedence, transient, never
persisted), so the hardening travels with the argv and an agent cannot strip it
from on-disk config.

The host-side **push** of a slice's commits is the credential-holding op at the
centre of this threat model, but it is not yet wired in code (it is the deferred
write-tail the supervisor's ``_handoff`` documents). When it is wired it MUST be
built as ``host_git_argv(*credential_helper, "push", "origin", branch,
work_dir=worktree)`` — the same builder fetch / worktree-create / branch-delete
already use — so a ``pre-push`` hook planted in the bind-mounted worktree cannot
fire in the host context. ``tests/test_git_host.py`` proves that contract
(planted ``pre-push`` hook neutralized, plus a bare-push control), so a future
push that bypasses this builder is a reviewable regression, not a silent escape.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Final

# core.hooksPath=/dev/null defeats a checkout-planted hook AND an agent-set
# core.hooksPath: the configured hooks dir becomes a path that holds no hooks,
# and the command-line -c outranks any repo/global/system config the agent set.
HOOKS_GUARD: Final[tuple[str, ...]] = ("-c", "core.hooksPath=/dev/null")


def _safe_directory_guard(work_dir: str | Path) -> tuple[str, ...]:
    """Build the narrowly-scoped ``safe.directory`` override for one checkout path.

    Scopes the dubious-ownership exemption to ``work_dir`` exactly — never the
    ``safe.directory=*`` wildcard, which would trust every repo on the host.
    """
    return ("-c", f"safe.directory={work_dir}")


def host_git_argv(
    *args: str,
    work_dir: str | Path | None = None,
) -> list[str]:
    """Build a hardened host-side ``git`` argv.

    Every returned argv begins ``git -c core.hooksPath=/dev/null …`` so a
    checkout-planted hook / agent-set ``core.hooksPath`` cannot fire host-side.

    When ``work_dir`` is given (any op that runs *against a checkout* — anything
    that would otherwise pass ``-C <path>``), the argv additionally carries a
    ``safe.directory=<work_dir>`` exemption scoped to that exact path and a
    ``-C <work_dir>`` so the op runs there. ``work_dir`` is omitted only for ops
    with no local working tree (``clone`` into a fresh dir, ``ls-remote`` against
    a URL), where there is no planted-hook surface to exempt.

    ``args`` is the git sub-command and its arguments (e.g. ``"fetch",
    "origin"``); credential-helper / other ``-c`` overrides a caller needs may be
    interleaved in ``args`` and are preserved in order after the guards.
    """
    guards: list[str] = list(HOOKS_GUARD)
    location: list[str] = []
    if work_dir is not None:
        guards.extend(_safe_directory_guard(work_dir))
        location = ["-C", str(work_dir)]
    return ["git", *guards, *location, *args]


def with_hooks_guard(extra_config: Sequence[str], *args: str) -> list[str]:
    """Build a hardened ``git`` argv with no ``-C`` but with extra ``-c`` overrides.

    For host-side ops that talk to a remote URL rather than a local checkout
    (``clone``, ``ls-remote``): the hooks guard is pinned, the caller's
    ``extra_config`` (e.g. a credential helper) is interleaved after it, and no
    ``safe.directory`` / ``-C`` is added (there is no on-disk checkout to exempt).
    """
    return ["git", *HOOKS_GUARD, *extra_config, *args]
