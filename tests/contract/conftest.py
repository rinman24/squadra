"""Fixtures for the ``BoardAccess`` conformance suite.

Each contract test runs against BOTH a freshly-seeded ADO-shaped fake and a
GitHub-shaped fake, under one shared logical seed, via a parametrized fixture.
Fixtures live here (not in the repo-root ``tests/conftest.py``) so the contract
suite is self-contained. Return types are annotated because Pyright cannot infer
parametrized-fixture return types.
"""

import pytest

from flotilla.board import BoardAccess
from flotilla.domain import Lifecycle, Tags
from tests.helpers.board_fakes import AdoShapedFakeBoard, GitHubShapedFakeBoard

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
