"""The GitHub ``BoardAccess`` adapter: one repository's issues on one Projects v2 board.

Transport is the ``gh`` CLI (``gh api`` for REST, ``gh api graphql`` for
GraphQL), injected as ``run`` the way :class:`squadra.board.AzCliAdo` takes
``az``, so tests answer it from an in-memory GitHub. Auth is ``gh``'s own
(``gh auth login`` / ``GH_TOKEN`` in the environment); no token ever reaches argv.

**Native state** (ledger N20, ADR-0006) is computed, not stored. An issue's
native state is ``closed:<reason>`` once it is closed, whatever its Status
says (closure wins), else its project ``Status`` option name. The closed names
are squadra's: :data:`CLOSED_COMPLETED`, :data:`CLOSED_NOT_PLANNED`,
:data:`CLOSED_DUPLICATE`, and :data:`CLOSED_NO_REASON` for a closed issue that
carries no reason. ``completed`` here names GitHub's close reason only, never
delivery: the DONE bucket is whatever ``[board.states].done`` maps.

An issue that carries an Origin (a hidden marker at the end of its body) is
*partial* until it also carries :data:`COMMITTED_LABEL`, the commit marker its
create writes last (ADR-0005 obligation 4, by marker per N20). A partial item is
in no bucket whatever its Status or closure. Any other open issue that is off
the project, or has an empty Status, is unmapped: :meth:`GhApiGitHub.item_state`
raises, so a committed increment taken off the project is loud, never requeued.

:meth:`GhApiGitHub.create_increment` is k = 5 + len(predecessors) ordered
writes (:func:`create_step_count`): create the issue with title, body and the
Origin marker; link the sub-issue parent; one "blocked by" link per
predecessor; add the issue to the project; set a queued Status; add
:data:`COMMITTED_LABEL`, the commit. :meth:`GhApiGitHub.items_with_origin`
lists every issue of the repository, not a search: the search index lags, and
a just-created partial item it missed would make the retry create a duplicate.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
import re
import subprocess
from typing import Final, cast
from urllib.parse import quote

from squadra.board import BoardValidationError, StateWriteRefusedError, render_github_markdown
from squadra.config import ClaimScope, GitHubBoardConfig, item_in_claim_scope
from squadra.domain import (
    CommentEvent,
    IncrementRequest,
    Lifecycle,
    OriginRecord,
    Tags,
    WorkItem,
    WorkItemLinks,
)

CLOSED_PREFIX: Final[str] = "closed:"
CLOSED_COMPLETED: Final[str] = "closed:completed"
CLOSED_NOT_PLANNED: Final[str] = "closed:not_planned"
CLOSED_DUPLICATE: Final[str] = "closed:duplicate"
CLOSED_NO_REASON: Final[str] = "closed:"
# Every closed name an issue can have; each must be mapped in [board.states].
CLOSED_NAMES: Final[tuple[str, ...]] = (
    CLOSED_COMPLETED,
    CLOSED_NOT_PLANNED,
    CLOSED_DUPLICATE,
    CLOSED_NO_REASON,
)
# GitHub's ``stateReason`` spelling → squadra's closed name, translated here at
# the boundary. ``REOPENED`` is absent on purpose: it is no closed reason.
_CLOSED_NAME_BY_REASON: Final[Mapping[str | None, str]] = {
    "COMPLETED": CLOSED_COMPLETED,
    "NOT_PLANNED": CLOSED_NOT_PLANNED,
    "DUPLICATE": CLOSED_DUPLICATE,
    None: CLOSED_NO_REASON,
}
# The buckets ``set_state`` reaches by closing the issue, and the reason it closes with.
_CLOSE_INTO: Final[Mapping[Lifecycle, tuple[str, str]]] = {
    Lifecycle.DONE: (CLOSED_COMPLETED, "COMPLETED"),
    Lifecycle.WITHDRAWN: (CLOSED_NOT_PLANNED, "NOT_PLANNED"),
}

# The commit marker: the label a create adds last. Outside any fleet tag
# namespace (``validate_config`` checks), so finalize's clear-by-prefix never
# touches it, and never reported as a tag.
COMMITTED_LABEL: Final[str] = "squadra:committed"
STATUS_FIELD: Final[str] = "Status"

# The Origin rides in a hidden HTML comment at the end of the body: JSON, with
# ``-`` escaped so no Origin can close the comment early.
ORIGIN_MARKER_START: Final[str] = "\n<!-- squadra-origin: "
ORIGIN_MARKER_END: Final[str] = " -->"

_PAGE_SIZE: Final[int] = 100


def native_state(*, is_open: bool, state_reason: str | None, status: str | None) -> str | None:
    """Return an issue's native state. Closure wins over Status.

    A closed issue's native state is its closed name (``closed:<reason>``),
    whatever its project Status says, so a closed issue whose Status still
    names a queued column is never QUEUED. An open issue's native state is its
    Status option name, or ``None`` when it is off the project or its Status is
    empty. A closed reason outside the known set raises; it is never defaulted.
    """
    if not is_open:
        try:
            return _CLOSED_NAME_BY_REASON[state_reason]
        except KeyError:
            raise BoardValidationError(
                f"closed issue carries an unknown close reason {state_reason!r}; "
                f"squadra knows {sorted(r for r in _CLOSED_NAME_BY_REASON if r)}"
            ) from None
    return status or None


def with_origin_marker(body: str, origin: str) -> str:
    """Append ``origin`` to ``body`` as the hidden Origin marker."""
    encoded: str = json.dumps(origin).replace("-", "\\u002d")
    return f"{body}{ORIGIN_MARKER_START}{encoded}{ORIGIN_MARKER_END}"


def split_origin_marker(raw: str) -> tuple[str, str] | None:
    r"""Split an issue body into ``(body, origin)``, or ``None`` when it carries no marker.

    A marker that is present but does not decode to a string raises: an
    Origin-bearing issue misread as Origin-free would map from its Status, and a
    partial one would become claimable. GitHub's web editor saves a body with
    ``\r\n`` line ends, and may leave whitespace after the marker, so both are
    normalised away: an edited body still carries its marker, and still equals
    the ``\n`` body it was queued with (ledger N23).
    """
    text: str = raw.replace("\r\n", "\n").rstrip()
    at: int = text.rfind(ORIGIN_MARKER_START)
    if at < 0 or not text.endswith(ORIGIN_MARKER_END):
        return None
    encoded: str = text[at + len(ORIGIN_MARKER_START) : -len(ORIGIN_MARKER_END)]
    try:
        decoded: object = json.loads(encoded)
    except json.JSONDecodeError:
        decoded = None
    if not isinstance(decoded, str):
        raise BoardValidationError(f"malformed squadra Origin marker {encoded!r} in an issue body")
    return text[:at], decoded


def create_step_count(request: IncrementRequest) -> int:
    """How many writes a fresh :meth:`GhApiGitHub.create_increment` makes (k).

    Create the issue, link the parent, one "blocked by" link per predecessor,
    add it to the project, set its Status, add the commit marker.
    """
    return 5 + len(request.predecessors)


# A GitHub remote URL: ``https://host/owner/name(.git)``, ``ssh://git@host/owner/name(.git)``
# or scp-like ``git@host:owner/name(.git)``. The host is not compared: the
# adapter reaches whichever host ``gh`` is logged into.
_REMOTE_PATH: Final[re.Pattern[str]] = re.compile(
    r"^(?:[a-z+]+://(?:[^@/]+@)?[^/]+/|[^@:/]+@[^:/]+:)([^/]+)/([^/]+?)(?:\.git)?/?$"
)


def github_repository_of(url: str) -> str | None:
    """Return the ``owner/name`` a GitHub remote URL points at, lowercased, or ``None``.

    Lowercased because GitHub owner and repository names are case-insensitive.
    """
    match: re.Match[str] | None = _REMOTE_PATH.match(url.strip())
    if match is None:
        return None
    return f"{match.group(1)}/{match.group(2)}".lower()


def _run_gh(args: Sequence[str]) -> str:
    """Run a gh command and return stdout (raises on a non-zero exit)."""
    result: subprocess.CompletedProcess[str] = subprocess.run(
        ["gh", *args], capture_output=True, text=True, check=True
    )
    return result.stdout


@dataclass(frozen=True, slots=True)
class _Project:
    """The project's ids and its Status options (name → option id)."""

    project_id: str
    status_field_id: str
    options: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class _Issue:
    """What the adapter reads of one issue to place it in a bucket."""

    node_id: str
    number: int
    title: str
    is_open: bool
    state_reason: str | None
    body: str
    labels: tuple[str, ...]
    project_item_id: str | None  # None when off the project (archived counts as off)
    status: str | None  # the Status option name on this project, if set

    @property
    def partial(self) -> bool:
        """An Origin-bearing issue whose create has not written the commit marker."""
        return split_origin_marker(self.body) is not None and COMMITTED_LABEL not in self.labels

    @property
    def native(self) -> str | None:
        """The issue's native state (:func:`native_state`)."""
        return native_state(
            is_open=self.is_open, state_reason=self.state_reason, status=self.status
        )

    def work_item(self) -> WorkItem:
        """Return the neutral record, with the adapter's own commit marker left out of the tags."""
        tags: tuple[str, ...] = tuple(label for label in self.labels if label != COMMITTED_LABEL)
        return WorkItem(item_id=self.number, title=self.title, tags=tags)


