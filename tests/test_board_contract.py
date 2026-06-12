"""``BoardAccess`` contract-test suite — the provider-blindness proof (ADR-0001).

A conformance suite any ``BoardAccess`` implementation must pass, run here
against two *divergent* fakes:

- an **ADO-shaped** fake (``To Do/Doing/Done`` native states, ``;``-joined tags,
  HTML comments), and
- a **GitHub-shaped** fake (arbitrary Projects-v2 status names, label tags under
  a ``fleet/`` prefix, Markdown comments).

The conformance tests assert the neutral contract holds identically across both
shapes. The provider-blindness tests then drive the real supervisor passes
against the GitHub-shaped fake: had core hardcoded a native state string
(``"Doing"``) or markup (inline HTML), it would break against GitHub semantics.
It does not — core speaks only ``Lifecycle`` + the configured tag vocabulary +
structured ``CommentEvent`` values, which each adapter maps and renders. The
real GitHub/GitLab adapters later become two more implementations this suite
checks.
"""

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from flotilla.board import render_ado_html
from flotilla.config import FlotillaConfig
from flotilla.domain import (
    Claimed,
    CommentEvent,
    Escalated,
    Finalized,
    Lifecycle,
    Reaped,
    RolledBack,
    Tags,
    WorkItem,
    WorkItemLinks,
)
from flotilla.supervisor import TickSeams, claim_pass, finalize_pass, reap_pass
from tests.helpers.fleet_fakes import FakeCleaner, FakeLauncher

# fleet_root, make_config are provided by tests/conftest.py

Renderer = Callable[[CommentEvent, Tags], str]

_ADO_NATIVE: Mapping[Lifecycle, str] = {
    Lifecycle.QUEUED: "To Do",
    Lifecycle.ACTIVE: "Doing",
    Lifecycle.DONE: "Done",
}
# Deliberately divergent: different status names AND a different markup language.
_GITHUB_NATIVE: Mapping[Lifecycle, str] = {
    Lifecycle.QUEUED: "Todo",
    Lifecycle.ACTIVE: "In Progress",
    Lifecycle.DONE: "Shipped",
}


def render_github_markdown(event: CommentEvent, tags: Tags) -> str:
    """Render a ``CommentEvent`` to a GitHub-flavored Markdown comment."""
    match event:
        case Claimed(runner_id=runner_id, branch=branch, when=when):
            return f"**fleet**: claimed by `{runner_id}` on `{branch}` at {when}"
        case RolledBack(reason=reason):
            return f"**fleet**: {reason} — claim rolled back"
        case Finalized(pr_url=pr_url, branch=branch):
            return f"**fleet**: finalized — PR {pr_url}, branch `{branch}` cleaned up"
        case Reaped(evidence=evidence, attempt=attempt):
            return f"**fleet**: reaped — {evidence} (attempt {attempt})"
        case Escalated(attempt=attempt, cap=cap):
            return (
                f"**fleet**: retry cap exhausted ({attempt}/{cap}) — escalated to `{tags.failed}`"
            )


