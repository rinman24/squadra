"""An in-memory GitHub that answers the GitHub adapter's ``gh api`` calls.

:class:`InMemoryGitHub` is the stubbed transport the GitHub adapter
(:class:`squadra.github_board.GhApiGitHub`) runs over offline: it parses the
argv the adapter hands ``gh`` (REST paths and the named GraphQL operations) and
answers with GitHub-shaped JSON from one repository and one project held in
memory. It knows only the calls the adapter makes; anything else fails loudly.
Project items and repository issues come back in pages of ``page_size`` so
pagination is exercised. ``arm_fault(j)`` makes the stub raise
:class:`~squadra.fake_board.InjectedCrashError` right after its j-th next write
lands, the way a crash mid-create leaves GitHub.

:class:`StubbedGitHubBoard` is the adapter over such a stub, the fourth shape of
the ``board`` contract fixture, with the stub's per-issue comment log exposed as
``comments`` the way the fakes expose theirs, ``state_writes`` the way
:class:`~tests.helpers.board_fakes.RecordingFileBoard` records them, and the
fakes' seeding surface (``seed_origin``, ``seed_partial``).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import json
import re
import subprocess
from urllib.parse import unquote

from squadra.config import ClaimScope, GitHubBoardConfig
from squadra.domain import Lifecycle, Tags
from squadra.fake_board import InjectedCrashError
from squadra.github_board import (
    CLOSED_COMPLETED,
    CLOSED_DUPLICATE,
    CLOSED_NO_REASON,
    CLOSED_NOT_PLANNED,
    COMMITTED_LABEL,
    GhApiGitHub,
    with_origin_marker,
)

REPOSITORY: str = "acme/widgets"
PROJECT_OWNER: str = "acme"
PROJECT_NUMBER: int = 7
GITHUB_CONFIG: GitHubBoardConfig = GitHubBoardConfig(
    repository=REPOSITORY, project_owner=PROJECT_OWNER, project_number=PROJECT_NUMBER
)
STATUS_OPTIONS: tuple[str, ...] = ("Todo", "Triage", "In Progress", "Done")
# The map the adapter runs under in the tests: many names per bucket on purpose,
# and every closed name mapped (closure: done or withdrawn, never queued/active).
GH_STATES: Mapping[Lifecycle, tuple[str, ...]] = {
    Lifecycle.QUEUED: ("Todo", "Triage"),
    Lifecycle.ACTIVE: ("In Progress",),
    Lifecycle.DONE: ("Done", CLOSED_COMPLETED),
    Lifecycle.WITHDRAWN: (CLOSED_NOT_PLANNED, CLOSED_DUPLICATE, CLOSED_NO_REASON),
}

_OPERATION: re.Pattern[str] = re.compile(r"\b(?:query|mutation)\s+(\w+)")


@dataclass
class StubIssue:
    """One issue in the stub repository, with its item on the stub project."""

    number: int
    title: str
    is_open: bool = True
    state_reason: str | None = None  # GitHub's spelling: COMPLETED, NOT_PLANNED, …
    status: str | None = None  # Status option name on the project
    on_project: bool = True
    archived: bool = False
    labels: list[str] = field(default_factory=list[str])
    body: str = ""
    parent: tuple[str, int] | None = None  # (nameWithOwner, number)
    blocked_by: list[tuple[str, int]] = field(default_factory=list[tuple[str, int]])
    comments: list[str] = field(default_factory=list[str])
    repository: str = REPOSITORY


@dataclass(frozen=True)
class StubPull:
    """One pull request: ``head`` is ``owner:branch``."""

    head: str
    base: str
    url: str
    merged: bool


class InMemoryGitHub:
    """A callable ``gh`` runner over one in-memory repository and project."""

    def __init__(
        self,
        *,
        status_options: Sequence[str] = STATUS_OPTIONS,
        page_size: int = 100,
    ) -> None:
        """Hold an empty repository and a project with ``status_options``."""
        self.status_options: list[str] = list(status_options)
        self.page_size = page_size
        self.issues: dict[int, StubIssue] = {}
        self.pulls: list[StubPull] = []
        self.writes: list[tuple[str, int]] = []  # (operation, issue number), in order
        self.calls: list[list[str]] = []
        self._fault_at: int | None = None  # crash once len(writes) reaches this

    def arm_fault(self, after_writes: int) -> None:
        """Raise ``InjectedCrashError`` right after the ``after_writes``-th next write lands."""
        self._fault_at = len(self.writes) + after_writes

    def add(self, issue: StubIssue) -> StubIssue:
        """Seed one issue."""
        self.issues[issue.number] = issue
        return issue

    # --- the gh entry point -------------------------------------------------------

    def __call__(self, args: Sequence[str]) -> str:
        """Answer one ``gh`` invocation (``args`` without ``gh``)."""
        self.calls.append(list(args))
        answer: str = self._answer(args)
        if self._fault_at is not None and len(self.writes) >= self._fault_at:
            self._fault_at = None
            raise InjectedCrashError(f"stub GitHub: injected crash after {self.writes[-1]}")
        return answer

    def _answer(self, args: Sequence[str]) -> str:
        if list(args[:2]) == ["api", "graphql"]:
            fields: dict[str, str] = _fields(args[2:])
            match_: re.Match[str] | None = _OPERATION.search(fields["query"])
            assert match_ is not None, f"unnamed GraphQL operation: {fields['query']!r}"
            return json.dumps({"data": self._graphql(match_.group(1), fields)})
        assert args[0] == "api" and args[1] == "--method", f"unexpected gh call {args!r}"
        return json.dumps(self._rest(args[2], args[3], _fields(args[4:])))

    # --- GraphQL --------------------------------------------------------------------

    def _graphql(self, operation: str, v: dict[str, str]) -> object:
        if operation == "SquadraProject":
            return self._owner(v, self._project())
        if operation == "SquadraProjectItems":
            return self._owner(v, {"items": self._items_page(v.get("after"))})
        if operation in ("SquadraIssue", "SquadraRepositoryIssues"):
            if f"{v['owner']}/{v['name']}" != REPOSITORY:
                return {"repository": None}
            if operation == "SquadraIssue":
                return {"repository": {"issue": self._issue_json(self._get(int(v["number"])))}}
            return {"repository": {"issues": self._issues_page(v.get("after"))}}
        return self._graphql_write(operation, v)

    def _graphql_write(self, operation: str, v: dict[str, str]) -> object:
        if operation == "SquadraAddSubIssue":
            parent: StubIssue = self._by_node_id(v["parent"])
            child: StubIssue = self._by_node_id(v["child"])
            if child.parent is not None:  # GitHub refuses without replaceParent
                raise subprocess.CalledProcessError(1, ["gh"], "Issue may not contain duplicate")
            child.parent = (parent.repository, parent.number)
            self.writes.append(("parent", child.number))
            return {"addSubIssue": {"subIssue": {"number": child.number}}}
        if operation == "SquadraAddBlockedBy":
            blocked: StubIssue = self._by_node_id(v["issue"])
            blocking: StubIssue = self._by_node_id(v["blocking"])
            link: tuple[str, int] = (blocking.repository, blocking.number)
            if link not in blocked.blocked_by:
                blocked.blocked_by.append(link)
            self.writes.append(("blocked_by", blocked.number))
            return {"addBlockedBy": {"issue": {"number": blocked.number}}}
        if operation == "SquadraAddToProject":
            assert v["project"] == "PVT_stub"
            added: StubIssue = self._by_node_id(v["content"])
            added.on_project, added.archived = True, False  # GitHub returns an existing item
            self.writes.append(("project", added.number))
            return {"addProjectV2ItemById": {"item": {"id": _item_id(added.number)}}}
        if operation == "SquadraCloseIssue":
            issue: StubIssue = self._by_node_id(v["id"])
            issue.is_open = False
            issue.state_reason = v["reason"]
            self.writes.append(("close", issue.number))
            return {"closeIssue": {"issue": {"number": issue.number}}}
        if operation == "SquadraSetStatus":
            issue = self._by_item_id(v["item"])
            assert v["project"] == "PVT_stub" and v["field"] == "PVTSSF_status"
            issue.status = self.status_options[int(v["option"].removeprefix("opt-"))]
            self.writes.append(("status", issue.number))
            return {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": v["item"]}}}
        raise AssertionError(f"the stub does not answer GraphQL operation {operation!r}")

    def _owner(self, v: dict[str, str], project: dict[str, object]) -> object:
        if v["owner"] != PROJECT_OWNER or int(v["number"]) != PROJECT_NUMBER:
            return {"repositoryOwner": {"projectV2": None}}
        return {"repositoryOwner": {"projectV2": project}}

    def _project(self) -> dict[str, object]:
        options: list[dict[str, str]] = [
            {"id": f"opt-{index}", "name": name} for index, name in enumerate(self.status_options)
        ]
        return {"id": "PVT_stub", "field": {"id": "PVTSSF_status", "options": options}}

    def _items_page(self, after: str | None) -> dict[str, object]:
        on_project: list[StubIssue] = [i for i in self.issues.values() if i.on_project]
        start: int = int(after) if after else 0
        page: list[StubIssue] = on_project[start : start + self.page_size]
        end: int = start + len(page)
        return {
            "pageInfo": {"hasNextPage": end < len(on_project), "endCursor": str(end)},
            "nodes": [
                {
                    "id": _item_id(issue.number),
                    "isArchived": issue.archived,
                    "status": _status_json(issue.status),
                    "content": {
                        "__typename": "Issue",
                        **self._issue_fields(issue),
                        "repository": {"nameWithOwner": issue.repository},
                    },
                }
                for issue in page
            ],
        }

    def _issues_page(self, after: str | None) -> dict[str, object]:
        issues: list[StubIssue] = [i for i in self.issues.values() if i.repository == REPOSITORY]
        start: int = int(after) if after else 0
        page: list[StubIssue] = issues[start : start + self.page_size]
        end: int = start + len(page)
        return {
            "pageInfo": {"hasNextPage": end < len(issues), "endCursor": str(end)},
            "nodes": [self._issue_json(issue) for issue in page],
        }

    def _issue_fields(self, issue: StubIssue) -> dict[str, object]:
        return {
            "id": _node_id(issue.number),
            "number": issue.number,
            "title": issue.title,
            "state": "OPEN" if issue.is_open else "CLOSED",
            "stateReason": issue.state_reason,
            "body": issue.body,
            "labels": {"nodes": [{"name": label} for label in issue.labels]},
        }

    def _issue_json(self, issue: StubIssue) -> dict[str, object]:
        items: list[dict[str, object]] = (
            [
                {
                    "id": _item_id(issue.number),
                    "project": {"id": "PVT_stub"},
                    "status": _status_json(issue.status),
                }
            ]
            if issue.on_project and not issue.archived
            else []
        )
        return {
            **self._issue_fields(issue),
            "projectItems": {"nodes": items},
            "parent": _link_json(issue.parent) if issue.parent else None,
            "blockedBy": {
                "pageInfo": {"hasNextPage": False},
                "nodes": [_link_json(link) for link in issue.blocked_by],
            },
        }

    # --- REST -------------------------------------------------------------------------

    def _rest(self, method: str, path: str, v: dict[str, str]) -> object:
        prefix: str = f"repos/{REPOSITORY}/"
        assert path.startswith(prefix), f"unexpected REST path {path!r}"
        parts: list[str] = path.removeprefix(prefix).split("/")
        if parts == ["issues"] and method == "POST":
            number: int = max(self.issues, default=0) + 1
            self.add(StubIssue(number, v["title"], body=v["body"], on_project=False))
            self.writes.append(("create", number))
            return {"number": number, "node_id": _node_id(number), "id": 900000 + number}
        if parts == ["pulls"] and method == "GET":
            return [
                {
                    "html_url": pull.url,
                    "merged_at": "2026-10-09T00:00:00Z" if pull.merged else None,
                }
                for pull in self.pulls
                if pull.head == v["head"] and pull.base == v["base"] and v["state"] == "closed"
            ]
        issue: StubIssue = self._get(int(parts[1]))
        if parts[2] == "labels" and method == "GET":
            return [{"name": label} for label in issue.labels]
        if parts[2] == "labels" and method == "POST":
            for label in [value for key, value in v.items() if key == "labels[]"]:
                if label not in issue.labels:
                    issue.labels.append(label)
            self.writes.append(("label", issue.number))
            return [{"name": label} for label in issue.labels]
        if parts[2] == "labels" and method == "DELETE":
            label = unquote(parts[3])
            if label not in issue.labels:
                raise subprocess.CalledProcessError(1, ["gh", "api", path], "Label does not exist")
            issue.labels.remove(label)
            self.writes.append(("unlabel", issue.number))
            return [{"name": name} for name in issue.labels]
        if parts[2] == "comments" and method == "POST":
            issue.comments.append(v["body"])
            self.writes.append(("comment", issue.number))
            return {"body": v["body"]}
        raise AssertionError(f"the stub does not answer {method} {path}")

    # --- lookups ----------------------------------------------------------------------

    def _get(self, number: int) -> StubIssue:
        try:
            return self.issues[number]
        except KeyError:
            raise subprocess.CalledProcessError(
                1, ["gh", "api"], f"Could not resolve to an Issue with the number of {number}."
            ) from None

    def _by_node_id(self, node_id: str) -> StubIssue:
        return self._get(int(node_id.removeprefix("I_")))

    def _by_item_id(self, item_id: str) -> StubIssue:
        return self._get(int(item_id.removeprefix("PVTI_")))


def _fields(args: Sequence[str]) -> dict[str, str]:
    """Parse ``-f key=value`` / ``-F key=value`` pairs (``labels[]`` keeps the last)."""
    fields: dict[str, str] = {}
    for flag, pair in zip(args[::2], args[1::2], strict=True):
        assert flag in ("-f", "-F"), f"unexpected gh flag {flag!r}"
        key, _, value = pair.partition("=")
        fields[key] = value
    return fields


def _node_id(number: int) -> str:
    return f"I_{number}"


def _item_id(number: int) -> str:
    return f"PVTI_{number}"


def _status_json(status: str | None) -> dict[str, str] | None:
    return {"name": status} if status else None


def _link_json(link: tuple[str, int]) -> dict[str, object]:
    return {"number": link[1], "repository": {"nameWithOwner": link[0]}}


class StubbedGitHubBoard(GhApiGitHub):
    """The GitHub adapter over an :class:`InMemoryGitHub`, for the contract suites."""

    def __init__(
        self,
        github: InMemoryGitHub | None = None,
        *,
        states: Mapping[Lifecycle, tuple[str, ...]] = GH_STATES,
        tags: Tags = Tags(),
        claim_scope: ClaimScope = ClaimScope.WHOLE_BOARD,
        parent_scope_ids: tuple[int, ...] = (),
    ) -> None:
        """Wire the adapter to ``github`` (a fresh, empty stub when omitted)."""
        self.github: InMemoryGitHub = github if github is not None else InMemoryGitHub()
        super().__init__(
            GITHUB_CONFIG,
            run=self.github,
            states=states,
            tags=tags,
            claim_scope=claim_scope,
            parent_scope_ids=parent_scope_ids,
        )
        self.state_writes: list[tuple[int, Lifecycle]] = []

    def set_state(self, item_id: int, state: Lifecycle) -> None:
        """Write the state and record the write."""
        super().set_state(item_id, state)
        self.state_writes.append((item_id, state))

    def arm_create_fault(self, after_step: int) -> None:
        """Make the next create stop after its ``after_step``-th write (a stub-wide fault)."""
        self.github.arm_fault(after_step)

    def seed_origin(self, item_id: int, origin: str) -> None:
        """Give a seeded issue an Origin by hand, committed (e.g. to inject a duplicate)."""
        issue: StubIssue = self.github.issues[item_id]
        issue.body = with_origin_marker(issue.body, origin)
        issue.labels.append(COMMITTED_LABEL)

    def seed_partial(
        self,
        origin: str,
        title: str,
        body: str,
        *,
        parent_id: int | None = None,
        predecessor_ids: tuple[int, ...] = (),
    ) -> int:
        """Place an issue a crash mid-create left behind: Origin set, off the project, no marker."""
        number: int = max(self.github.issues, default=0) + 1
        self.github.add(
            StubIssue(
                number,
                title,
                body=with_origin_marker(body, origin),
                on_project=False,
                parent=(REPOSITORY, parent_id) if parent_id is not None else None,
                blocked_by=[(REPOSITORY, link) for link in predecessor_ids],
            )
        )
        return number

    @property
    def comments(self) -> dict[int, list[str]]:
        """Every issue's comments, by number (issues without any are left out)."""
        return {n: issue.comments for n, issue in self.github.issues.items() if issue.comments}
