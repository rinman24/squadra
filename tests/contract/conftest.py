"""Fixtures for the ResourceAccess conformance suites.

The ``BoardAccess`` suite runs against BOTH a freshly-seeded ADO-shaped fake and
a GitHub-shaped fake, under one shared logical seed, via the parametrized
``board`` fixture. The deterministic ``CleanupAccess`` suite runs against BOTH
the real :class:`flotilla.cleanup.DeterministicCleanup` (driven by a recording
runner — no live git/docker) and an in-memory fake, via the parametrized
``cleanup`` fixture. Fixtures live here (not in the repo-root
``tests/conftest.py``) so the contract suites stay self-contained. Return types
are annotated because Pyright cannot infer parametrized-fixture return types.
"""

from collections.abc import Sequence

import pytest

from flotilla.board import BoardAccess
from flotilla.cleanup import CleanupAccess, DeterministicCleanup
from flotilla.domain import Lifecycle, Tags
from tests.helpers.board_fakes import AdoShapedFakeBoard, GitHubShapedFakeBoard
from tests.helpers.cleanup_fakes import FakeCleanup

# Shared logical seed — the same items, in the same neutral buckets, across both
# shapes. The DONE item on the GitHub fake is seeded under its SECONDARY native
# name ("Closed-merged") to exercise many-native→one-neutral collapse.
TAGS: Tags = Tags()

QUEUED_ID: int = 101
ACTIVE_ID: int = 102
DONE_ID: int = 103
LINKED_ID: int = 104  # has a parent + predecessors

PARENT_ID: int = 200
PRED_IDS: tuple[int, ...] = (150, 151)

PR_BRANCH: str = "feat/slice-103-done"
PR_URL: str = "https://example.invalid/pr/42"


def _seed_ado() -> AdoShapedFakeBoard:
    """Build a correctly-configured ADO-shaped board under the shared seed."""
    board = AdoShapedFakeBoard(tags=TAGS)
    board.add(QUEUED_ID, "queued slice", Lifecycle.QUEUED)
    board.add(ACTIVE_ID, "active slice", Lifecycle.ACTIVE, tags=(TAGS.claimed,))
    board.add(DONE_ID, "done slice", Lifecycle.DONE)
    board.add(
        LINKED_ID,
        "linked slice",
        Lifecycle.QUEUED,
        parent_id=PARENT_ID,
        predecessor_ids=PRED_IDS,
    )
    board.seed_pr(PR_BRANCH, PR_URL)
    return board


def _seed_github() -> GitHubShapedFakeBoard:
    """Build a correctly-configured GitHub-shaped board under the shared seed.

    The DONE item is seeded under the SECONDARY native done-name on purpose.
    """
    board = GitHubShapedFakeBoard(tags=TAGS)
    board.add(QUEUED_ID, "queued slice", Lifecycle.QUEUED)
    board.add(ACTIVE_ID, "active slice", Lifecycle.ACTIVE, tags=(TAGS.claimed,))
    board.add(DONE_ID, "done slice", Lifecycle.DONE, native_status="Closed-merged")
    board.add(
        LINKED_ID,
        "linked slice",
        Lifecycle.QUEUED,
        parent_id=PARENT_ID,
        predecessor_ids=PRED_IDS,
    )
    board.seed_pr(PR_BRANCH, PR_URL)
    return board


@pytest.fixture(params=["ado", "github"])
def board(request: pytest.FixtureRequest) -> BoardAccess:
    """A freshly-seeded conforming board of each shape (parametrized over both)."""
    shape: str = request.param
    if shape == "ado":
        return _seed_ado()
    return _seed_github()


def _all_succeed(_args: Sequence[str]) -> int:
    """A recording-free runner that succeeds — drives the real adapter's happy path."""
    return 0


@pytest.fixture(params=["real", "fake"])
def cleanup(request: pytest.FixtureRequest) -> CleanupAccess:
    """A conforming ``CleanupAccess`` of each shape (real adapter + in-memory fake)."""
    shape: str = request.param
    if shape == "real":
        return DeterministicCleanup(fleet_home="/repo", run=_all_succeed)
    return FakeCleanup()