_ISSUE_FIELDS: Final[str] = (
    "id number title state stateReason body labels(first: 100) { nodes { name } }"
)

_PROJECT_QUERY: Final[str] = (
    "query SquadraProject($owner: String!, $number: Int!) {"
    " repositoryOwner(login: $owner) { ... on ProjectV2Owner { projectV2(number: $number) {"
    f' id field(name: "{STATUS_FIELD}") {{ ... on ProjectV2SingleSelectField'
    " { id options { id name } } } } } } }"
)

_PROJECT_ITEMS_QUERY: Final[str] = (
    "query SquadraProjectItems($owner: String!, $number: Int!, $first: Int!, $after: String) {"
    " repositoryOwner(login: $owner) { ... on ProjectV2Owner { projectV2(number: $number) {"
    " items(first: $first, after: $after) { pageInfo { hasNextPage endCursor }"
    f' nodes {{ id isArchived status: fieldValueByName(name: "{STATUS_FIELD}")'
    " { ... on ProjectV2ItemFieldSingleSelectValue { name } }"
    f" content {{ __typename ... on Issue {{ {_ISSUE_FIELDS}"
    " repository { nameWithOwner } } } } } } } } }"
)

# One issue with its project items and links: what places it in a bucket and
# what an OriginRecord reads.
_ISSUE_DETAIL: Final[str] = (
    f"{_ISSUE_FIELDS}"
    " projectItems(first: 100, includeArchived: false) { nodes { id project { id }"
    f' status: fieldValueByName(name: "{STATUS_FIELD}")'
    " { ... on ProjectV2ItemFieldSingleSelectValue { name } } } }"
    " parent { number repository { nameWithOwner } }"
    " blockedBy(first: 100) { pageInfo { hasNextPage }"
    " nodes { number repository { nameWithOwner } } }"
)

