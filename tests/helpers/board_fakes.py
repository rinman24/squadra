"""Two deliberately-divergent in-memory ``BoardAccess`` fakes for the contract suite.

Both fakes conform structurally to :class:`squadra.board.BoardAccess`, but they
model their native semantics differently on purpose, to prove the seam is
provider-blind:

- :class:`AdoShapedFakeBoard` mimics Azure DevOps Basic: native states are the
  ADO-Basic strings (``To Do``/``Doing``/``Done``), with no withdrawn state
  unless one is configured (the contract seed adds ``Removed``, as ADO's other
  processes have); tags are stored as a single
  ``;``-joined ``System.Tags``-style string; comments render to HTML via the
  shipped :func:`squadra.board.render_ado_html`.
- :class:`GitHubShapedFakeBoard` mimics a GitHub Projects board: arbitrary
  status names (many native names map to one neutral bucket); tags are a
  ``list[str]`` label model; comments render to Markdown via a local
  :func:`render_github_markdown`.

Each fake stores a seedable in-memory board and records its mutations so tests
can inspect recorded comments / tags / state without asserting native dialect.
Both store an Origin their own way: the ADO fake in a field of its own, the
GitHub fake in a hidden marker appended to the issue body (escaped, so any
string round-trips). ``items_with_origin`` hands back exactly what
``create_increment`` was given. Both honour the adapter's mapping rules: an unmapped native state raises rather
than defaulting to a bucket, and ``validate_config`` fails on a configured name
the board lacks or a board state the map leaves out.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import json

from squadra.board import BoardValidationError, render_ado_html
from squadra.config import ADO_BASIC_STATES
from squadra.domain import (
    Claimed,
    CommentEvent,
    Escalated,
    Finalized,
    IncrementRequest,
    Lifecycle,
    OriginRecord,
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
    Lifecycle.WITHDRAWN: ("Withdrawn",),
}
# ADO-Basic plus the ``Removed`` state of ADO's other processes, mapped to WITHDRAWN.
ADO_STATES_WITH_WITHDRAWN: Mapping[Lifecycle, tuple[str, ...]] = {
    **ADO_BASIC_STATES,
    Lifecycle.WITHDRAWN: ("Removed",),
}


def render_github_markdown(event: CommentEvent, tags: Tags) -> str:
    """Render a structured ``CommentEvent`` to one GitHub Markdown comment.

    Deliberately a distinct dialect from :func:`squadra.board.render_ado_html`
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
    origin: str | None = None  # a field of its own (an ADO custom field)
    body: str = ""  # System.Description


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
        names: tuple[str, ...] = self._states.get(state, ())
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
        raise BoardValidationError(f"native state {native!r} maps to no lifecycle bucket")

    def set_state(self, item_id: int, state: Lifecycle) -> None:
        """Write the first native name of ``state`` and record the write."""
        native: str = _first_native(self._states, state)
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
        """Raise if a configured state is absent from the board, or a board state is unmapped."""
        _check_state_map(self._states, self._available)

    def create_increment(self, request: IncrementRequest) -> int:
        """Create a QUEUED Issue carrying the Origin in a field of its own."""
        item_id: int = _next_id(self.items.keys())
        self.items[item_id] = _AdoItem(
            item_id=item_id,
            title=request.title,
            state=_first_native(self._states, Lifecycle.QUEUED),
            parent_id=request.parent,
            predecessor_ids=request.predecessors,
            origin=request.origin,
            body=request.body,
        )
        return item_id

    def items_with_origin(self) -> tuple[OriginRecord, ...]:
        """Return every Issue whose Origin field is set, duplicates included."""
        return tuple(
            OriginRecord(
                item_id=item.item_id,
                origin=item.origin,
                parent=item.parent_id,
                predecessors=item.predecessor_ids,
                title=item.title,
                body=item.body,
                lifecycle=self.item_state(item.item_id),
            )
            for item in self.items.values()
            if item.origin is not None
        )

    def seed_origin(self, item_id: int, origin: str) -> None:
        """Set an Origin on a seeded Issue by hand (e.g. to inject a duplicate)."""
        self.items[item_id].origin = origin


