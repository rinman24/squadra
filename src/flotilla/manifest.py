"""Host-side manifest/context I/O for the commit-only handoff (ADR-0002 §§1–2).

Two bind-mounted JSON files under the worktree's ``.flotilla/`` carry the
host↔agent exchange the credential-free agent cannot perform over the board:

- **slice context** (``slice.json``) — written host-side *before* launch
  (:func:`write_slice_context`): the slice's Issue + Tasks + predecessor states
  the supervisor read for the agent, since the contained agent has no board.
- **outcome manifest** (``outcome.json``) — written by the agent as its final
  act, read + validated host-side *after* exit (:func:`read_manifest`). It is the
  *intent* half of the ``(container_exit, manifest_valid, commits)`` completion
  triple the :class:`~flotilla.engines.LifecycleEngine` keys on.

This module owns only the file I/O + schema validation; the orchestration that
turns a :class:`ManifestRead` into engine facts (F4 ``run_tick``) lives in
:mod:`flotilla.supervisor`. Reads never raise — a missing, malformed, or
schema-invalid manifest is *information* (a crash edge), not an exception, so the
result distinguishes ``present`` from ``valid`` for the triple.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Final, cast

from flotilla.domain import OutcomeManifest, SliceContext
from flotilla.status import PARKED_STATES

MANIFEST_FILENAME: Final[str] = "outcome.json"
SLICE_CONTEXT_FILENAME: Final[str] = "slice.json"
_FLOTILLA_DIR: Final[str] = ".flotilla"

# The parked states a valid manifest may declare — the same canonical vocabulary
# the status convention uses (``status.PARKED_STATES``); ``needs-decision`` is the
# one that routes to the no-PR decision park.
MANIFEST_PARKED_STATES: Final[tuple[str, ...]] = PARKED_STATES
_NEEDS_DECISION: Final[str] = "needs-decision"


@dataclass(frozen=True, slots=True)
class ManifestRead:
    """The host's projection of an ``outcome.json`` read — the triple's intent half.

    ``present`` — the file exists at all; ``valid`` — it parses and satisfies the
    schema; ``needs_decision`` — a valid manifest whose ``parked_state`` is
    ``needs-decision``. ``manifest`` is the parsed :class:`OutcomeManifest` when
    ``valid`` (else ``None``). A present-but-invalid read (malformed JSON, missing
    or unknown ``parked_state``) is a crash edge, never an exception.
    """

    present: bool
    valid: bool
    needs_decision: bool
    manifest: OutcomeManifest | None


def manifest_path(worktree: Path) -> Path:
    """Return the slice worktree's ``.flotilla/outcome.json`` path."""
    return worktree / _FLOTILLA_DIR / MANIFEST_FILENAME


def slice_context_path(worktree: Path) -> Path:
    """Return the slice worktree's ``.flotilla/slice.json`` path."""
    return worktree / _FLOTILLA_DIR / SLICE_CONTEXT_FILENAME


def write_slice_context(worktree: Path, context: SliceContext) -> Path:
    """Write the read-only host→agent ``slice.json`` into the worktree's ``.flotilla/``.

    Creates the ``.flotilla/`` directory if needed and returns the written path.
    Predecessor-state map keys are serialized as strings (JSON object keys).
    """
    path: Path = slice_context_path(worktree)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "issue_id": context.issue_id,
        "title": context.title,
        "tasks": [
            {"task_id": task.task_id, "title": task.title, "state": task.state}
            for task in context.tasks
        ],
        "predecessor_states": {
            str(item_id): state for item_id, state in context.predecessor_states.items()
        },
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def read_manifest(worktree: Path) -> ManifestRead:
    """Read + validate the agent's ``outcome.json``; never raises.

    Distinguishes the triple's intent states: absent (``present=False``),
    present-but-invalid (malformed JSON, a non-object payload, a missing or
    unknown ``parked_state``), and valid (a known ``parked_state``, with
    ``needs_decision`` set when it is ``needs-decision``).
    """
    path: Path = manifest_path(worktree)
    if not path.is_file():
        return ManifestRead(present=False, valid=False, needs_decision=False, manifest=None)
    raw: str = path.read_text(encoding="utf-8")
    try:
        data: object = json.loads(raw)
    except json.JSONDecodeError:
        return ManifestRead(present=True, valid=False, needs_decision=False, manifest=None)
    if not isinstance(data, dict):
        return ManifestRead(present=True, valid=False, needs_decision=False, manifest=None)
    return _validate(cast("Mapping[str, object]", data))


def _validate(data: Mapping[str, object]) -> ManifestRead:
    """Project a parsed JSON object onto a present, schema-checked manifest read."""
    parked_state: object = data.get("parked_state")
    if not isinstance(parked_state, str) or parked_state not in MANIFEST_PARKED_STATES:
        return ManifestRead(present=True, valid=False, needs_decision=False, manifest=None)
    manifest = OutcomeManifest(
        parked_state=parked_state,
        pr_title=_optional_str(data, "pr_title"),
        pr_body=_optional_str(data, "pr_body"),
        qa_path=_optional_str(data, "qa_path"),
    )
    return ManifestRead(
        present=True,
        valid=True,
        needs_decision=parked_state == _NEEDS_DECISION,
        manifest=manifest,
    )


def _optional_str(data: Mapping[str, object], key: str) -> str | None:
    """Return a string field, or ``None`` when absent/null/not-a-string."""
    value: object = data.get(key)
    return value if isinstance(value, str) else None
