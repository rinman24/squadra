"""In-memory fakes for the AFK-fleet supervisor seams (BoardAccess, Launcher).

Public so test files can annotate fixture parameters with the concrete types
(Pyright cannot infer fixture types; see Testing Conventions in CLAUDE.md).
Instances are provided by fixtures in tests/conftest.py.

``FakeBoard`` implements the provider-neutral ``BoardAccess`` contract: it
speaks :class:`~flotilla.domain.Lifecycle`, returns ``WorkItem`` records, and
stores the structured ``CommentEvent`` objects core emits (no markup) so tests
assert on event identity, not rendered HTML.
"""

from dataclasses import dataclass, field

from flotilla.domain import CommentEvent, Lifecycle, WorkItem, WorkItemLinks


@dataclass
class FakeIssue:
    """Mutable board work item held by the fake board."""

    item_id: int
    title: str
    state: Lifecycle
    tags: list[str] = field(default_factory=list[str])
    parent_id: int | None = None
    predecessor_ids: tuple[int, ...] = ()


class FakeBoard:
    """Configurable in-memory ``BoardAccess``; records every mutation in order.

    ``calls`` records ``(op, item_id, payload)`` tuples where ``payload`` is the
    :class:`~flotilla.domain.Lifecycle` for state ops, the tag string for tag
    ops, the :data:`~flotilla.domain.CommentEvent` for comments, and the queried
    ``Lifecycle`` for ``items_in_state`` (with a sentinel item id of 0).
    """

    def __init__(self) -> None:
        """Start with an empty board."""
        self.issues: dict[int, FakeIssue] = {}
        self.comments: dict[int, list[CommentEvent]] = {}
        self.calls: list[tuple[str, int, object]] = []
        self.completed_prs: dict[str, str] = {}  # branch -> completed PR url
        self.validated: bool = False

    def add_issue(self, issue: FakeIssue) -> None:
        """Seed the board with one work item."""
        self.issues[issue.item_id] = issue

    def items_in_state(self, state: Lifecycle) -> tuple[WorkItem, ...]:
        """Return seeded work items currently in ``state``."""
        self.calls.append(("items_in_state", 0, state))
        return tuple(
            WorkItem(item_id=issue.item_id, title=issue.title, tags=tuple(issue.tags))
            for issue in self.issues.values()
            if issue.state == state
        )

    def item_links(self, item_id: int) -> WorkItemLinks:
        """Return the seeded parent / predecessor links."""
        issue: FakeIssue = self.issues[item_id]
        return WorkItemLinks(parent_id=issue.parent_id, predecessor_ids=issue.predecessor_ids)

    def completed_pr_url(self, branch: str) -> str | None:
        """Return the seeded completed-PR url for ``branch``, if any."""
        return self.completed_prs.get(branch)

    def item_state(self, item_id: int) -> Lifecycle:
        """Return the work item's current lifecycle bucket."""
        return self.issues[item_id].state

    def set_state(self, item_id: int, state: Lifecycle) -> None:
        """Record and apply a state transition."""
        self.calls.append(("set_state", item_id, state))
        self.issues[item_id].state = state

    def add_tag(self, item_id: int, tag: str) -> None:
        """Record and apply a tag addition."""
        self.calls.append(("add_tag", item_id, tag))
        if tag not in self.issues[item_id].tags:
            self.issues[item_id].tags.append(tag)

    def remove_tag(self, item_id: int, tag: str) -> None:
        """Record and apply a tag removal."""
        self.calls.append(("remove_tag", item_id, tag))
        if tag in self.issues[item_id].tags:
            self.issues[item_id].tags.remove(tag)

    def add_comment(self, item_id: int, event: CommentEvent) -> None:
        """Record the structured discussion event core emitted."""
        self.calls.append(("add_comment", item_id, event))
        self.comments.setdefault(item_id, []).append(event)

    def validate_config(self) -> None:
        """Record that the startup validation ran (the fake board always passes)."""
        self.validated = True


class FakeLauncher:
    """Spy launcher; configurable to fail for chosen item ids."""

    def __init__(self) -> None:
        """Start with no recorded launches and no configured failures."""
        self.launches: list[tuple[int, str, int]] = []
        self.fail_for: set[int] = set()

    def launch(self, item_id: int, branch: str, attempt: int) -> bool:
        """Record the launch; fail when the item id is in ``fail_for``."""
        self.launches.append((item_id, branch, attempt))
        return item_id not in self.fail_for


class FakeCleaner:
    """Spy cleaner; configurable to fail for chosen branches."""

    def __init__(self) -> None:
        """Start with no recorded cleanups and no configured failures."""
        self.cleaned: list[str] = []
        self.fail_for: set[str] = set()

    def cleanup(self, branch: str) -> bool:
        """Record the cleanup; fail when the branch is in ``fail_for``."""
        self.cleaned.append(branch)
        return branch not in self.fail_for