_ISSUE_QUERY: Final[str] = (
    "query SquadraIssue($owner: String!, $name: String!, $number: Int!) {"
    f" repository(owner: $owner, name: $name) {{ issue(number: $number) {{ {_ISSUE_DETAIL}"
    " } } }"
)

_REPOSITORY_ISSUES_QUERY: Final[str] = (
    "query SquadraRepositoryIssues($owner: String!, $name: String!, $first: Int!,"
    " $after: String) { repository(owner: $owner, name: $name) {"
    " issues(first: $first, after: $after, states: [OPEN, CLOSED],"
    " orderBy: {field: CREATED_AT, direction: ASC}) { pageInfo { hasNextPage endCursor }"
    f" nodes {{ {_ISSUE_DETAIL} }} }} }} }}"
)

_ADD_SUB_ISSUE_MUTATION: Final[str] = (
    "mutation SquadraAddSubIssue($parent: ID!, $child: ID!) {"
    " addSubIssue(input: {issueId: $parent, subIssueId: $child}) { subIssue { number } } }"
)

_ADD_BLOCKED_BY_MUTATION: Final[str] = (
    "mutation SquadraAddBlockedBy($issue: ID!, $blocking: ID!) {"
    " addBlockedBy(input: {issueId: $issue, blockingIssueId: $blocking})"
    " { issue { number } } }"
)

_ADD_TO_PROJECT_MUTATION: Final[str] = (
    "mutation SquadraAddToProject($project: ID!, $content: ID!) {"
    " addProjectV2ItemById(input: {projectId: $project, contentId: $content})"
    " { item { id } } }"
)

_CLOSE_MUTATION: Final[str] = (
    "mutation SquadraCloseIssue($id: ID!, $reason: IssueClosedStateReason!) {"
    " closeIssue(input: {issueId: $id, stateReason: $reason}) { issue { number } } }"
)

_SET_STATUS_MUTATION: Final[str] = (
    "mutation SquadraSetStatus($project: ID!, $item: ID!, $field: ID!, $option: String!) {"
    " updateProjectV2ItemFieldValue(input: {projectId: $project, itemId: $item,"
    " fieldId: $field, value: {singleSelectOptionId: $option}}) { projectV2Item { id } } }"
)


