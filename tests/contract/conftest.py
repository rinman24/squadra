"""Fixtures for the ResourceAccess conformance suites.

The ``BoardAccess`` suite runs against a freshly-seeded ADO-shaped fake, a
GitHub-shaped fake, the registered fake provider (a JSON file under
``tmp_path``) and the real GitHub adapter over an in-memory GitHub (``gh``),
under one shared logical seed, via the parametrized ``board`` fixture. The
``fake_board`` fixture and the increment suites stay on the first three shapes
until the GitHub adapter can create increments (ledger SQ5b). The deterministic ``CleanupAccess`` suite runs against BOTH
the real :class:`squadra.cleanup.DeterministicCleanup` (driven by a recording
runner — no live git/docker) and an in-memory fake, via the parametrized
``cleanup`` fixture. The ``WorktreeAccess`` suite runs against the real
:class:`squadra.worktree.GitWorktreeAccess` (driven by fake git runner + mover)
and an in-memory fake, via the parametrized ``worktree`` fixture. The
``SandboxAccess`` suite runs against a freshly-seeded in-memory
:class:`~tests.helpers.sandbox_fakes.FakeSandbox`, via the ``sandbox`` fixture.
Fixtures live here (not in the repo-root ``tests/conftest.py``) so the contract
suites stay self-contained. Return types are annotated because Pyright cannot
infer parametrized-fixture return types.
"""

from collections.abc import Sequence
from pathlib import Path

import pytest

from squadra.board import BoardAccess
from squadra.cleanup import CleanupAccess, DeterministicCleanup
from squadra.domain import Lifecycle, SandboxSpec, Tags
from squadra.worktree import GitWorktreeAccess, WorktreeAccess
from tests.helpers.board_fakes import (
    ADO_STATES_WITH_WITHDRAWN,
    AdoShapedFakeBoard,
    GitHubShapedFakeBoard,
    RecordingFileBoard,
    SeedableFakeBoard,
)
from tests.helpers.cleanup_fakes import FakeCleanup
from tests.helpers.github_stub import (
    REPOSITORY,
    InMemoryGitHub,
    StubbedGitHubBoard,
    StubIssue,
    StubPull,
)
from tests.helpers.sandbox_fakes import FakeSandbox
from tests.helpers.worktree_fakes import FakeWorktree

# Shared logical seed — the same items, in the same neutral buckets, across both
# shapes. The DONE item on the GitHub fake is seeded under its SECONDARY native
# name ("Closed-merged") to exercise many-native→one-neutral collapse.
TAGS: Tags = Tags()

QUEUED_ID: int = 101
ACTIVE_ID: int = 102
DONE_ID: int = 103
LINKED_ID: int = 104  # has a parent + predecessors
WITHDRAWN_ID: int = 105

PARENT_ID: int = 200
PRED_IDS: tuple[int, ...] = (150, 151)

PR_BRANCH: str = "feat/increment-103-done"
PR_URL: str = "https://example.invalid/pr/42"


def _seed_ado() -> AdoShapedFakeBoard:
    """Build a correctly-configured ADO-shaped board under the shared seed."""
    board = AdoShapedFakeBoard(states=ADO_STATES_WITH_WITHDRAWN, tags=TAGS)
    board.add(QUEUED_ID, "queued increment", Lifecycle.QUEUED)
    board.add(ACTIVE_ID, "active increment", Lifecycle.ACTIVE, tags=(TAGS.claimed,))
    board.add(DONE_ID, "done increment", Lifecycle.DONE)
    board.add(
        LINKED_ID,
        "linked increment",
        Lifecycle.QUEUED,
        parent_id=PARENT_ID,
        predecessor_ids=PRED_IDS,
    )
    board.add(WITHDRAWN_ID, "withdrawn increment", Lifecycle.WITHDRAWN)
    board.seed_pr(PR_BRANCH, PR_URL)
    return board


def _seed_github() -> GitHubShapedFakeBoard:
    """Build a correctly-configured GitHub-shaped board under the shared seed.

    The DONE item is seeded under the SECONDARY native done-name on purpose.
    """
    board = GitHubShapedFakeBoard(tags=TAGS)
    board.add(QUEUED_ID, "queued increment", Lifecycle.QUEUED)
    board.add(ACTIVE_ID, "active increment", Lifecycle.ACTIVE, tags=(TAGS.claimed,))
    board.add(DONE_ID, "done increment", Lifecycle.DONE, native_status="Closed-merged")
    board.add(
        LINKED_ID,
        "linked increment",
        Lifecycle.QUEUED,
        parent_id=PARENT_ID,
        predecessor_ids=PRED_IDS,
    )
    board.add(WITHDRAWN_ID, "withdrawn increment", Lifecycle.WITHDRAWN)
    board.seed_pr(PR_BRANCH, PR_URL)
    return board


