"""Unit tests for flotilla.board — the AzCliAdo adapter, rendering, and validation.

All az I/O is canned: a recording stub matches stdout by argv-fragment, records
every call, and reads back any ``--in-file`` payload, so the tests run with no
real az / network / ADO. They exercise the provider-neutral seam directly
(Lifecycle mapping, op:replace tag writes, comment rendering, config validation)
without importing the supervisor (mid-migration) or its conftest fixtures.
"""

from collections.abc import Sequence
import json
from pathlib import Path

import pytest

from flotilla.board import (
    AzCliAdo,
    BoardValidationError,
    render_ado_html,
)
from flotilla.domain import (
    Claimed,
    Escalated,
    Finalized,
    Lifecycle,
    Reaped,
    RolledBack,
    Tags,
    WorkItem,
)

# A divergent (non-ADO-Basic) state map that maps many native names onto one
# lifecycle bucket, to prove the adapter is provider-blind and not wired to the
# ADO-Basic defaults.
_STATES: dict[Lifecycle, tuple[str, ...]] = {
    Lifecycle.QUEUED: ("New", "Approved"),
    Lifecycle.ACTIVE: ("Active", "Committed"),
    Lifecycle.DONE: ("Closed", "Done"),
}

_TAGS: Tags = Tags("fleet:")


class _RecordingAzRunner:
    """Canned az stdout per matched argv fragment; records every call."""

    def __init__(self, responses: dict[str, str]) -> None:
        """Store the fragment→stdout map and init the call / in-file logs."""
        self.responses = responses
        self.calls: list[list[str]] = []
        self.in_files: list[str] = []

    def __call__(self, args: Sequence[str]) -> str:
        """Record the call (and any --in-file payload); return canned stdout."""
        arglist: list[str] = list(args)
        self.calls.append(arglist)
        if "--in-file" in arglist:
            path: str = arglist[arglist.index("--in-file") + 1]
            self.in_files.append(Path(path).read_text(encoding="utf-8"))
        joined: str = " ".join(args)
        for fragment, response in self.responses.items():
            if fragment in joined:
                return response
        return "{}"


def _adapter(runner: _RecordingAzRunner, *, base_branch: str = "main") -> AzCliAdo:
    return AzCliAdo(
        runner,
        project="gswa-dev",
        states=_STATES,
        base_branch=base_branch,
        tags=_TAGS,
    )


# --- op:replace tag writes ----------------------------------------------------


def test_add_tag_writes_op_replace_json_patch_on_an_already_tagged_item() -> None:
    show: str = json.dumps({"fields": {"System.Tags": "alpha; beta"}})
    runner = _RecordingAzRunner({"work-item show": show})
    _adapter(runner).add_tag(70, "fleet:claimed")

    # The write goes through `az devops invoke` PATCH wit/workitems by id.
    write: list[str] = runner.calls[-1]
    assert write[:2] == ["devops", "invoke"]
    assert "workitems" in write
    assert write[write.index("--http-method") + 1] == "PATCH"
    assert write[write.index("--route-parameters") + 1] == "id=70"
    assert "wit" in write

    # The body is the op:replace JSON-Patch carrying the full new tag string.
    body: object = json.loads(runner.in_files[-1])
    assert body == [
        {"op": "replace", "path": "/fields/System.Tags", "value": "alpha; beta; fleet:claimed"}
    ]


def test_add_tag_is_idempotent_when_the_tag_is_already_present() -> None:
    show: str = json.dumps({"fields": {"System.Tags": "fleet:claimed; beta"}})
    runner = _RecordingAzRunner({"work-item show": show})
    _adapter(runner).add_tag(70, "fleet:claimed")
    # Only the read happened; no write (no devops invoke, no in-file payload).
    assert all("invoke" not in call for call in runner.calls)
    assert runner.in_files == []


def test_remove_tag_drops_the_tag_via_op_replace() -> None:
    show: str = json.dumps({"fields": {"System.Tags": "alpha; fleet:claimed; beta"}})
    runner = _RecordingAzRunner({"work-item show": show})
    _adapter(runner).remove_tag(70, "fleet:claimed")

    write: list[str] = runner.calls[-1]
    assert write[:2] == ["devops", "invoke"]
    assert write[write.index("--http-method") + 1] == "PATCH"
    body: object = json.loads(runner.in_files[-1])
    assert body == [{"op": "replace", "path": "/fields/System.Tags", "value": "alpha; beta"}]


def test_remove_tag_is_a_noop_when_the_tag_is_absent() -> None:
    show: str = json.dumps({"fields": {"System.Tags": "alpha; beta"}})
    runner = _RecordingAzRunner({"work-item show": show})
    _adapter(runner).remove_tag(70, "fleet:claimed")
    assert all("invoke" not in call for call in runner.calls)
    assert runner.in_files == []


# --- Lifecycle mapping --------------------------------------------------------


def test_items_in_state_builds_an_in_clause_over_many_native_names() -> None:
    wiql_resp: str = json.dumps({"workItems": [{"id": 11}, {"id": 12}]})
    batch_resp: str = json.dumps(
        {
            "value": [
                {
                    "id": 11,
                    "fields": {"System.Title": "feat: a", "System.Tags": "fleet:claimed; x"},
                },
                {"id": 12, "fields": {"System.Title": "feat: b"}},
            ]
        }
    )
    runner = _RecordingAzRunner({"resource wiql": wiql_resp, "workitemsbatch": batch_resp})
    items: tuple[WorkItem, ...] = _adapter(runner).items_in_state(Lifecycle.QUEUED)

    assert items == (
        WorkItem(item_id=11, title="feat: a", tags=("fleet:claimed", "x")),
        WorkItem(item_id=12, title="feat: b", tags=()),
    )
    # The WIQL body carries an IN-clause over BOTH native QUEUED names.
    assert any(
        "[System.State] IN ('New', 'Approved')" in body
        and "[System.WorkItemType] = 'Issue'" in body
        for body in runner.in_files
    )