class GhApiGitHub:
    """``BoardAccess`` over one repository's issues and one Projects v2 board, via ``gh``."""

    def __init__(  # noqa: PLR0913 - Constructor is a DI seam
        self,
        board: GitHubBoardConfig,
        *,
        run: Callable[[Sequence[str]], str] = _run_gh,
        states: Mapping[Lifecycle, tuple[str, ...]],
        base_branch: str = "main",
        tags: Tags = Tags(),
        claim_scope: ClaimScope,
        parent_scope_ids: tuple[int, ...],
        read_origin: Callable[[], str | None],
    ) -> None:
        """Wire the adapter to ``[board.github]``, its ``gh`` runner and the state map.

        ``run`` receives ``gh``'s arguments (without ``gh``) and returns stdout.
        The project's ids and Status options are resolved once, on first use.
        The project listing is memoized until this instance's next write, so a
        tick's gather of every bucket costs one listing (the supervisor builds
        its seams once per tick); a write by anyone else is not seen until then.
        ``claim_scope`` and ``parent_scope_ids`` are ``[board]``'s, with no
        default (WSQ1). ``read_origin`` returns ``FLEET_HOME``'s ``origin`` URL
        (``repo.target_remote_url``), which :meth:`validate_config` checks is
        the board's repository; no default, so the check cannot be skipped.
        """
        self._board = board
        self._run = run
        self._states = states
        self._base_branch = base_branch
        self._tags = tags
        self._claim_scope = claim_scope
        self._parent_scope_ids = parent_scope_ids
        self._read_origin = read_origin
        self._project_cache: _Project | None = None
        self._listing: list[_Issue] | None = None

    # --- reads ---------------------------------------------------------------

    def items_in_state(self, state: Lifecycle) -> tuple[WorkItem, ...]:
        """Return the project's issues (in this repository) whose bucket is ``state``.

        Partial items and issues whose bucket :meth:`item_state` would refuse
        (unmapped, open with a done Status, an unknown close reason) are in no
        bucket, so no listing returns them.
        """
        if not self._states.get(state):
            return ()
        return tuple(
            issue.work_item()
            for issue in self._project_issues()
            if self._bucket_or_none(issue) is state
        )

    def item_state(self, item_id: int) -> Lifecycle:
        """Return the issue's bucket; raise ``BoardValidationError`` when it has none."""
        return self._bucket_of(self._issue(self._fetch_issue(item_id)))

    def item_links(self, item_id: int) -> WorkItemLinks:
        """Read the sub-issue parent and the "blocked by" issues (the predecessors).

        A link into another repository raises: an issue number names an item
        only within this board's repository, and dropping a predecessor would
        make its successor claimable early.
        """
        return self._links_of(self._fetch_issue(item_id), item_id)

    def in_claim_scope(self, item_id: int) -> bool:
        """Whether the claim scope covers the item, by its parent link (read only under parents)."""
        return item_in_claim_scope(
            lambda: self.item_links(item_id).parent_id, self._claim_scope, self._parent_scope_ids
        )

    def completed_pr_url(self, branch: str) -> str | None:
        """Return the URL of a merged PR from ``branch`` into the base branch, if any."""
        out: str = self._run(
            [
                "api",
                "--method",
                "GET",
                f"repos/{self._board.repository}/pulls",
                "-f",
                "state=closed",
                "-f",
                f"head={self._board.repository_owner}:{branch}",
                "-f",
                f"base={self._base_branch}",
                "-f",
                f"per_page={_PAGE_SIZE}",
            ]
        )
        for entry in _list(json.loads(out) if out.strip() else []):
            pr: Mapping[str, object] = _obj(entry)
            url: object = pr.get("html_url")
            if isinstance(pr.get("merged_at"), str) and isinstance(url, str):
                return url
        return None

    def _validate_origin(self) -> None:
        """Fail closed unless ``origin`` is ``[board.github].repository``."""
        url: str | None = self._read_origin()
        origin: str | None = github_repository_of(url) if url is not None else None
        if origin is None or origin != self._board.repository.lower():
            # Only the parsed owner/name is shown: an HTTPS URL may carry a token.
            found: str = origin or ("no GitHub owner/name" if url else "no origin remote")
            raise BoardValidationError(
                f"[board.github].repository {self._board.repository!r} is not FLEET_HOME's "
                f"origin ({found}): a fleet is one repository, so the board's issues and "
                "the pushed branches must be the same repository"
            )

    def validate_config(self) -> None:
        """Check ``[board.states]`` against the project's Status options and the closed names.

        Both ways (N4): every configured name must exist (a Status option or a
        closed name) and every Status option and closed name must be mapped. On
        top, no Status option may look like a closed name; closure is never
        QUEUED or ACTIVE; ``closed:completed`` is DONE and ``closed:not_planned``
        WITHDRAWN, the two closes :meth:`set_state` makes; and the commit marker
        lies outside the fleet's tag namespace. First of all, the board's
        repository must be ``FLEET_HOME``'s ``origin`` (N34): branches are pushed
        to ``origin`` and :meth:`completed_pr_url` looks in the repository, so
        a mismatch never finalizes.
        """
        self._validate_origin()
        options: set[str] = set(self._project().options)
        clashing: list[str] = sorted(name for name in options if name.startswith(CLOSED_PREFIX))
        if clashing:
            raise BoardValidationError(
                f"project Status option(s) {clashing} start with {CLOSED_PREFIX!r}, which "
                "names a closed issue's native state; rename them on the project"
            )
        available: set[str] = options | set(CLOSED_NAMES)
        configured: set[str] = {name for names in self._states.values() for name in names}
        missing: list[str] = sorted(configured - available)
        if missing:
            raise BoardValidationError(
                f"configured board state(s) {missing} are neither a Status option of this "
                f"project {sorted(options)} nor a closed name {list(CLOSED_NAMES)}; "
                "fix squadra.toml [board.states]"
            )
        unmapped: list[str] = sorted(available - configured)
        if unmapped:
            raise BoardValidationError(
                f"board state(s) {unmapped} map to no lifecycle bucket; map each in "
                "squadra.toml [board.states] (queued/active/done/withdrawn)"
            )
        for bucket in (Lifecycle.QUEUED, Lifecycle.ACTIVE):
            closed: list[str] = [n for n in self._states.get(bucket, ()) if n in CLOSED_NAMES]
            if closed:
                raise BoardValidationError(
                    f"[board.states].{bucket.value} maps closed name(s) {closed}: a closed "
                    "issue is never queued or active; map them to done or withdrawn"
                )
        for bucket, (name, _reason) in _CLOSE_INTO.items():
            if name not in self._states.get(bucket, ()):
                raise BoardValidationError(
                    f"[board.states].{bucket.value} must map {name!r}: squadra closes an "
                    f"issue with that reason to move it to {bucket.value}"
                )
        if COMMITTED_LABEL.startswith(self._tags.prefix):
            raise BoardValidationError(
                f"tag_prefix {self._tags.prefix!r} covers squadra's commit marker label "
                f"{COMMITTED_LABEL!r}; choose a prefix that does not"
            )

    # --- the tick's writes -----------------------------------------------------

    def set_state(self, item_id: int, state: Lifecycle) -> None:
        """Move the issue into ``state`` with one write, or none.

        DONE closes the issue as completed and WITHDRAWN as not planned; QUEUED
        and ACTIVE write the bucket's first Status option. An issue already in
        ``state`` is left alone (finalize after a merged PR closed it). A closed
        issue is never reopened: a Status write onto one, or a close that would
        change its reason, raises ``StateWriteRefusedError``, which the tick
        reports per item. Any write onto a partial item, whose create commits
        it, raises ``BoardValidationError``.
        """
        self._listing = None
        issue: _Issue = self._issue(self._fetch_issue(item_id))
        if issue.partial:
            raise BoardValidationError(_partial_message(item_id))
        if self._bucket_or_none(issue) is state:
            return
        names: tuple[str, ...] = self._states.get(state, ())
        if not names:
            raise BoardValidationError(
                f"no native state maps to {state.value!r}; fix squadra.toml [board.states]"
            )
        if not issue.is_open:
            raise StateWriteRefusedError(
                f"issue #{item_id} is closed (reason {issue.state_reason}); moving it to {state.value} "
                "would reopen it or change its close reason, which squadra never does"
            )
        close: tuple[str, str] | None = _CLOSE_INTO.get(state)
        if close is not None:
            self._graphql(_CLOSE_MUTATION, id=issue.node_id, reason=close[1])
            return
        if issue.project_item_id is None:
            raise BoardValidationError(
                f"issue #{item_id} is not on the project, so it has no Status to set"
            )
        self._set_status(issue.project_item_id, state)

    def add_tag(self, item_id: int, tag: str) -> None:
        """Add ``tag`` as a label (GitHub's add is idempotent, and creates the label)."""
        self._listing = None
        self._add_label(item_id, tag)

    def remove_tag(self, item_id: int, tag: str) -> None:
        """Remove the ``tag`` label; a no-op when the issue does not carry it."""
        self._listing = None
        out: str = self._run(
            [
                "api",
                "--method",
                "GET",
                f"repos/{self._board.repository}/issues/{item_id}/labels",
                "-f",
                f"per_page={_PAGE_SIZE}",
            ]
        )
        names: set[object] = {_obj(label).get("name") for label in _list(json.loads(out or "[]"))}
        if tag not in names:
            return
        self._run(
            [
                "api",
                "--method",
                "DELETE",
                f"repos/{self._board.repository}/issues/{item_id}/labels/{quote(tag, safe='')}",
            ]
        )

    def add_comment(self, item_id: int, event: CommentEvent) -> None:
        """Render ``event`` to Markdown and add it as an issue comment."""
        self._listing = None
        self._run(
            [
                "api",
                "--method",
                "POST",
                f"repos/{self._board.repository}/issues/{item_id}/comments",
                "-f",
                f"body={render_github_markdown(event, self._tags)}",
            ]
        )

    # --- board writes -----------------------------------------------------------

    def create_increment(self, request: IncrementRequest, partial_item: int | None = None) -> int:
        """Land a QUEUED issue in k ordered writes (:func:`create_step_count`), or finish one.

        1. Create the issue with title and body, the Origin marker already in
           the body (obligation 1).
        2. Link the sub-issue parent.
        3. Add each "blocked by" link.
        4. Add the issue to the project.
        5. Set the first queued Status.
        6. Add :data:`COMMITTED_LABEL`: the commit (obligation 4). Until it
           lands the issue is partial, in no bucket whatever a workflow or a
           human does to its Status (obligations 2 and 3).

        With ``partial_item``, the issue must be an open partial item carrying
        ``request``'s Origin (a closed one would need a reopen); writes 2–5 are
        made only where missing, then the commit. Status is read back once after
        the commit (ADR-0006 decision 7): an "Item added to project" workflow
        that sets a non-queued Status raises here. Detection, not a guarantee.
        """
        self._listing = None
        links: WorkItemLinks
        if partial_item is None:
            issue: _Issue = self._create_issue(request)
            links = WorkItemLinks(parent_id=None, predecessor_ids=())
        else:
            raw: Mapping[str, object] = self._fetch_issue(partial_item)
            issue = self._issue(raw)
            marker: tuple[str, str] | None = split_origin_marker(issue.body)
            if not issue.partial or marker is None or marker[1] != request.origin:
                raise ValueError(
                    f"issue #{partial_item} is not a partial item carrying origin "
                    f"{request.origin!r}"
                )
            if not issue.is_open:
                raise BoardValidationError(
                    f"partial issue #{partial_item} is closed; finishing it would reopen it, "
                    "which squadra never does"
                )
            links = self._links_of(raw, partial_item)
        if links.parent_id is None:
            self._graphql(
                _ADD_SUB_ISSUE_MUTATION, parent=self._node_id(request.parent), child=issue.node_id
            )
        for predecessor in request.predecessors:
            if predecessor not in links.predecessor_ids:
                self._graphql(
                    _ADD_BLOCKED_BY_MUTATION,
                    issue=issue.node_id,
                    blocking=self._node_id(predecessor),
                )
        project_item: str = issue.project_item_id or self._add_to_project(issue.node_id)
        if issue.status not in self._states.get(Lifecycle.QUEUED, ()):
            self._set_status(project_item, Lifecycle.QUEUED)
        self._add_label(issue.number, COMMITTED_LABEL)
        try:
            landed: Lifecycle = self.item_state(issue.number)
        except BoardValidationError as exc:
            raise _not_queued(issue.number, str(exc)) from exc
        if landed is not Lifecycle.QUEUED:
            raise _not_queued(issue.number, f"it reads {landed.value}")
        return issue.number

    def items_with_origin(self) -> tuple[OriginRecord, ...]:
        r"""Return every Origin-bearing issue of the repository, on the project or not.

        Every bucket; partial issues with ``lifecycle`` ``None``. A committed
        issue whose bucket :meth:`item_state` would refuse raises rather than
        read as partial, so a retry never "finishes" (requeues) it. The body
        comes back without the marker and with ``\n`` line ends.
        """
        records: list[OriginRecord] = []
        for raw in self._repository_issues():
            issue: _Issue = self._issue(raw)
            marker: tuple[str, str] | None = split_origin_marker(issue.body)
            if marker is None:
                continue
            links: WorkItemLinks = self._links_of(raw, issue.number)
            records.append(
                OriginRecord(
                    item_id=issue.number,
                    origin=marker[1],
                    parent=links.parent_id,
                    predecessors=links.predecessor_ids,
                    title=issue.title,
                    body=marker[0],
                    lifecycle=None if issue.partial else self._bucket_of(issue),
                )
            )
        return tuple(records)

    def _create_issue(self, request: IncrementRequest) -> _Issue:
        """Write 1: the issue with its title and body, the Origin marker in the body."""
        out: str = self._run(
            [
                "api",
                "--method",
                "POST",
                f"repos/{self._board.repository}/issues",
                "-f",
                f"title={request.title}",
                "-f",
                f"body={with_origin_marker(request.body, request.origin)}",
            ]
        )
        created: Mapping[str, object] = _obj(json.loads(out or "{}"))
        number: object = created.get("number")
        node_id: object = created.get("node_id")
        if not isinstance(number, int) or not isinstance(node_id, str):
            raise RuntimeError(f"unexpected create-issue response from GitHub: {out!r}")
        return _Issue(
            node_id=node_id,
            number=number,
            title=request.title,
            is_open=True,
            state_reason=None,
            body=with_origin_marker(request.body, request.origin),
            labels=(),
            project_item_id=None,
            status=None,
        )

    def _add_to_project(self, node_id: str) -> str:
        """Add an issue to the project and return its item id (GitHub returns an existing one)."""
        data: Mapping[str, object] = self._graphql(
            _ADD_TO_PROJECT_MUTATION, project=self._project().project_id, content=node_id
        )
        item_id: object = _obj(_obj(data.get("addProjectV2ItemById")).get("item")).get("id")
        if not isinstance(item_id, str):
            raise RuntimeError(f"unexpected add-to-project response from GitHub: {dict(data)!r}")
        return item_id

    # --- bucket rules ------------------------------------------------------------

    def _bucket_of(self, issue: _Issue) -> Lifecycle:
        """Return the issue's bucket; raise for a partial, unmapped or contradictory issue."""
        if issue.partial:
            raise BoardValidationError(_partial_message(issue.number))
        native: str | None = issue.native
        if native is None:
            where: str = (
                "is not on the project"
                if issue.project_item_id is None
                else "has an empty Status on the project"
            )
            raise BoardValidationError(
                f"open issue #{issue.number} {where}, so it maps to no lifecycle bucket; "
                "put it on the project with a Status"
            )
        bucket: Lifecycle = self._lifecycle_of(native)
        if bucket is Lifecycle.DONE and issue.is_open:
            raise BoardValidationError(
                f"open issue #{issue.number} has Status {native!r}, mapped to done: a done "
                "Status is valid only on a closed issue (reopened, or dragged to it by hand?)"
            )
        return bucket

    def _bucket_or_none(self, issue: _Issue) -> Lifecycle | None:
        """:meth:`_bucket_of`, with ``None`` where it would raise (listings skip those)."""
        try:
            return self._bucket_of(issue)
        except BoardValidationError:
            return None

    def _lifecycle_of(self, native: str) -> Lifecycle:
        """Reverse-map a native state to its bucket; an unmapped state raises."""
        for lifecycle, names in self._states.items():
            if native in names:
                return lifecycle
        raise BoardValidationError(
            f"native state {native!r} maps to no lifecycle bucket; fix squadra.toml [board.states]"
        )

    # --- transport ---------------------------------------------------------------

    def _graphql(self, query: str, **variables: str | int | None) -> Mapping[str, object]:
        """Run one ``gh api graphql`` call and return its ``data``.

        Strings go as ``-f`` (raw), integers as ``-F`` (typed); ``None`` is left
        out, which GraphQL reads as null.
        """
        args: list[str] = ["api", "graphql", "-f", f"query={query}"]
        for key, value in variables.items():
            if isinstance(value, int):
                args += ["-F", f"{key}={value}"]
            elif value is not None:
                args += ["-f", f"{key}={value}"]
        payload: Mapping[str, object] = _obj(json.loads(self._run(args) or "{}"))
        if payload.get("errors"):
            raise RuntimeError(f"GitHub GraphQL error: {payload['errors']!r}")
        return _obj(payload.get("data"))

    def _set_status(self, project_item: str, state: Lifecycle) -> None:
        """Write the first Status option ``state`` maps onto a project item."""
        names: tuple[str, ...] = self._states.get(state, ())
        status: str = next((n for n in names if not n.startswith(CLOSED_PREFIX)), "")
        project: _Project = self._project()
        option: str | None = project.options.get(status)
        if option is None:
            raise BoardValidationError(
                f"{state.value} maps to no Status option of the project; "
                "fix squadra.toml [board.states]"
            )
        self._graphql(
            _SET_STATUS_MUTATION,
            project=project.project_id,
            item=project_item,
            field=project.status_field_id,
            option=option,
        )

    def _add_label(self, item_id: int, label: str) -> None:
        self._run(
            [
                "api",
                "--method",
                "POST",
                f"repos/{self._board.repository}/issues/{item_id}/labels",
                "-f",
                f"labels[]={label}",
            ]
        )

    def _node_id(self, item_id: int) -> str:
        """Return the GraphQL id of an issue of this repository (links are written by id)."""
        node_id: object = self._fetch_issue(item_id).get("id")
        if not isinstance(node_id, str):
            raise BoardValidationError(f"GitHub returned no id for issue #{item_id}")
        return node_id

    def _project(self) -> _Project:
        """Resolve the project's id, its Status field and options, once."""
        if self._project_cache is None:
            data: Mapping[str, object] = self._graphql(
                _PROJECT_QUERY,
                owner=self._board.project_owner,
                number=self._board.project_number,
            )
            project: Mapping[str, object] = _obj(_obj(data.get("repositoryOwner")).get("projectV2"))
            project_id: object = project.get("id")
            if not isinstance(project_id, str):
                raise BoardValidationError(
                    f"no project {self._board.project_number} under {self._board.project_owner!r}"
                    "; fix squadra.toml [board.github]"
                )
            field: Mapping[str, object] = _obj(project.get("field"))
            field_id: object = field.get("id")
            if not isinstance(field_id, str):
                raise BoardValidationError(
                    f"project {self._board.project_number} has no single-select "
                    f"{STATUS_FIELD!r} field"
                )
            options: dict[str, str] = {}
            for entry in _list(field.get("options")):
                option: Mapping[str, object] = _obj(entry)
                name: object = option.get("name")
                option_id: object = option.get("id")
                if isinstance(name, str) and isinstance(option_id, str):
                    options[name] = option_id
            self._project_cache = _Project(project_id, field_id, options)
        return self._project_cache

    def _project_issues(self) -> list[_Issue]:
        """Every unarchived project item that is an issue in this board's repository.

        Memoized until this instance's next write (see :meth:`__init__`).
        """
        if self._listing is None:
            self._listing = self._list_project_issues()
        return self._listing

    def _list_project_issues(self) -> list[_Issue]:
        """List the project's issues in this board's repository, page by page."""
        issues: list[_Issue] = []
        after: str | None = None
        while True:
            data: Mapping[str, object] = self._graphql(
                _PROJECT_ITEMS_QUERY,
                owner=self._board.project_owner,
                number=self._board.project_number,
                first=_PAGE_SIZE,
                after=after,
            )
            items: Mapping[str, object] = _obj(
                _obj(_obj(data.get("repositoryOwner")).get("projectV2")).get("items")
            )
            for entry in _list(items.get("nodes")):
                node: Mapping[str, object] = _obj(entry)
                content: Mapping[str, object] = _obj(node.get("content"))
                if node.get("isArchived") is True or content.get("__typename") != "Issue":
                    continue
                if not self._in_repository(_obj(content.get("repository"))):
                    continue
                item_id: object = node.get("id")
                issues.append(
                    _issue_from(
                        content,
                        item_id if isinstance(item_id, str) else None,
                        _status_name(node.get("status")),
                    )
                )
            page: Mapping[str, object] = _obj(items.get("pageInfo"))
            cursor: object = page.get("endCursor")
            if page.get("hasNextPage") is not True or not isinstance(cursor, str):
                return issues
            after = cursor

    def _repository_issues(self) -> list[Mapping[str, object]]:
        """Every issue of this board's repository, open and closed, with its detail."""
        issues: list[Mapping[str, object]] = []
        after: str | None = None
        while True:
            data: Mapping[str, object] = self._graphql(
                _REPOSITORY_ISSUES_QUERY,
                owner=self._board.repository_owner,
                name=self._board.repository_name,
                first=_PAGE_SIZE,
                after=after,
            )
            page_of: Mapping[str, object] = _obj(_obj(data.get("repository")).get("issues"))
            issues.extend(_obj(node) for node in _list(page_of.get("nodes")))
            page: Mapping[str, object] = _obj(page_of.get("pageInfo"))
            cursor: object = page.get("endCursor")
            if page.get("hasNextPage") is not True or not isinstance(cursor, str):
                return issues
            after = cursor

    def _fetch_issue(self, item_id: int) -> Mapping[str, object]:
        """Fetch one issue of this repository with its project items and links."""
        data: Mapping[str, object] = self._graphql(
            _ISSUE_QUERY,
            owner=self._board.repository_owner,
            name=self._board.repository_name,
            number=item_id,
        )
        issue: Mapping[str, object] = _obj(_obj(data.get("repository")).get("issue"))
        if not issue:
            raise BoardValidationError(f"no issue #{item_id} in {self._board.repository}")
        return issue

    def _issue(self, raw: Mapping[str, object]) -> _Issue:
        """Place a fetched issue on this board's project (or off it)."""
        project_id: str = self._project().project_id
        for entry in _list(_obj(raw.get("projectItems")).get("nodes")):
            node: Mapping[str, object] = _obj(entry)
            if _obj(node.get("project")).get("id") == project_id:
                item_id: object = node.get("id")
                return _issue_from(
                    raw,
                    item_id if isinstance(item_id, str) else None,
                    _status_name(node.get("status")),
                )
        return _issue_from(raw, None, None)

    def _in_repository(self, repository: Mapping[str, object]) -> bool:
        """Whether a ``repository { nameWithOwner }`` is this board's repository."""
        name: object = repository.get("nameWithOwner")
        return isinstance(name, str) and name.casefold() == self._board.repository.casefold()

    def _links_of(self, raw: Mapping[str, object], item_id: int) -> WorkItemLinks:
        """Read a fetched issue's sub-issue parent and "blocked by" issues."""
        parent: int | None = None
        if isinstance(raw.get("parent"), dict):
            parent = self._local_number(_obj(raw.get("parent")), item_id, "parent")
        blocked_by: Mapping[str, object] = _obj(raw.get("blockedBy"))
        if _obj(blocked_by.get("pageInfo")).get("hasNextPage") is True:
            raise BoardValidationError(
                f"issue #{item_id} is blocked by more than {_PAGE_SIZE} issues; squadra "
                "reads at most that many predecessors"
            )
        predecessors: tuple[int, ...] = tuple(
            self._local_number(_obj(node), item_id, "blocked-by")
            for node in _list(blocked_by.get("nodes"))
        )
        return WorkItemLinks(parent_id=parent, predecessor_ids=predecessors)

    def _local_number(self, linked: Mapping[str, object], item_id: int, kind: str) -> int:
        """Return the number of a linked issue, which must be in this board's repository."""
        number: object = linked.get("number")
        repository: Mapping[str, object] = _obj(linked.get("repository"))
        if not isinstance(number, int) or not self._in_repository(repository):
            raise BoardValidationError(
                f"issue #{item_id} has a {kind} link to {repository.get('nameWithOwner')!r}"
                f"#{number}, outside {self._board.repository}; squadra reads links within "
                "the board's repository only"
            )
        return number


