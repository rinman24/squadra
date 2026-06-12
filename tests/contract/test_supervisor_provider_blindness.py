"""Provider-blindness proof: drive the REAL supervisor passes against a fake
whose native dialect is nothing like ADO's.

The conformance tests prove the seam *contract* holds across shapes; this module
proves the thing that contract exists for — that the supervisor's claim/reap/
finalize logic contains no hardcoded native state string or markup. It runs the
shipped passes against ``GitHubShapedFakeBoard`` (arbitrary statuses like
"Backlog"/"In Progress"/"Closed-merged", label tags, Markdown comments). Had core
named "Doing" or emitted HTML, these would fail; they pass because core speaks
only ``Lifecycle`` + the configured tag vocabulary + structured ``CommentEvent``s.
"""

from collections.abc import Callable
from pathlib import Path

from flotilla.config import FlotillaConfig
from flotilla.domain import Lifecycle, Tags
from flotilla.supervisor import TickSeams, claim_pass, finalize_pass, reap_pass
from tests.helpers.board_fakes import GITHUB_STATES, GitHubShapedFakeBoard
from tests.helpers.fleet_fakes import FakeCleaner, FakeLauncher

# fleet_root + make_config come from the repo-root tests/conftest.py.


def _seams(board: GitHubShapedFakeBoard) -> TickSeams:
    return TickSeams(ado=board, launcher=FakeLauncher(), cleaner=FakeCleaner())


def test_claim_pass_moves_a_github_native_item_with_no_state_or_markup_leak(
    fleet_root: Path,
    make_config: Callable[..., FlotillaConfig],
) -> None:
    board = GitHubShapedFakeBoard(tags=Tags())  # native "Backlog"/"In Progress"/...
    board.add(7, "feat: ship it", Lifecycle.QUEUED)
    config: FlotillaConfig = make_config(fleet_root=fleet_root)  # default tag_prefix "fleet:"

    outcome = claim_pass(_seams(board), config)

    assert outcome.claimed == (7,)
    assert board.item_state(7) == Lifecycle.ACTIVE
    # The supervisor wrote the GitHub-native ACTIVE column without naming it.
    assert board.items[7].status == GITHUB_STATES[Lifecycle.ACTIVE][0] == "In Progress"
    assert "fleet:claimed" in board.items[7].labels
    # The comment is the adapter's Markdown, never core HTML.
    assert board.comments[7][0].startswith("**fleet")
    assert "<p>" not in board.comments[7][0]


def test_finalize_pass_retires_a_github_native_done_item(
    fleet_root: Path,
    make_config: Callable[..., FlotillaConfig],
) -> None:
    board = GitHubShapedFakeBoard(tags=Tags())
    # Seeded under the SECONDARY native done name to exercise many-native→one.
    board.add(
        7, "feat: ship it", Lifecycle.DONE, native_status="Closed-merged", tags=("fleet:claimed",)
    )
    board.seed_pr("feat/slice-7-ship-it", "https://example.invalid/pr/7")
    config: FlotillaConfig = make_config(fleet_root=fleet_root)

    outcome = finalize_pass(_seams(board), config)

    assert outcome.finalized == (7,)
    assert "fleet:claimed" not in board.items[7].labels  # fleet labels dropped
    assert board.comments[7][0].startswith("**fleet")  # Markdown render


def test_reap_pass_escalates_a_github_native_item_at_the_cap(
    fleet_root: Path,
    make_config: Callable[..., FlotillaConfig],
) -> None:
    board = GitHubShapedFakeBoard(tags=Tags())
    board.add(7, "feat: ship it", Lifecycle.ACTIVE, tags=("fleet:claimed",))
    # No status file + no pid sidecar (runner never started → dead); an ancient
    # claimed-at marker is the only liveness evidence, so it is reap-eligible.
    (fleet_root / "7").mkdir(parents=True)
    (fleet_root / "7" / "claimed-at").write_text("2020-01-01T00:00:00+00:00\n", encoding="utf-8")
    config: FlotillaConfig = make_config(fleet_root=fleet_root, max_attempts=1)

    outcome = reap_pass(_seams(board), config)

    assert outcome.escalated == (7,)
    assert "fleet:failed" in board.items[7].labels
    assert "fleet:claimed" not in board.items[7].labels
    assert board.comments[7][0].startswith("**fleet")