def test_items_in_state_short_circuits_when_no_ids_match() -> None:
    runner = _RecordingAzRunner({"resource wiql": json.dumps({"workItems": []})})
    assert _adapter(runner).items_in_state(Lifecycle.DONE) == ()
    assert all("workitemsbatch" not in " ".join(call) for call in runner.calls)


def test_item_state_reverse_maps_a_secondary_native_name_to_its_bucket() -> None:
    # "Committed" is the SECOND native ACTIVE name (many-native→one).
    payload: str = json.dumps({"fields": {"System.State": "Committed"}})
    runner = _RecordingAzRunner({"work-item show": payload})
    assert _adapter(runner).item_state(70) == Lifecycle.ACTIVE


def test_item_state_maps_an_unmapped_native_state_to_queued() -> None:
    payload: str = json.dumps({"fields": {"System.State": "Removed"}})
    runner = _RecordingAzRunner({"work-item show": payload})
    # An unmapped column is treated as not-done (blocking) → QUEUED.
    assert _adapter(runner).item_state(70) == Lifecycle.QUEUED


def test_set_state_writes_the_first_native_name_of_the_bucket() -> None:
    runner = _RecordingAzRunner({})
    _adapter(runner).set_state(70, Lifecycle.DONE)
    update: list[str] = runner.calls[-1]
    assert "update" in update
    # DONE's first native name is "Closed", not "Done".
    assert update[update.index("--state") + 1] == "Closed"


# --- completed PR target ------------------------------------------------------


def test_completed_pr_url_targets_the_configured_base_branch() -> None:
    pr_resp: str = json.dumps([{"url": "https://dev.azure.com/o/_apis/git/pr/42"}])
    runner = _RecordingAzRunner({"pr list": pr_resp})
    url: str | None = _adapter(runner, base_branch="release/2026").completed_pr_url(
        "feat/slice-7-x"
    )
    assert url == "https://dev.azure.com/o/_apis/git/pr/42"
    call: list[str] = runner.calls[-1]
    # The PR query targets the configured base branch, NOT a hardcoded "main".
    assert call[call.index("--target-branch") + 1] == "release/2026"
    assert "main" not in call


def test_completed_pr_url_returns_none_when_there_is_no_completed_pr() -> None:
    runner = _RecordingAzRunner({"pr list": "[]"})
    assert _adapter(runner).completed_pr_url("feat/slice-7-x") is None


# --- comment rendering (all 5 event types) ------------------------------------


def test_render_claimed() -> None:
    html: str = render_ado_html(
        Claimed(runner_id="r3", branch="feat/slice-7-x", when="2026-06-12T00:00:00Z"), _TAGS
    )
    assert "claimed by supervisor" in html
    assert "<code>r3</code>" in html
    assert "<code>feat/slice-7-x</code>" in html


def test_render_rolled_back() -> None:
    html: str = render_ado_html(RolledBack(reason="runner launch failed"), _TAGS)
    assert "runner launch failed" in html
    assert "claim rolled back" in html


def test_render_finalized() -> None:
    html: str = render_ado_html(Finalized(pr_url="https://x/pr/9", branch="feat/slice-7-x"), _TAGS)
    assert "finalized" in html
    assert '<a href="https://x/pr/9">https://x/pr/9</a>' in html
    assert "<code>feat/slice-7-x</code>" in html


def test_render_reaped() -> None:
    html: str = render_ado_html(Reaped(evidence="no heartbeat for 30m", attempt=2), _TAGS)
    assert "reaped" in html
    assert "no heartbeat for 30m" in html
    assert "attempt 2" in html


def test_render_escalated_references_the_failed_tag() -> None:
    html: str = render_ado_html(Escalated(attempt=4, cap=3), _TAGS)
    assert "retry cap exhausted" in html
    assert "cap 3" in html
    # The escalation message names the configured failed tag.
    assert f"<code>{_TAGS.failed}</code>" in html
    assert "fleet:failed" in html


def test_add_comment_renders_the_event_and_posts_via_discussion() -> None:
    runner = _RecordingAzRunner({})
    _adapter(runner).add_comment(70, RolledBack(reason="boom"))
    call: list[str] = runner.calls[-1]
    assert "--discussion" in call
    posted: str = call[call.index("--discussion") + 1]
    assert posted == render_ado_html(RolledBack(reason="boom"), _TAGS)


# --- validate_config ----------------------------------------------------------


def _states_resp(*names: str) -> str:
    return json.dumps({"value": [{"name": name} for name in names]})


def test_validate_config_passes_when_every_configured_state_is_present() -> None:
    runner = _RecordingAzRunner(
        {
            "workItemTypeStates": _states_resp(
                "New", "Approved", "Active", "Committed", "Closed", "Done", "Removed"
            )
        }
    )
    # No raise.
    _adapter(runner).validate_config()
    # The states query is scoped to the Issue work-item type and the project.
    call: list[str] = runner.calls[-1]
    assert "workItemTypeStates" in call
    assert "type=Issue" in call
    assert "project=gswa-dev" in call


def test_validate_config_raises_naming_the_missing_state() -> None:
    # The live board is missing "Committed".
    runner = _RecordingAzRunner(
        {"workItemTypeStates": _states_resp("New", "Approved", "Active", "Closed", "Done")}
    )
    with pytest.raises(BoardValidationError) as excinfo:
        _adapter(runner).validate_config()
    assert "Committed" in str(excinfo.value)
