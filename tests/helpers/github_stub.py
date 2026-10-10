"""An in-memory GitHub that answers the GitHub adapter's ``gh api`` calls.

:class:`InMemoryGitHub` is the stubbed transport the GitHub adapter
(:class:`squadra.github_board.GhApiGitHub`) runs over offline: it parses the
argv the adapter hands ``gh`` (REST paths and the named GraphQL operations) and
answers with GitHub-shaped JSON from one repository and one project held in
memory. It knows only the calls the adapter makes; anything else fails loudly.
Project items come back in pages of ``page_size`` so pagination is exercised.

:class:`StubbedGitHubBoard` is the adapter over such a stub, the fourth shape of
the ``board`` contract fixture, with the stub's per-issue comment log exposed as
``comments`` the way the fakes expose theirs.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import json
import re
import subprocess
from urllib.parse import unquote

from squadra.config import GitHubBoardConfig
from squadra.domain import Lifecycle, Tags
from squadra.github_board import (
    CLOSED_COMPLETED,
    CLOSED_DUPLICATE,
    CLOSED_NO_REASON,
    CLOSED_NOT_PLANNED,
    GhApiGitHub,
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

    def add(self, issue: StubIssue) -> StubIssue:
        """Seed one issue."""
        self.issues[issue.number] = issue
        return issue

    # --- the gh entry point -------------------------------------------------------

    def __call__(self, args: Sequence[str]) -> str:
        """Answer one ``gh`` invocation (``args`` without ``gh``)."""
        self.calls.append(list(args))
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
        if operation == "SquadraIssue":
            if f"{v['owner']}/{v['name']}" != REPOSITORY:
                return {"repository": None}
            return {"repository": {"issue": self._issue_json(self._get(int(v["number"])))}}
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
    ) -> None:
        """Wire the adapter to ``github`` (a fresh, empty stub when omitted)."""
        self.github: InMemoryGitHub = github if github is not None else InMemoryGitHub()
        super().__init__(GITHUB_CONFIG, run=self.github, states=states, tags=tags)

    @property
    def comments(self) -> dict[int, list[str]]:
        """Every issue's comments, by number (issues without any are left out)."""
        return {n: issue.comments for n, issue in self.github.issues.items() if issue.comments}
