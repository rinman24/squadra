"""Unit tests for the host-side manifest/context I/O (F4, Task #167).

The supervisor injects a read-only ``slice.json`` (the **slice context**: Issue +
Tasks + predecessor states it read host-side) into the bind-mounted ``/work``
before launch, and reads + validates the agent's ``outcome.json`` (the **outcome
manifest**) after exit. The ``(container_exit, manifest_valid, commits)`` triple
the :class:`~squadra.engines.LifecycleEngine` keys on consumes
:func:`~squadra.manifest.read_manifest`'s ``present`` / ``valid`` /
``needs_decision`` projection; the contract here is what G2 produces.
"""

import json
from pathlib import Path

import pytest

from squadra.domain import SliceContext, SliceTask
from squadra.manifest import (
    MANIFEST_FILENAME,
    SLICE_CONTEXT_FILENAME,
    ManifestRead,
    read_manifest,
    write_slice_context,
)


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    """An isolated bind-mount worktree root with its ``.squadra/`` directory."""
    root: Path = tmp_path / "work"
    (root / ".squadra").mkdir(parents=True)
    return root


def _write_manifest(worktree: Path, payload: object) -> None:
    """Write a raw ``outcome.json`` payload (valid JSON) into ``.squadra/``."""
    (worktree / ".squadra" / MANIFEST_FILENAME).write_text(json.dumps(payload), encoding="utf-8")


# --- slice context (host -> agent) --------------------------------------------


def test_write_slice_context_lands_readable_json(worktree: Path) -> None:
    context = SliceContext(
        issue_id=143,
        title="feat: orchestration cutover",
        tasks=(SliceTask(task_id=163, title="rewrite run_tick", state="Doing"),),
        predecessor_states={140: "Done", 141: "Done"},
    )
    path: Path = write_slice_context(worktree, context)
    assert path == worktree / ".squadra" / SLICE_CONTEXT_FILENAME
    data: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    assert data["issue_id"] == 143
    assert data["title"] == "feat: orchestration cutover"
    assert data["tasks"] == [{"task_id": 163, "title": "rewrite run_tick", "state": "Doing"}]
    assert data["predecessor_states"] == {"140": "Done", "141": "Done"}


def test_write_slice_context_creates_the_squadra_dir(tmp_path: Path) -> None:
    worktree: Path = tmp_path / "fresh"
    worktree.mkdir()
    context = SliceContext(issue_id=1, title="t", tasks=(), predecessor_states={})
    path: Path = write_slice_context(worktree, context)
    assert path.is_file()


# --- outcome manifest (agent -> host) -----------------------------------------


def test_read_manifest_absent_is_not_present(worktree: Path) -> None:
    read: ManifestRead = read_manifest(worktree)
    assert read == ManifestRead(present=False, valid=False, needs_decision=False, manifest=None)


def test_read_manifest_valid_handoff(worktree: Path) -> None:
    _write_manifest(
        worktree,
        {
            "parked_state": "awaiting-pr-approval",
            "pr_title": "feat: cutover",
            "pr_body": "## Summary\nwires the engine",
        },
    )
    read: ManifestRead = read_manifest(worktree)
    assert read.present is True
    assert read.valid is True
    assert read.needs_decision is False
    assert read.manifest is not None
    assert read.manifest.parked_state == "awaiting-pr-approval"
    assert read.manifest.pr_title == "feat: cutover"
    assert read.manifest.pr_body == "## Summary\nwires the engine"


def test_read_manifest_needs_decision_flag(worktree: Path) -> None:
    _write_manifest(worktree, {"parked_state": "needs-decision"})
    read: ManifestRead = read_manifest(worktree)
    assert read.present is True
    assert read.valid is True
    assert read.needs_decision is True
    assert read.manifest is not None
    assert read.manifest.pr_title is None


def test_read_manifest_malformed_json_is_present_but_invalid(worktree: Path) -> None:
    (worktree / ".squadra" / MANIFEST_FILENAME).write_text("{not json", encoding="utf-8")
    read: ManifestRead = read_manifest(worktree)
    assert read.present is True
    assert read.valid is False
    assert read.needs_decision is False
    assert read.manifest is None


def test_read_manifest_missing_parked_state_is_invalid(worktree: Path) -> None:
    _write_manifest(worktree, {"pr_title": "no parked_state"})
    read: ManifestRead = read_manifest(worktree)
    assert read.present is True
    assert read.valid is False
    assert read.manifest is None


def test_read_manifest_unknown_parked_state_is_invalid(worktree: Path) -> None:
    _write_manifest(worktree, {"parked_state": "banana"})
    read: ManifestRead = read_manifest(worktree)
    assert read.present is True
    assert read.valid is False


def test_read_manifest_non_object_payload_is_invalid(worktree: Path) -> None:
    _write_manifest(worktree, ["not", "an", "object"])
    read: ManifestRead = read_manifest(worktree)
    assert read.present is True
    assert read.valid is False
