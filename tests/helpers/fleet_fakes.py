"""In-memory fakes for the AFK-fleet supervisor seams (AdoClient, Launcher).

Public so test files can annotate fixture parameters with the concrete types
(Pyright cannot infer fixture types; see Testing Conventions in CLAUDE.md).
Instances are provided by fixtures in tests/conftest.py.
"""

from dataclasses import dataclass, field

from flotilla.supervisor import IssueLinks, IssueRef


@dataclass
class FakeIssue:
    """Mutable board Issue held by the fake ADO client."""

    issue_id: int
    title: str
    state: str
    tags: list[str] = field(default_factory=list[str])
    parent_id: int | None = None
    predecessor_ids: tuple[int, ...] = ()


class FakeBoard:
    """Configurable in-memory ``AdoClient``; records every mutation in order."""

    def __init__(self) -> None:
        """Start with an empty board."""
        self.issues: dict[int, FakeIssue] = {}
        self.comments: dict[int, list[str]] = {}
        self.calls: list[tuple[str, int, str]] = []
        self.completed_prs: dict[str, str] = {}  # branch -> completed PR url

    def add_issue(self, issue: FakeIssue) -> None:
        """Seed the board with one Issue."""
        self.issues[issue.issue_id] = issue

    def issues_in_state(self, state: str) -> tuple[IssueRef, ...]:
        """Return seeded Issues currently in ``state``."""
        self.calls.append(("issues_in_state", 0, state))
        return tuple(
            IssueRef(issue_id=issue.issue_id, title=issue.title, tags=tuple(issue.tags))
            for issue in self.issues.values()
            if issue.state == state
        )

    def issue_links(self, issue_id: int) -> IssueLinks:
        """Return the seeded parent / predecessor links."""
        issue: FakeIssue = self.issues[issue_id]
        return IssueLinks(parent_id=issue.parent_id, predecessor_ids=issue.predecessor_ids)

    def completed_pr_url(self, branch: str) -> str | None:
        """Return the seeded completed-PR url for ``branch``, if any."""
        return self.completed_prs.get(branch)

    def issue_state(self, issue_id: int) -> str:
        """Return the Issue's current state."""
        return self.issues[issue_id].state

    def set_state(self, issue_id: int, state: str) -> None:
        """Record and apply a state transition."""
        self.calls.append(("set_state", issue_id, state))
        self.issues[issue_id].state = state

    def add_tag(self, issue_id: int, tag: str) -> None:
        """Record and apply a tag addition."""
        self.calls.append(("add_tag", issue_id, tag))
        if tag not in self.issues[issue_id].tags:
            self.issues[issue_id].tags.append(tag)

    def remove_tag(self, issue_id: int, tag: str) -> None:
        """Record and apply a tag removal."""
        self.calls.append(("remove_tag", issue_id, tag))
        if tag in self.issues[issue_id].tags:
            self.issues[issue_id].tags.remove(tag)

    def add_comment(self, issue_id: int, html: str) -> None:
        """Record a discussion comment."""
        self.calls.append(("add_comment", issue_id, html))
        self.comments.setdefault(issue_id, []).append(html)


class FakeLauncher:
    """Spy launcher; configurable to fail for chosen issue ids."""

    def __init__(self) -> None:
        """Start with no recorded launches and no configured failures."""
        self.launches: list[tuple[int, str, int]] = []
        self.fail_for: set[int] = set()

    def launch(self, issue_id: int, branch: str, attempt: int) -> bool:
        """Record the launch; fail when the issue id is in ``fail_for``."""
        self.launches.append((issue_id, branch, attempt))
        return issue_id not in self.fail_for


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