class _ConformanceBoard:
    """A ``BoardAccess`` parameterized by a native-state map + comment renderer.

    One class, two shapes: instantiated once with ADO-native semantics and once
    with GitHub-native semantics. Native state strings and markup live *only*
    here — exactly where a real adapter would own them.
    """

    def __init__(
        self,
        native: Mapping[Lifecycle, str],
        renderer: Renderer,
        tags: Tags = Tags(),
    ) -> None:
        self._native = native
        self._renderer = renderer
        self._tags = tags
        self.states: dict[int, str] = {}
        self.titles: dict[int, str] = {}
        self.tags_by_item: dict[int, list[str]] = {}
        self.links: dict[int, WorkItemLinks] = {}
        self.comments: dict[int, list[str]] = {}
        self.completed: dict[str, str] = {}
        self.validated: bool = False

    def seed(
        self,
        item_id: int,
        lifecycle: Lifecycle,
        title: str = "feat: x",
        tags: tuple[str, ...] = (),
    ) -> None:
        """Seed one work item in its native state for ``lifecycle``."""
        self.states[item_id] = self._native[lifecycle]
        self.titles[item_id] = title
        self.tags_by_item[item_id] = list(tags)

    def items_in_state(self, state: Lifecycle) -> tuple[WorkItem, ...]:
        native: str = self._native[state]
        return tuple(
            WorkItem(item_id, self.titles[item_id], tuple(self.tags_by_item[item_id]))
            for item_id, current in self.states.items()
            if current == native
        )

    def completed_pr_url(self, branch: str) -> str | None:
        return self.completed.get(branch)

    def item_links(self, item_id: int) -> WorkItemLinks:
        return self.links.get(item_id, WorkItemLinks(parent_id=None, predecessor_ids=()))

    def item_state(self, item_id: int) -> Lifecycle:
        native: str = self.states[item_id]
        for lifecycle, name in self._native.items():
            if name == native:
                return lifecycle
        return Lifecycle.QUEUED

    def set_state(self, item_id: int, state: Lifecycle) -> None:
        self.states[item_id] = self._native[state]

    def add_tag(self, item_id: int, tag: str) -> None:
        if tag not in self.tags_by_item[item_id]:
            self.tags_by_item[item_id].append(tag)

    def remove_tag(self, item_id: int, tag: str) -> None:
        if tag in self.tags_by_item[item_id]:
            self.tags_by_item[item_id].remove(tag)

    def add_comment(self, item_id: int, event: CommentEvent) -> None:
        self.comments.setdefault(item_id, []).append(self._renderer(event, self._tags))

    def validate_config(self) -> None:
        self.validated = True


def _ado_board() -> _ConformanceBoard:
    return _ConformanceBoard(_ADO_NATIVE, render_ado_html, Tags("fleet:"))


def _github_board() -> _ConformanceBoard:
    return _ConformanceBoard(_GITHUB_NATIVE, render_github_markdown, Tags("fleet/"))


_BOARDS: list[Callable[[], _ConformanceBoard]] = [_ado_board, _github_board]


# --- conformance: the neutral contract holds across both shapes ----------------


@pytest.mark.parametrize("make_board", _BOARDS)
def test_state_round_trips_through_the_neutral_lifecycle(
    make_board: Callable[[], _ConformanceBoard],
) -> None:
    board = make_board()
    board.seed(1, Lifecycle.QUEUED)
    assert board.item_state(1) == Lifecycle.QUEUED
    assert [item.item_id for item in board.items_in_state(Lifecycle.QUEUED)] == [1]
    board.set_state(1, Lifecycle.ACTIVE)
    assert board.item_state(1) == Lifecycle.ACTIVE
    assert board.items_in_state(Lifecycle.QUEUED) == ()
    assert [item.item_id for item in board.items_in_state(Lifecycle.ACTIVE)] == [1]


@pytest.mark.parametrize("make_board", _BOARDS)
def test_tag_add_is_idempotent_and_removable(
    make_board: Callable[[], _ConformanceBoard],
) -> None:
    board = make_board()
    board.seed(1, Lifecycle.QUEUED)
    board.add_tag(1, "x")
    board.add_tag(1, "x")
    assert board.items_in_state(Lifecycle.QUEUED)[0].tags == ("x",)
    board.remove_tag(1, "x")
    assert board.items_in_state(Lifecycle.QUEUED)[0].tags == ()


@pytest.mark.parametrize("make_board", _BOARDS)
def test_items_in_state_is_empty_for_an_unpopulated_bucket(
    make_board: Callable[[], _ConformanceBoard],
) -> None:
    board = make_board()
    board.seed(1, Lifecycle.QUEUED)
    assert board.items_in_state(Lifecycle.DONE) == ()