# --- parsing helpers ---------------------------------------------------------------


def _partial_message(item_id: int) -> str:
    return (
        f"issue #{item_id} is partial: it carries an Origin but not the commit marker "
        f"{COMMITTED_LABEL!r}, so its create has not committed it and it is in no bucket"
    )


def _not_queued(item_id: int, why: str) -> BoardValidationError:
    return BoardValidationError(
        f'issue #{item_id} is committed but did not land queued ({why}); an "Item added to '
        'project" workflow or a human set its Status. Turn that workflow off, or make it '
        "set a queued Status (ADR-0006 decision 7), then fix the issue's Status by hand"
    )


def _issue_from(raw: Mapping[str, object], item_id: str | None, status: str | None) -> _Issue:
    """Build an :class:`_Issue` from a GraphQL issue object and its project item."""
    number: object = raw.get("number")
    node_id: object = raw.get("id")
    title: object = raw.get("title")
    body: object = raw.get("body")
    reason: object = raw.get("stateReason")
    labels: tuple[str, ...] = tuple(
        name
        for name in (
            _obj(label).get("name") for label in _list(_obj(raw.get("labels")).get("nodes"))
        )
        if isinstance(name, str)
    )
    if not isinstance(number, int) or not isinstance(node_id, str):
        raise BoardValidationError(f"unexpected issue payload from GitHub: {dict(raw)!r}")
    return _Issue(
        node_id=node_id,
        number=number,
        title=title if isinstance(title, str) else "",
        is_open=raw.get("state") == "OPEN",
        state_reason=reason if isinstance(reason, str) else None,
        body=body if isinstance(body, str) else "",
        labels=labels,
        project_item_id=item_id,
        status=status,
    )


def _status_name(value: object) -> str | None:
    """Return the option name of a ``fieldValueByName`` single-select value, if set."""
    name: object = _obj(value).get("name")
    return name if isinstance(name, str) and name else None


def _obj(value: object) -> Mapping[str, object]:
    """``value`` as a JSON object, or empty when it is not one."""
    return cast("dict[str, object]", value) if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    """``value`` as a JSON array, or empty when it is not one."""
    return cast("list[object]", value) if isinstance(value, list) else []