def seed_stubbed_github() -> StubbedGitHubBoard:
    """Build the real GitHub adapter over an in-memory GitHub under the shared seed.

    The DONE item is closed as completed with its Status left at "Done" (as the
    "Item closed" workflow leaves it), so it reads under the secondary done name
    ``closed:completed``; the WITHDRAWN item is closed as not planned with a
    queued Status, which closure must win over. Pages of two items exercise
    the project listing's pagination.
    """
    github = InMemoryGitHub(page_size=2)
    github.add(StubIssue(QUEUED_ID, "queued increment", status="Todo"))
    github.add(
        StubIssue(ACTIVE_ID, "active increment", status="In Progress", labels=[TAGS.claimed])
    )
    github.add(
        StubIssue(DONE_ID, "done increment", is_open=False, state_reason="COMPLETED", status="Done")
    )
    github.add(
        StubIssue(
            LINKED_ID,
            "linked increment",
            status="Triage",
            parent=(REPOSITORY, PARENT_ID),
            blocked_by=[(REPOSITORY, pred) for pred in PRED_IDS],
        )
    )
    github.add(
        StubIssue(
            WITHDRAWN_ID,
            "withdrawn increment",
            is_open=False,
            state_reason="NOT_PLANNED",
            status="Todo",
        )
    )
    owner: str = REPOSITORY.split("/")[0]
    github.pulls.append(StubPull(f"{owner}:{PR_BRANCH}", "main", PR_URL, merged=True))
    github.pulls.append(StubPull(f"{owner}:feat/closed-unmerged", "main", "x", merged=False))
    return StubbedGitHubBoard(github, tags=TAGS)


def seed_file_board(tmp_path: Path) -> RecordingFileBoard:
    """Build the registered fake provider under the shared seed, in a fresh file."""
    board = RecordingFileBoard(tmp_path / ".squadra" / "fake-board.json", tags=TAGS)
    board.add(QUEUED_ID, "queued increment", Lifecycle.QUEUED)
    board.add(ACTIVE_ID, "active increment", Lifecycle.ACTIVE, tags=(TAGS.claimed,))
    board.add(DONE_ID, "done increment", Lifecycle.DONE)
    board.add(
        LINKED_ID,
        "linked increment",
        Lifecycle.QUEUED,
        parent_id=PARENT_ID,
        predecessor_ids=PRED_IDS,
    )
    board.add(WITHDRAWN_ID, "withdrawn increment", Lifecycle.WITHDRAWN)
    board.seed_pr(PR_BRANCH, PR_URL)
    board.state_writes.clear()
    return board


def seed_fake(shape: str, tmp_path: Path) -> SeedableFakeBoard:
    """One of the three fakes under the shared seed."""
    if shape == "ado":
        return _seed_ado()
    if shape == "github":
        return _seed_github()
    return seed_file_board(tmp_path)


# The fakes; every suite runs against these.
SHAPES: tuple[str, ...] = ("ado", "github", "file")
# Plus the real GitHub adapter over the in-memory GitHub: the BoardAccess suite only.
BOARD_SHAPES: tuple[str, ...] = (*SHAPES, "gh")


@pytest.fixture(params=BOARD_SHAPES)
def board(request: pytest.FixtureRequest, tmp_path: Path) -> BoardAccess:
    """A freshly-seeded conforming board of each shape (parametrized over all four)."""
    if request.param == "gh":
        return seed_stubbed_github()
    return seed_fake(request.param, tmp_path)


@pytest.fixture(params=SHAPES)
def fake_board(request: pytest.FixtureRequest, tmp_path: Path) -> SeedableFakeBoard:
    """The ``board`` seed of each fake, typed concretely so tests can seed Origins by hand."""
    return seed_fake(request.param, tmp_path)


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


def _noop_move(_src: str, _dest: str) -> None:
    """A move that does nothing — drives the real adapter's archive without disk I/O."""


@pytest.fixture(params=["real", "fake"])
def worktree(request: pytest.FixtureRequest) -> WorktreeAccess:
    """A conforming ``WorktreeAccess`` of each shape (real adapter + in-memory fake)."""
    shape: str = request.param
    if shape == "real":
        return GitWorktreeAccess(fleet_home="/repo", run=_all_succeed, move=_noop_move)
    return FakeWorktree()


# --- SandboxAccess conformance fixtures ---------------------------------------

# The shared sandbox seed — one increment's per-increment ephemeral compose project.
SANDBOX_ITEM_ID: int = 141
SANDBOX_PROJECT: str = "squadra-increment-141"


@pytest.fixture
def sandbox_spec() -> SandboxSpec:
    """The shared per-increment sandbox spec the contract exercises."""
    return SandboxSpec(
        item_id=SANDBOX_ITEM_ID,
        project=SANDBOX_PROJECT,
        compose_file=Path("/work/.squadra/compose.yaml"),
        worktree=Path("/work"),
    )


@pytest.fixture
def sandbox() -> FakeSandbox:
    """A freshly-seeded conforming ``SandboxAccess`` (the in-memory fake)."""
    return FakeSandbox()
