"""Two deliberately-divergent in-memory ``BoardAccess`` fakes for the contract suite.

Both fakes conform structurally to :class:`flotilla.board.BoardAccess`, but they
model their native semantics differently on purpose, to prove the seam is
provider-blind:

- :class:`AdoShapedFakeBoard` mimics Azure DevOps Basic: native states are the
  ADO-Basic strings (``To Do``/``Doing``/``Done``); tags are stored as a single
  ``;``-joined ``System.Tags``-style string; comments render to HTML via the
  shipped :func:`flotilla.board.render_ado_html`.
- :class:`GitHubShapedFakeBoard` mimics a GitHub Projects board: arbitrary
  status names (many native names map to one neutral bucket); tags are a
  ``list[str]`` label model; comments render to Markdown via a local
  :func:`render_github_markdown`.

Each fake stores a seedable in-memory board and records its mutations so tests
can inspect recorded comments / tags / state without asserting native dialect.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from flotilla.board import BoardValidationError, render_ado_html
from flotilla.config import ADO_BASIC_STATES
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

# A GitHub-shaped status map: MANY native names per neutral bucket on purpose,
# so the contract exercises the many-native→one-neutral collapse.
GITHUB_STATES: Mapping[Lifecycle, tuple[str, ...]] = {
    Lifecycle.QUEUED: ("Backlog", "Triage"),
    Lifecycle.ACTIVE: ("In Progress",),
    Lifecycle.DONE: ("Shipped", "Closed-merged"),
}


def render_github_markdown(event: CommentEvent, tags: Tags) -> str:
    """Render a structured ``CommentEvent`` to one GitHub Markdown comment.

    Deliberately a distinct dialect from :func:`flotilla.board.render_ado_html`
    (Markdown, not HTML) so the contract suite cannot accidentally depend on
    one provider's markup.
    """
    match event:
        case Claimed(runner_id=runner_id, branch=branch, when=when):
            return f"**fleet: claimed** by `{runner_id}` on `{branch}` ({when})."
        case RolledBack(reason=reason):
            return f"**fleet:** {reason} — claim rolled back."
        case Finalized(pr_url=pr_url, branch=branch):
            return f"**fleet: finalized** — PR [{pr_url}]({pr_url}), `{branch}` cleaned up."
        case Reaped(evidence=evidence, attempt=attempt):
            return f"**fleet: reaped** — {evidence} (attempt {attempt}); requeued."
        case Escalated(attempt=attempt, cap=cap):
            return (
                f"**fleet:** retry cap exhausted ({attempt}/{cap}) — escalated to `{tags.failed}`."
            )


# --- ADO-shaped fake ----------------------------------------------------------


@dataclass
class _AdoItem:
    """One Issue on the ADO-shaped fake board."""

    item_id: int
    title: str
    state: str  # native ADO-Basic state string
    tags_raw: str = ""  # ``;``-joined System.Tags-style string
    parent_id: int | None = None
    predecessor_ids: tuple[int, ...] = ()


class AdoShapedFakeBoard:
    """``BoardAccess`` fake whose native semantics mimic Azure DevOps Basic."""

    def __init__(
        self,
        *,
        states: Mapping[Lifecycle, tuple[str, ...]] = ADO_BASIC_STATES,
        available_states: tuple[str, ...] | None = None,
        tags: Tags = Tags(),
    ) -> None:
        """Seed an empty board.

        ``available_states`` is the set of state names the board "knows about"
        (what ``validate_config`` checks the configured map against); when
        omitted it defaults to the union of ``states`` (a valid board).
        """
        self._states = states
        self._tags = tags
        self._available: set[str] = set(
            available_states
            if available_states is not None
            else (name for names in states.values() for name in names)
        )
        self.items: dict[int, _AdoItem] = {}
        # recorded mutations, for assertions
        self.comments: dict[int, list[str]] = {}
        self.state_writes: list[tuple[int, str]] = []
        self.tag_writes: list[tuple[str, int, str]] = []
        self.completed_prs: dict[str, str] = {}

    def add(
        self,
        item_id: int,
        title: str,
        lifecycle: Lifecycle,
        *,
        native_state: str | None = None,
        tags: tuple[str, ...] = (),
        parent_id: int | None = None,
        predecessor_ids: tuple[int, ...] = (),
    ) -> None:
        """Seed one Issue, in ``lifecycle`` (or a specific ``native_state``)."""
        state: str = native_state if native_state is not None else self._states[lifecycle][0]
        self.items[item_id] = _AdoItem(
            item_id=item_id,
            title=title,
            state=state,
            tags_raw="; ".join(tags),
            parent_id=parent_id,
            predecessor_ids=predecessor_ids,
        )

    def seed_pr(self, branch: str, url: str) -> None:
        """Seed a completed-PR url for ``branch``."""
        self.completed_prs[branch] = url

    # --- BoardAccess surface --------------------------------------------------

    def items_in_state(self, state: Lifecycle) -> tuple[WorkItem, ...]:
        """Return the work items whose native state maps to ``state``."""
        names: tuple[str, ...] = self._states[state]
        return tuple(
            WorkItem(item_id=item.item_id, title=item.title, tags=_split_ado_tags(item.tags_raw))
            for item in self.items.values()
            if item.state in names
        )

    def completed_pr_url(self, branch: str) -> str | None:
        """Return the seeded completed-PR url for ``branch``, if any."""
        return self.completed_prs.get(branch)

    def item_links(self, item_id: int) -> WorkItemLinks:
        """Return the seeded parent / predecessor links of the item."""
        item: _AdoItem = self.items[item_id]
        return WorkItemLinks(parent_id=item.parent_id, predecessor_ids=item.predecessor_ids)

    def item_state(self, item_id: int) -> Lifecycle:
        """Reverse-map the item's native state to its lifecycle bucket."""
        native: str = self.items[item_id].state
        for lifecycle, names in self._states.items():
            if native in names:
                return lifecycle
        return Lifecycle.QUEUED

    def set_state(self, item_id: int, state: Lifecycle) -> None:
        """Write the first native name of ``state`` and record the write."""
        native: str = self._states[state][0]
        self.items[item_id].state = native
        self.state_writes.append((item_id, native))

    def add_tag(self, item_id: int, tag: str) -> None:
        """Append ``tag`` to System.Tags (idempotent)."""
        tags: list[str] = _split_ado_tags_list(self.items[item_id].tags_raw)
        if tag in tags:
            return
        tags.append(tag)
        self.items[item_id].tags_raw = "; ".join(tags)
        self.tag_writes.append(("add", item_id, tag))

    def remove_tag(self, item_id: int, tag: str) -> None:
        """Filter ``tag`` out of System.Tags (no-op if absent)."""
        tags: list[str] = _split_ado_tags_list(self.items[item_id].tags_raw)
        if tag not in tags:
            return
        tags = [name for name in tags if name != tag]
        self.items[item_id].tags_raw = "; ".join(tags)
        self.tag_writes.append(("remove", item_id, tag))

    def add_comment(self, item_id: int, event: CommentEvent) -> None:
        """Render ``event`` to ADO HTML and record it."""
        html: str = render_ado_html(event, self._tags)
        self.comments.setdefault(item_id, []).append(html)

    def validate_config(self) -> None:
        """Raise if any configured state is absent from the available set."""
        configured: set[str] = {name for names in self._states.values() for name in names}
        missing: list[str] = sorted(name for name in configured if name not in self._available)
        if missing:
            raise BoardValidationError(
                f"configured board state(s) {missing} not among available {sorted(self._available)}"
            )


