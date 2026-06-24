"""Unit tests for squadra.board — the AzCliAdo adapter, rendering, and validation.

All I/O is canned: a recording az stub matches stdout by argv-fragment, records
every call, and reads back any ``--in-file`` payload; a coupled curl stub
records the REST tag writes and reflects each patched value into the az stub's
``work-item show`` response so the adapter's post-write read-back sees the new
board state. The tests run with no real az / curl / network / ADO. They
exercise the provider-neutral seam directly (Lifecycle mapping, REST op:replace
tag writes + read-back, comment rendering, config validation) without importing
the supervisor (mid-migration) or its conftest fixtures.
"""

import base64
from collections.abc import Sequence
import json
from pathlib import Path
from typing import cast

import pytest

from squadra.board import (
    AzCliAdo,
    BoardValidationError,
    TagWriteError,
    render_ado_html,
)
from squadra.domain import (
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


class _RecordingCurlRunner:
    """Records curl argv, auth configs, and JSON-Patch bodies; applies writes.

    Each call reads back the ``-K`` auth config and ``--data @file`` payload
    (both are adapter-owned temp files deleted right after the call) and —
    unless built with ``apply_writes=False`` (a silently-dropped write) —
    reflects the patched tag value into the az stub's ``work-item show``
    response, so the adapter's post-write read-back sees the new board state.
    ``readback`` overrides that reflected value to simulate server-side tag
    normalization. An empty value clears the field entirely, as live ADO does.
    """

    def __init__(
        self,
        az: _RecordingAzRunner,
        *,
        apply_writes: bool = True,
        readback: str | None = None,
    ) -> None:
        """Couple the curl stub to the az stub whose show response it updates."""
        self._az = az
        self._apply_writes = apply_writes
        self._readback = readback
        self.calls: list[list[str]] = []
        self.bodies: list[str] = []
        self.auth_configs: list[str] = []

    def __call__(self, args: Sequence[str]) -> str:
        """Record the call; reflect the written value into the az show response."""
        arglist: list[str] = list(args)
        self.calls.append(arglist)
        cfg: str = arglist[arglist.index("-K") + 1]
        self.auth_configs.append(Path(cfg).read_text(encoding="utf-8"))
        data: str = arglist[arglist.index("--data") + 1]
        body: str = Path(data.removeprefix("@")).read_text(encoding="utf-8")
        self.bodies.append(body)
        if not self._apply_writes:
            return "{}"
        value: str = self._readback if self._readback is not None else _patched_value(body)
        fields: dict[str, str] = {"System.Tags": value} if value else {}
        self._az.responses["work-item show"] = json.dumps({"fields": fields})
        return "{}"


def _patched_value(body: str) -> str:
    """Extract the written tag string from a recorded JSON-Patch body."""
    patch: object = json.loads(body)
    assert isinstance(patch, list)
    ops: list[object] = cast("list[object]", patch)
    assert len(ops) == 1
    op: object = ops[0]
    assert isinstance(op, dict)
    value: object = cast("dict[str, object]", op).get("value")
    assert isinstance(value, str)
    return value


def _adapter(
    runner: _RecordingAzRunner,
    *,
    http_run: _RecordingCurlRunner | None = None,
    organization: str | None = "https://dev.azure.com/acme",
    base_branch: str = "main",
) -> AzCliAdo:
    return AzCliAdo(
        runner,
        project="example-project",
        http_run=http_run if http_run is not None else _RecordingCurlRunner(runner),
        organization=organization,
        states=_STATES,
        base_branch=base_branch,
        tags=_TAGS,
    )


@pytest.fixture
def pat_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Set a known PAT so the REST tag write can build its auth header."""
    monkeypatch.setenv("AZURE_DEVOPS_EXT_PAT", "scratch-pat")
    return "scratch-pat"


# --- REST op:replace tag writes -------------------------------------------------


def test_add_tag_patches_tags_via_rest_on_an_already_tagged_item(pat_env: str) -> None:
    show: str = json.dumps({"fields": {"System.Tags": "alpha; beta"}})
    runner = _RecordingAzRunner({"work-item show": show})
    curl = _RecordingCurlRunner(runner)
    _adapter(runner, http_run=curl).add_tag(70, "fleet:claimed")

    # The write is a direct REST PATCH by id (az cannot route this call).
    [write] = curl.calls
    assert write[write.index("-X") + 1] == "PATCH"
    assert write[-1] == "https://dev.azure.com/acme/_apis/wit/workitems/70?api-version=7.1"
    assert "Content-Type: application/json-patch+json" in write

    # The body is the op:replace JSON-Patch carrying the full new tag string.
    body: object = json.loads(curl.bodies[-1])
    assert body == [
        {"op": "replace", "path": "/fields/System.Tags", "value": "alpha; beta; fleet:claimed"}
    ]


def test_tag_write_keeps_the_pat_out_of_argv(pat_env: str) -> None:
    show: str = json.dumps({"fields": {"System.Tags": "alpha"}})
    runner = _RecordingAzRunner({"work-item show": show})
    curl = _RecordingCurlRunner(runner)
    _adapter(runner, http_run=curl).add_tag(70, "fleet:claimed")

    # Auth travels in the curl config file, never on the command line.
    token: str = base64.b64encode(f":{pat_env}".encode()).decode()
    assert curl.auth_configs == [f'header = "Authorization: Basic {token}"\n']
    assert all(pat_env not in arg and token not in arg for arg in curl.calls[-1])


def test_tag_write_raises_without_a_pat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_DEVOPS_EXT_PAT", raising=False)
    show: str = json.dumps({"fields": {"System.Tags": "alpha"}})
    runner = _RecordingAzRunner({"work-item show": show})
    curl = _RecordingCurlRunner(runner)
    with pytest.raises(RuntimeError, match="AZURE_DEVOPS_EXT_PAT"):
        _adapter(runner, http_run=curl).add_tag(70, "fleet:claimed")
    assert curl.calls == []


def test_add_tag_is_idempotent_when_the_tag_is_already_present(pat_env: str) -> None:
    show: str = json.dumps({"fields": {"System.Tags": "fleet:claimed; beta"}})
    runner = _RecordingAzRunner({"work-item show": show})
    curl = _RecordingCurlRunner(runner)
    _adapter(runner, http_run=curl).add_tag(70, "fleet:claimed")
    # Only the read happened; no REST write went out.
    assert curl.calls == []


def test_remove_tag_drops_the_tag_via_op_replace(pat_env: str) -> None:
    show: str = json.dumps({"fields": {"System.Tags": "alpha; fleet:claimed; beta"}})
    runner = _RecordingAzRunner({"work-item show": show})
    curl = _RecordingCurlRunner(runner)
    _adapter(runner, http_run=curl).remove_tag(70, "fleet:claimed")

    [write] = curl.calls
    assert write[write.index("-X") + 1] == "PATCH"
    body: object = json.loads(curl.bodies[-1])
    assert body == [{"op": "replace", "path": "/fields/System.Tags", "value": "alpha; beta"}]


def test_remove_tag_is_a_noop_when_the_tag_is_absent(pat_env: str) -> None:
    show: str = json.dumps({"fields": {"System.Tags": "alpha; beta"}})
    runner = _RecordingAzRunner({"work-item show": show})
    curl = _RecordingCurlRunner(runner)
    _adapter(runner, http_run=curl).remove_tag(70, "fleet:claimed")
    assert curl.calls == []


def test_removing_the_last_tag_clears_the_field_without_a_false_alarm(pat_env: str) -> None:
    # Live ADO drops System.Tags entirely on an empty replace; the read-back
    # must treat the absent field as "no tags" and not raise.
    show: str = json.dumps({"fields": {"System.Tags": "fleet:claimed"}})
    runner = _RecordingAzRunner({"work-item show": show})
    curl = _RecordingCurlRunner(runner)
    _adapter(runner, http_run=curl).remove_tag(70, "fleet:claimed")
    body: object = json.loads(curl.bodies[-1])
    assert body == [{"op": "replace", "path": "/fields/System.Tags", "value": ""}]


def test_tag_write_read_back_divergence_raises(pat_env: str) -> None:
    # The transport "succeeds" but the board never takes the write — the
    # original #93 silent-drop failure mode must now be loud.
    show: str = json.dumps({"fields": {"System.Tags": "alpha; beta"}})
    runner = _RecordingAzRunner({"work-item show": show})
    curl = _RecordingCurlRunner(runner, apply_writes=False)
    with pytest.raises(TagWriteError, match="work item 70"):
        _adapter(runner, http_run=curl).add_tag(70, "fleet:claimed")


def test_tag_write_read_back_tolerates_server_side_normalization(pat_env: str) -> None:
    # ADO may reorder tags and normalize case; neither is a divergence.
    show: str = json.dumps({"fields": {"System.Tags": "alpha; beta"}})
    runner = _RecordingAzRunner({"work-item show": show})
    curl = _RecordingCurlRunner(runner, readback="Fleet:Claimed; BETA; Alpha")
    _adapter(runner, http_run=curl).add_tag(70, "fleet:claimed")  # must not raise


def test_tag_write_resolves_the_org_url_from_az_defaults(pat_env: str) -> None:
    show: str = json.dumps({"fields": {"System.Tags": "alpha"}})
    configure: str = "organization = https://dev.azure.com/acme/\nproject = example-project\n"
    runner = _RecordingAzRunner({"work-item show": show, "configure --list": configure})
    curl = _RecordingCurlRunner(runner)
    _adapter(runner, http_run=curl, organization=None).add_tag(70, "fleet:claimed")
    # Trailing slash stripped; resolved org used for the REST URL.
    assert curl.calls[-1][-1] == "https://dev.azure.com/acme/_apis/wit/workitems/70?api-version=7.1"


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
    assert "project=example-project" in call


def test_validate_config_raises_naming_the_missing_state() -> None:
    # The live board is missing "Committed".
    runner = _RecordingAzRunner(
        {"workItemTypeStates": _states_resp("New", "Approved", "Active", "Closed", "Done")}
    )
    with pytest.raises(BoardValidationError) as excinfo:
        _adapter(runner).validate_config()
    assert "Committed" in str(excinfo.value)