def test_the_two_fakes_render_the_same_event_to_divergent_markup() -> None:
    # The whole point of the seam: identical neutral event, provider-owned markup.
    event = Claimed(runner_id="runner-1-a1", branch="feat/slice-1-x", when="2026-06-12")
    ado: str = render_ado_html(event, Tags("fleet:"))
    github: str = render_github_markdown(event, Tags("fleet/"))
    assert ado.startswith("<p>") and "<code>" in ado
    assert github.startswith("**fleet**") and "<p>" not in github
    assert ado != github


# --- provider-blindness: the real supervisor passes drive the GitHub fake ------


def test_claim_pass_drives_a_github_shaped_board_with_no_native_leak(
    fleet_root: Path,
    make_config: Callable[..., FlotillaConfig],
) -> None:
    # Native states "Todo"/"In Progress" and a "fleet/" label prefix — nothing
    # the supervisor hardcodes. A successful claim proves core is provider-blind.
    board = _github_board()
    board.seed(7, Lifecycle.QUEUED, title="feat: ship it")
    config: FlotillaConfig = make_config(tag_prefix="fleet/", fleet_root=fleet_root)
    seams = TickSeams(ado=board, launcher=FakeLauncher(), cleaner=FakeCleaner())

    outcome = claim_pass(seams, config)

    assert outcome.claimed == (7,)
    assert board.states[7] == "In Progress"  # set_state(ACTIVE) hit the GitHub-native column
    assert "fleet/claimed" in board.tags_by_item[7]
    # The comment was rendered as Markdown by the adapter, not HTML by core.
    assert board.comments[7][0].startswith("**fleet**")


def test_finalize_pass_drives_a_github_shaped_board(
    fleet_root: Path,
    make_config: Callable[..., FlotillaConfig],
) -> None:
    board = _github_board()
    board.seed(7, Lifecycle.DONE, title="feat: ship it", tags=("fleet/claimed",))
    board.completed["feat/slice-7-ship-it"] = "https://example/pr/7"
    config: FlotillaConfig = make_config(tag_prefix="fleet/", fleet_root=fleet_root)
    cleaner = FakeCleaner()
    seams = TickSeams(ado=board, launcher=FakeLauncher(), cleaner=cleaner)

    outcome = finalize_pass(seams, config)

    assert outcome.finalized == (7,)
    assert cleaner.cleaned == ["feat/slice-7-ship-it"]
    assert "fleet/claimed" not in board.tags_by_item[7]  # fleet labels dropped
    assert board.comments[7][0].startswith("**fleet**")  # Markdown render


def test_reap_pass_escalates_on_a_github_shaped_board(
    fleet_root: Path,
    make_config: Callable[..., FlotillaConfig],
) -> None:
    # An exhausted, dead, stale claim escalates to the configured failed label —
    # rendered as Markdown — without core ever naming a native state.
    board = _github_board()
    board.seed(7, Lifecycle.ACTIVE, title="feat: ship it", tags=("fleet/claimed",))
    # No status file + no pid sidecar: the runner never started (dead), and a
    # stale claimed-at marker is the only (ancient) liveness evidence — so the
    # claim is reap-eligible, and with the cap at 1 it escalates.
    (fleet_root / "7").mkdir(parents=True)
    (fleet_root / "7" / "claimed-at").write_text("2020-01-01T00:00:00+00:00\n")
    config: FlotillaConfig = make_config(tag_prefix="fleet/", fleet_root=fleet_root, max_attempts=1)
    seams = TickSeams(ado=board, launcher=FakeLauncher(), cleaner=FakeCleaner())

    outcome = reap_pass(seams, config)

    assert outcome.escalated == (7,)
    assert "fleet/failed" in board.tags_by_item[7]
    assert "fleet/claimed" not in board.tags_by_item[7]
    assert board.comments[7][0].startswith("**fleet**")