def _first_native(states: Mapping[Lifecycle, tuple[str, ...]], state: Lifecycle) -> str:
    """The native name a write to ``state`` uses; raise if the bucket is unmapped."""
    names: tuple[str, ...] = states.get(state, ())
    if not names:
        raise BoardValidationError(f"no native state maps to {state.value!r}")
    return names[0]


def _check_state_map(states: Mapping[Lifecycle, tuple[str, ...]], available: set[str]) -> None:
    """Check a state map against a board's states both ways, as the adapter does."""
    configured: set[str] = {name for names in states.values() for name in names}
    missing: list[str] = sorted(configured - available)
    if missing:
        raise BoardValidationError(
            f"configured state(s) {missing} not among available {sorted(available)}"
        )
    unmapped: list[str] = sorted(available - configured)
    if unmapped:
        raise BoardValidationError(f"board state(s) {unmapped} map to no lifecycle bucket")


def _next_id(taken: Iterable[int]) -> int:
    """The next free item id on a fake board (ids above every seeded one)."""
    return max(taken, default=1000) + 1


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
    body: str = ""  # the issue body; an Origin rides in a hidden marker at its end


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
        names: tuple[str, ...] = self._states.get(state, ())
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
        raise BoardValidationError(f"status {status!r} maps to no lifecycle bucket")

    def set_state(self, item_id: int, state: Lifecycle) -> None:
        """Write the first native status of ``state`` and record the write."""
        status: str = _first_native(self._states, state)
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
        """Raise if a configured status is absent from the board, or a board status is unmapped."""
        _check_state_map(self._states, self._available)

    def create_increment(self, request: IncrementRequest) -> int:
        """Create a queued issue with the Origin in a hidden marker at the end of its body."""
        item_id: int = _next_id(self.items.keys())
        self.items[item_id] = _GitHubItem(
            item_id=item_id,
            title=request.title,
            status=_first_native(self._states, Lifecycle.QUEUED),
            parent_id=request.parent,
            predecessor_ids=request.predecessors,
            body=_with_origin_marker(request.body, request.origin),
        )
        return item_id

    def items_with_origin(self) -> tuple[OriginRecord, ...]:
        """Return every issue whose body ends in an Origin marker, duplicates included."""
        records: list[OriginRecord] = []
        for item in self.items.values():
            split: tuple[str, str] | None = _split_origin_marker(item.body)
            if split is None:
                continue
            body, origin = split
            records.append(
                OriginRecord(
                    item_id=item.item_id,
                    origin=origin,
                    parent=item.parent_id,
                    predecessors=item.predecessor_ids,
                    title=item.title,
                    body=body,
                    lifecycle=self.item_state(item.item_id),
                )
            )
        return tuple(records)

    def seed_origin(self, item_id: int, origin: str) -> None:
        """Set an Origin on a seeded issue by hand (e.g. to inject a duplicate)."""
        item: _GitHubItem = self.items[item_id]
        item.body = _with_origin_marker(item.body, origin)


_ORIGIN_MARKER: str = "\n<!-- squadra-origin: "
_MARKER_END: str = " -->"


def _with_origin_marker(body: str, origin: str) -> str:
    """Append the Origin as a hidden HTML comment; JSON plus escaped ``-`` keeps it inert."""
    encoded: str = json.dumps(origin).replace("-", "\\u002d")
    return f"{body}{_ORIGIN_MARKER}{encoded}{_MARKER_END}"


def _split_origin_marker(raw: str) -> tuple[str, str] | None:
    """Split a body into (body, origin), or ``None`` when it carries no marker."""
    at: int = raw.rfind(_ORIGIN_MARKER)
    if at < 0 or not raw.endswith(_MARKER_END):
        return None
    encoded: str = raw[at + len(_ORIGIN_MARKER) : -len(_MARKER_END)]
    decoded: object = json.loads(encoded)
    return (raw[:at], decoded) if isinstance(decoded, str) else None