def _split_ado_tags(raw: str) -> tuple[str, ...]:
    """Split a ``;``-joined System.Tags-style string into a tuple."""
    return tuple(part.strip() for part in raw.split(";") if part.strip())


def _split_ado_tags_list(raw: str) -> list[str]:
    """Split a ``;``-joined System.Tags-style string into a list."""
    return [part.strip() for part in raw.split(";") if part.strip()]


# --- GitHub-shaped fake -------------------------------------------------------


@dataclass
class _GitHubItem:
    """One issue on the GitHub-shaped fake board (label model, sub-issue links)."""

    item_id: int
    title: str
    status: str  # arbitrary GitHub Projects status name
    labels: list[str] = field(default_factory=list[str])
    parent_id: int | None = None  # "sub-issue" parent
    predecessor_ids: tuple[int, ...] = ()  # "dependency" links


class GitHubShapedFakeBoard:
    """``BoardAccess`` fake whose native semantics mimic a GitHub Projects board.

    Divergent from the ADO fake on purpose: arbitrary status names (many per
    bucket), labels as a list, and Markdown comments.
    """

    def __init__(
        self,
        *,
        states: Mapping[Lifecycle, tuple[str, ...]] = GITHUB_STATES,
        available_statuses: tuple[str, ...] | None = None,
        tags: Tags = Tags(),
    ) -> None:
        """Seed an empty board; ``available_statuses`` drives ``validate_config``."""
        self._states = states
        self._tags = tags
        self._available: set[str] = set(
            available_statuses
            if available_statuses is not None
            else (name for names in states.values() for name in names)
        )
        self.items: dict[int, _GitHubItem] = {}
        self.comments: dict[int, list[str]] = {}
        self.state_writes: list[tuple[int, str]] = []
        self.tag_writes: list[tuple[str, int, str]] = []
        self.completed_prs: dict[str, str] = {}

    def add(
        self,
        item_id: int,
        title: str,
        lifecycle: Lifecycle,
        *,
        native_status: str | None = None,
        tags: tuple[str, ...] = (),
        parent_id: int | None = None,
        predecessor_ids: tuple[int, ...] = (),
    ) -> None:
        """Seed one issue in ``lifecycle`` (or a specific ``native_status``)."""
        status: str = native_status if native_status is not None else self._states[lifecycle][0]
        self.items[item_id] = _GitHubItem(
            item_id=item_id,
            title=title,
            status=status,
            labels=list(tags),
            parent_id=parent_id,
            predecessor_ids=predecessor_ids,
        )

    def seed_pr(self, branch: str, url: str) -> None:
        """Seed a merged-PR url for ``branch``."""
        self.completed_prs[branch] = url

    # --- BoardAccess surface --------------------------------------------------

    def items_in_state(self, state: Lifecycle) -> tuple[WorkItem, ...]:
        """Return the issues whose native status maps to ``state``."""
        names: tuple[str, ...] = self._states[state]
        return tuple(
            WorkItem(item_id=item.item_id, title=item.title, tags=tuple(item.labels))
            for item in self.items.values()
            if item.status in names
        )

    def completed_pr_url(self, branch: str) -> str | None:
        """Return the seeded merged-PR url for ``branch``, if any."""
        return self.completed_prs.get(branch)

    def item_links(self, item_id: int) -> WorkItemLinks:
        """Return the seeded sub-issue parent / dependency links."""
        item: _GitHubItem = self.items[item_id]
        return WorkItemLinks(parent_id=item.parent_id, predecessor_ids=item.predecessor_ids)

    def item_state(self, item_id: int) -> Lifecycle:
        """Reverse-map the issue's native status to its lifecycle bucket."""
        status: str = self.items[item_id].status
        for lifecycle, names in self._states.items():
            if status in names:
                return lifecycle
        return Lifecycle.QUEUED

    def set_state(self, item_id: int, state: Lifecycle) -> None:
        """Write the first native status of ``state`` and record the write."""
        status: str = self._states[state][0]
        self.items[item_id].status = status
        self.state_writes.append((item_id, status))

    def add_tag(self, item_id: int, tag: str) -> None:
        """Add ``tag`` to the label list (idempotent)."""
        labels: list[str] = self.items[item_id].labels
        if tag in labels:
            return
        labels.append(tag)
        self.tag_writes.append(("add", item_id, tag))

    def remove_tag(self, item_id: int, tag: str) -> None:
        """Remove ``tag`` from the label list (no-op if absent)."""
        labels: list[str] = self.items[item_id].labels
        if tag not in labels:
            return
        labels.remove(tag)
        self.tag_writes.append(("remove", item_id, tag))

    def add_comment(self, item_id: int, event: CommentEvent) -> None:
        """Render ``event`` to Markdown and record it."""
        markdown: str = render_github_markdown(event, self._tags)
        self.comments.setdefault(item_id, []).append(markdown)

    def validate_config(self) -> None:
        """Raise if any configured status is absent from the available set."""
        configured: set[str] = {name for names in self._states.values() for name in names}
        missing: list[str] = sorted(name for name in configured if name not in self._available)
        if missing:
            raise BoardValidationError(
                f"configured status(es) {missing} not among available {sorted(self._available)}"
            )
