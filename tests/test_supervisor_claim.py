"""Engine-driven tick: the claim/launch path + the cross-slice claim budget (F4).

Claim parity with the legacy claim pass (id-order, cap, predecessor/parent-scope
gating, the state→tag→comment protocol, rollback) plus the new commit-only setup:
the supervisor creates the slice worktree off fresh ``origin/main`` and injects the
read-only ``slice.json`` context *before* launching the sandbox (ADR-0002 §§1–2).
The claim budget stays orchestrator-side over the engine's ``SignalClaimable`` set.
"""

from collections.abc import Callable
from datetime import UTC, datetime
import fcntl
from pathlib import Path

from squadra.config import SquadraConfig
from squadra.constants import SUPERVISOR_LOCK_FILENAME
from squadra.domain import Claimed, Lifecycle, RolledBack
from squadra.status import FleetStatus, write
from squadra.supervisor import CLAIMED_AT_FILENAME, TickSeams, run_tick
from tests.helpers.cleanup_fakes import FakeCleanup
from tests.helpers.fleet_fakes import FakeBoard, FakeIssue
from tests.helpers.sandbox_fakes import FakeSandbox
from tests.helpers.worktree_fakes import FakeWorktree

# fleet_root, make_status, fake_board, make_issue, fake_sandbox, fake_cleanup,
# fake_worktree, make_seams, make_config are provided by tests/conftest.py


