"""Fleet constants — env-tunable.

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
# (addendum §1).
FLEET_MAX_RUNNERS: int = _int_from_env("FLEET_MAX_RUNNERS", 2)

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

# ADO tag vocabulary. System.State arbitrates claims; fleet:claimed
# distinguishes a fleet-claimed Issue from a human's manual move to Doing.
# Parked sub-states are tags because the Basic process has only three states
# (addendum preamble, §2).
TAG_CLAIMED: str = "fleet:claimed"
TAG_FAILED: str = "fleet:failed"
TAG_NEEDS_DECISION: str = "fleet:needs-decision"
TAG_QA_READY: str = "fleet:qa-ready"
TAG_AWAITING_PR_APPROVAL: str = "fleet:awaiting-pr-approval"

# Overlapping supervisor ticks serialize on this lock (addendum §2).
SUPERVISOR_LOCK_FILENAME: str = "supervisor.lock"

STATUS_FILENAME: str = "status.json"
