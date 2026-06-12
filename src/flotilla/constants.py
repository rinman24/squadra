"""Fleet constants — the ADR-0007 addendum (2026-06-10) values, env-tunable.

Defaults are the addendum's resolved decisions. Every numeric value can be
overridden from the environment without code edits; values are read once at
import, which is enough because each supervisor tick and each runner wrapper
is a fresh process (crash-only design), so an env change applies on the next
fire.
"""

import os
from pathlib import Path


def _int_from_env(name: str, default: int) -> int:
    """Read an integer override from the environment, falling back to ``default``."""
    raw: str | None = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _str_from_env(name: str, default: str) -> str:
    """Read a string override from the environment, falling back to ``default``."""
    raw: str | None = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip()


def _bool_from_env(name: str, default: bool) -> bool:
    """Read a boolean override from the environment, falling back to ``default``."""
    raw: str | None = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in ("0", "false", "no")


def _fleet_home() -> Path:
    """Return the repo flotilla operates on: ``FLEET_HOME`` or the working directory.

    flotilla is repo-agnostic — it drives whichever checkout ``FLEET_HOME``
    points at. With no override it falls back to the current working directory
    (the repo you launched it from); there is no hardcoded default path.
    """
    raw: str | None = os.environ.get("FLEET_HOME")
    if raw is not None and raw.strip():
        return Path(raw)
    return Path.cwd()


# Per-slice runtime artifacts (status.json, sidecars, archived attempts) live
# under FLEET_ROOT/<issue-id>/. The default sits under the target repo's
# .claude/fleet (FLEET_HOME, else the cwd); on a bind-mounted checkout that
# survives container restart, and it is git-excluded there.
FLEET_ROOT: Path = Path(os.environ.get("FLEET_ROOT") or (_fleet_home() / ".claude" / "fleet"))

# Concurrency cap: 4 cores / no swap — two runners already saturate a small
# devbox during lint/test, and all runners share one account rate limit
# (addendum §1). This is the CLAIM BUDGET only — 0 stops new claims but does
# NOT make a tick safe: finalize and reap still mutate. For a tick that
# cannot mutate, use FLEET_DRY_RUN / `flotilla tick --dry-run`.
FLEET_MAX_RUNNERS: int = _int_from_env("FLEET_MAX_RUNNERS", 2)

# Dry run: the tick runs every pass's read+plan logic and reports the
# would-be finalize/reap/claim actions, but every side effect — ADO writes,
# runner launches, claude spawns (cleanup + auth probe), git, worktree moves,
# local status/marker writes — is suppressed at the TickSeams boundary.
# Equivalent to the `--dry-run` flag on `flotilla tick` / flotilla-supervisor.
FLEET_DRY_RUN: bool = _bool_from_env("FLEET_DRY_RUN", False)

# Liveness: the runner wrapper stamps last_heartbeat every 60s; the watchdog
# treats a heartbeat older than 10 minutes (10× the interval, tolerant of long
# test/lint runs) as stale (addendum §3).
HEARTBEAT_INTERVAL_SECONDS: int = _int_from_env("FLEET_HEARTBEAT_INTERVAL_SECONDS", 60)
STALENESS_THRESHOLD_SECONDS: int = _int_from_env("FLEET_STALENESS_THRESHOLD_SECONDS", 600)

# Transient failures auto-retry up to 3 total attempts, each from a fresh
# worktree; exhaustion escalates to fleet:failed (addendum §4).
MAX_ATTEMPTS: int = _int_from_env("FLEET_MAX_ATTEMPTS", 3)

# Compute tier for every model-backed claude call the fleet makes — the slice
# runner (/afk-slice-runner), the cleanup pass (/cleanup-merged-branches), and
# the auth probe. Pinned here as the single source of truth so the fleet's tier
# is a deliberate choice, NOT whatever default an interactive session's
# settings.json/settings.local.json "model" pin happens to be (which a headless
# `claude -p` would otherwise inherit ambiently). Opus 4.8 at high reasoning
# effort for unattended TDD; both are env-overridable. Effort accepts the CLI
# levels low|medium|high|xhigh|max (model-dependent).
FLEET_MODEL: str = _str_from_env("FLEET_MODEL", "claude-opus-4-8")
FLEET_EFFORT: str = _str_from_env("FLEET_EFFORT", "high")

# Board tag vocabulary. Tags carry a configurable namespace PREFIX (default
# below; override via flotilla.toml ``[board].tag_prefix`` / ``FLEET_TAG_PREFIX``).
# The five SUFFIXES are fixed canonical vocabulary across every provider, and
# detection stays prefix-based (``startswith``). System.State arbitrates claims;
# the ``claimed`` suffix distinguishes a fleet-claimed item from a human's manual
# move to the active column. Parked sub-states are tags because ADO's Basic
# process has only three states (addendum preamble, §2). The fully-qualified
# tags are assembled by :class:`flotilla.domain.Tags`.
DEFAULT_TAG_PREFIX: str = "fleet:"

TAG_SUFFIX_CLAIMED: str = "claimed"
TAG_SUFFIX_FAILED: str = "failed"
TAG_SUFFIX_NEEDS_DECISION: str = "needs-decision"
TAG_SUFFIX_QA_READY: str = "qa-ready"
TAG_SUFFIX_AWAITING_PR_APPROVAL: str = "awaiting-pr-approval"

# Suffixes whose presence marks a *deliberate* park (the slice is never reaped).
# ``failed`` is included: a tagged ``<prefix>failed`` item is already escalated
# and terminal (never auto-retried). An untagged ``parked_state="failed"``
# status, by contrast, is positive failure evidence and reap-eligible.
PARKED_TAG_SUFFIXES: tuple[str, ...] = (
    TAG_SUFFIX_NEEDS_DECISION,
    TAG_SUFFIX_QA_READY,
    TAG_SUFFIX_AWAITING_PR_APPROVAL,
    TAG_SUFFIX_FAILED,
)

# Overlapping supervisor ticks serialize on this lock (addendum §2).
SUPERVISOR_LOCK_FILENAME: str = "supervisor.lock"

STATUS_FILENAME: str = "status.json"