def test_tick_skips_when_lock_held(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    make_issue(5)
    config: SquadraConfig = make_config()
    fleet_root.mkdir(parents=True)
    with (fleet_root / SUPERVISOR_LOCK_FILENAME).open("w") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        assert run_tick(make_seams(), config) == 0
    assert fake_board.calls == []
    assert fake_sandbox.launches == []


def test_claims_unblocked_up_to_cap_in_id_order(
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    make_issue(7)
    make_issue(5, title="feat: add scope revocation")
    make_issue(6)

    assert run_tick(make_seams(), make_config()) == 0  # cap 2

    # The two lowest ids are claimed; #7 stays queued (budget exhausted).
    assert fake_board.item_state(5) == Lifecycle.ACTIVE
    assert fake_board.item_state(6) == Lifecycle.ACTIVE
    assert fake_board.item_state(7) == Lifecycle.QUEUED
    assert "fleet:claimed" in fake_board.issues[5].tags
    assert any(
        isinstance(event, Claimed) and event.runner_id == "runner-5-a1"
        for event in fake_board.comments[5]
    )
    # One sandbox launched per claim, in id order.
    assert [spec.item_id for spec in fake_sandbox.launches] == [5, 6]


def test_inflight_claimed_issue_counts_against_cap(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    make_issue(4, state=Lifecycle.ACTIVE, tags=["fleet:claimed"])
    write(make_status(issue_id=4, runner_id="runner-4-a1", last_heartbeat=_now()), fleet_root)
    from squadra.domain import SandboxRunning  # noqa: PLC0415

    fake_sandbox.seed("squadra-slice-4", SandboxRunning())
    make_issue(5)
    make_issue(6)

    assert run_tick(make_seams(), make_config()) == 0  # cap 2, one inflight -> budget 1

    # Only one new claim fits.
    new_claims = [i for i in (5, 6) if fake_board.item_state(i) == Lifecycle.ACTIVE]
    assert new_claims == [5]


def test_human_active_issue_does_not_count_against_cap(
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    make_issue(4, state=Lifecycle.ACTIVE)  # a human moved this; no fleet:claimed tag
    make_issue(5)
    make_issue(6)

    assert run_tick(make_seams(), make_config()) == 0  # cap 2, no fleet inflight

    assert fake_board.item_state(5) == Lifecycle.ACTIVE
    assert fake_board.item_state(6) == Lifecycle.ACTIVE


def test_blocked_issue_is_skipped_until_predecessors_done(
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    make_issue(4, state=Lifecycle.ACTIVE)  # predecessor still in flight (not done)
    make_issue(5, predecessor_ids=(4,))
    make_issue(6, predecessor_ids=(3,))
    make_issue(3, state=Lifecycle.DONE)

    assert run_tick(make_seams(), make_config()) == 0

    assert fake_board.item_state(5) == Lifecycle.QUEUED  # blocked
    assert fake_board.item_state(6) == Lifecycle.ACTIVE  # unblocked, claimed


def test_fleet_tagged_queued_issue_is_never_claimed(
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    make_issue(5, tags=["fleet:failed"])  # already escalated -> terminal, never re-driven
    assert run_tick(make_seams(), make_config()) == 0
    assert fake_sandbox.launches == []
    assert fake_board.item_state(5) == Lifecycle.QUEUED


def test_claim_creates_worktree_and_injects_context_before_launch(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    fake_worktree: FakeWorktree,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    tmp_path: Path,
) -> None:
    make_issue(5, title="feat: add scope revocation", predecessor_ids=(3,))
    make_issue(3, state=Lifecycle.DONE)
    config: SquadraConfig = make_config(fleet_home=tmp_path)

    contexts: list[tuple[Path, int]] = []

    def _record_context(worktree: Path, context: object) -> Path:
        from squadra.domain import SliceContext  # noqa: PLC0415

        assert isinstance(context, SliceContext)
        contexts.append((worktree, context.issue_id))
        return worktree / ".squadra" / "slice.json"

    assert run_tick(make_seams(write_context=_record_context), config) == 0

    branch = "feat/slice-5-add-scope-revocation"
    expected_wt = tmp_path / config.worktree_dir / branch.replace("/", "+")
    # Worktree created off fresh origin/main before the launch.
    assert fake_worktree.created == [(branch, str(expected_wt), "origin/main")]
    # Slice context injected for #5 (predecessor states read host-side).
    assert contexts == [(expected_wt, 5)]
    # And the sandbox was launched for #5.
    assert [spec.item_id for spec in fake_sandbox.launches] == [5]


def test_slice_context_carries_predecessor_states(
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    make_issue(5, predecessor_ids=(3,))
    make_issue(3, state=Lifecycle.DONE)

    captured: dict[str, object] = {}

    def _capture(worktree: Path, context: object) -> Path:
        from squadra.domain import SliceContext  # noqa: PLC0415

        assert isinstance(context, SliceContext)
        captured["predecessor_states"] = dict(context.predecessor_states)
        return worktree / "slice.json"

    assert run_tick(make_seams(write_context=_capture), make_config()) == 0
    assert captured["predecessor_states"] == {3: "done"}


def test_worktree_create_failure_does_not_claim(
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    fake_worktree: FakeWorktree,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    make_issue(5, title="feat: x")
    fake_worktree.fail_branches.add("feat/slice-5-x")

    assert run_tick(make_seams(), make_config()) == 0

    # No board mutation, no launch — the claim never started.
    assert fake_board.item_state(5) == Lifecycle.QUEUED
    assert "fleet:claimed" not in fake_board.issues[5].tags
    assert fake_sandbox.launches == []


def test_launch_failure_rolls_the_claim_back(
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    make_issue(5, title="feat: x")
    make_issue(6, title="feat: y")
    fake_sandbox.fail_launch.add("squadra-slice-5")

    assert run_tick(make_seams(), make_config()) == 0

    assert fake_board.item_state(5) == Lifecycle.QUEUED
    assert "fleet:claimed" not in fake_board.issues[5].tags
    assert any(isinstance(event, RolledBack) for event in fake_board.comments[5])
    assert fake_board.item_state(6) == Lifecycle.ACTIVE  # the other claim still went


def test_claim_protocol_order_state_then_tag_then_comment(
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    make_issue(5)
    assert run_tick(make_seams(), make_config()) == 0
    mutations: list[str] = [call[0] for call in fake_board.calls if call[1] == 5]
    assert mutations == ["set_state", "add_tag", "add_comment"]


def test_claim_writes_the_claimed_at_marker(
    fleet_root: Path,
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    make_issue(5)
    assert run_tick(make_seams(), make_config()) == 0
    marker: Path = fleet_root / "5" / CLAIMED_AT_FILENAME
    assert marker.is_file()
    assert marker.read_text().strip().startswith("20")


def test_retry_uses_next_attempt_and_suffixes_branch(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    fake_worktree: FakeWorktree,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    # A reaped previous attempt (status attempt=1) -> the next claim runs attempt 2.
    make_issue(41, title="feat: example")
    write(make_status(attempt=1), fleet_root)

    assert run_tick(make_seams(), make_config()) == 0

    assert [spec.item_id for spec in fake_sandbox.launches] == [41]
    assert fake_worktree.created == [
        ("feat/slice-41-example-a2", str(fake_worktree.created[0][1]), "origin/main")
    ]
    assert any(
        isinstance(event, Claimed) and event.branch == "feat/slice-41-example-a2"
        for event in fake_board.comments[41]
    )


def test_cap_zero_suppresses_claims_only(
    fleet_root: Path,
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    fake_cleanup: FakeCleanup,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    # cap 0 must NOT claim, but the non-claim decisions still run (finalize here).
    make_issue(5)  # claimable
    make_issue(40, title="feat: merged", state=Lifecycle.DONE, tags=["fleet:claimed"])
    write(
        make_status(issue_id=40, runner_id="runner-40-a1", branch="feat/slice-40-merged"),
        fleet_root,
    )
    fake_board.completed_prs["feat/slice-40-merged"] = "https://pr/40"

    assert run_tick(make_seams(), make_config(cap=0)) == 0

    assert fake_sandbox.launches == []  # no claim
    assert fake_board.item_state(5) == Lifecycle.QUEUED
    assert fake_cleanup.deleted_branches == ["feat/slice-40-merged"]  # finalize still ran


def test_parent_scope_filter_limits_claims(
    fake_board: FakeBoard,
    make_issue: Callable[..., FakeIssue],
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    make_issue(5, parent_id=68)
    make_issue(6, parent_id=99)
    config: SquadraConfig = make_config(parent_scope_ids=(68,))

    assert run_tick(make_seams(), config) == 0

    assert fake_board.item_state(5) == Lifecycle.ACTIVE
    assert fake_board.item_state(6) == Lifecycle.QUEUED


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
