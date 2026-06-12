"""The board ResourceAccess seam and its Azure DevOps adapter.

``BoardAccess`` (renamed from ``AdoClient``) is the contract the supervisor
passes depend on; ``AzCliAdo`` is the concrete az-CLI-backed adapter together
with its WIQL/JSON parsing helpers. Renaming the Protocol is the only change
here — the operations, their signatures, and the ADO wire behaviour are
unchanged. PR2 makes this seam provider-neutral (``Lifecycle`` state, structured
comment events, configurable tag prefix).
"""

from collections.abc import Callable, Sequence
import json
import os
import subprocess
import tempfile
from typing import Final, Protocol, cast

from flotilla.domain import IssueLinks, IssueRef

_PREDECESSOR_REL: Final[str] = "System.LinkTypes.Dependency-Reverse"
_PARENT_REL: Final[str] = "System.LinkTypes.Hierarchy-Reverse"


class BoardAccess(Protocol):
    """The ADO operations the supervisor passes need (az-CLI-backed in prod)."""

    def issues_in_state(self, state: str) -> tuple[IssueRef, ...]:
        """Return all Issues currently in ``state``."""
        ...

    def completed_pr_url(self, branch: str) -> str | None:
        """Return the completed PR for ``branch`` targeting main, if any."""
        ...

    def issue_links(self, issue_id: int) -> IssueLinks:
        """Return parent / predecessor links of one Issue."""
        ...

    def issue_state(self, issue_id: int) -> str:
        """Return the current ``System.State`` of a work item."""
        ...

    def set_state(self, issue_id: int, state: str) -> None:
        """Transition a work item to ``state``."""
        ...

    def add_tag(self, issue_id: int, tag: str) -> None:
        """Add ``tag`` to the work item (read-append-write of System.Tags)."""
        ...

    def remove_tag(self, issue_id: int, tag: str) -> None:
        """Remove ``tag`` from the work item."""
        ...

    def add_comment(self, issue_id: int, html: str) -> None:
        """Add an HTML discussion comment to the work item."""
        ...


# --- az CLI adapter ----------------------------------------------------------


def _run_az(args: Sequence[str]) -> str:
    """Run an az command and return stdout (raises on a non-zero exit)."""
    result: subprocess.CompletedProcess[str] = subprocess.run(
        ["az", *args], capture_output=True, text=True, check=True
    )
    return result.stdout


class AzCliAdo:
    """``BoardAccess`` backed by the az CLI (the devbox's authenticated transport)."""

    def __init__(
        self,
        run: Callable[[Sequence[str]], str] = _run_az,
        project: str | None = None,
    ) -> None:
        """Wire the adapter to a command runner (injectable for tests).

        ``project`` names the Azure DevOps project for the REST route
        parameter; when omitted it is resolved once from the configured az
        default on first use.
        """
        self._run = run
        self._project = project

    def issues_in_state(self, state: str) -> tuple[IssueRef, ...]:
        """Return Issues in ``state`` with their tags, via the WIQL REST API.

        ``az boards query`` produces no output under the devbox's az-CLI /
        azure-devops extension pairing, so the query goes through ``az devops
        invoke`` instead: ``wit/wiql`` for the matching ids, then
        ``wit/workitemsbatch`` for their fields.
        """
        wiql: str = (
            "SELECT [System.Id] FROM WorkItems "
            "WHERE [System.TeamProject] = @project "
            "AND [System.WorkItemType] = 'Issue' "
            f"AND [System.State] = '{state}'"
        )
        ids: list[int] = _wiql_ids(self._invoke_json("wiql", {"query": wiql}))
        if not ids:
            return ()
        batch: str = self._invoke_json(
            "workitemsbatch",
            {"ids": ids, "fields": ["System.Id", "System.Title", "System.Tags"]},
        )
        value: object = _json_object(batch).get("value")
        items: list[object] = cast("list[object]", value) if isinstance(value, list) else []
        return _issue_refs_from_items(items)

    def _resolve_project(self) -> str:
        """Return the project for REST routes, resolving the az default once."""
        if self._project is None:
            self._project = _configured_project(self._run)
        return self._project

    def _invoke_json(self, resource: str, body: dict[str, object]) -> str:
        """POST ``body`` to a ``wit`` REST resource via ``az devops invoke``.

        The body is written to a temp file because ``az devops invoke`` reads
        its payload from ``--in-file`` only; the file is removed afterwards.
        """
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as handle:
            json.dump(body, handle)
            in_file: str = handle.name
        try:
            return self._run(
                [
                    "devops",
                    "invoke",
                    "--area",
                    "wit",
                    "--resource",
                    resource,
                    "--route-parameters",
                    f"project={self._resolve_project()}",
                    "--http-method",
                    "POST",
                    "--in-file",
                    in_file,
                    "--api-version",
                    "7.1",
                    "-o",
                    "json",
                ]
            )
        finally:
            os.unlink(in_file)

    def issue_links(self, issue_id: int) -> IssueLinks:
        """Read parent / predecessor relations from the work item."""
        out: str = self._run(
            [
                "boards",
                "work-item",
                "show",
                "--id",
                str(issue_id),
                "--expand",
                "relations",
                "-o",
                "json",
            ]
        )
        return _parse_issue_links(out)

    def completed_pr_url(self, branch: str) -> str | None:
        """Find a completed PR from ``branch`` into main, if one exists."""
        out: str = self._run(
            [
                "repos",
                "pr",
                "list",
                "--source-branch",
                branch,
                "--target-branch",
                "main",
                "--status",
                "completed",
                "-o",
                "json",
            ]
        )
        if not out.strip():
            return None
        raw: object = json.loads(out)
        if not isinstance(raw, list) or not raw:
            return None
        first: object = cast("list[object]", raw)[0]
        if not isinstance(first, dict):
            return None
        pr: dict[str, object] = cast("dict[str, object]", first)
        url: object = pr.get("url")
        if isinstance(url, str):
            return url
        pr_id: object = pr.get("pullRequestId")
        return f"PR {pr_id}" if isinstance(pr_id, int) else None

    def issue_state(self, issue_id: int) -> str:
        """Read ``System.State`` of one work item."""
        fields: dict[str, object] = _show_fields(self._run, issue_id)
        state: object = fields.get("System.State")
        return state if isinstance(state, str) else ""

    def set_state(self, issue_id: int, state: str) -> None:
        """Transition the work item to ``state``."""
        self._run(
            ["boards", "work-item", "update", "--id", str(issue_id), "--state", state, "-o", "none"]
        )

    def add_tag(self, issue_id: int, tag: str) -> None:
        """Append ``tag`` to System.Tags (read-append-write)."""
        tags: list[str] = self._current_tags(issue_id)
        if tag in tags:
            return
        self._write_tags(issue_id, [*tags, tag])

    def remove_tag(self, issue_id: int, tag: str) -> None:
        """Filter ``tag`` out of System.Tags."""
        tags: list[str] = self._current_tags(issue_id)
        if tag not in tags:
            return
        self._write_tags(issue_id, [item for item in tags if item != tag])

    def add_comment(self, issue_id: int, html: str) -> None:
        """Add an HTML discussion comment to the work item."""
        self._run(
            [
                "boards",
                "work-item",
                "update",
                "--id",
                str(issue_id),
                "--discussion",
                html,
                "-o",
                "none",
            ]
        )

    def _current_tags(self, issue_id: int) -> list[str]:
        fields: dict[str, object] = _show_fields(self._run, issue_id)
        raw: object = fields.get("System.Tags")
        if not isinstance(raw, str) or not raw.strip():
            return []
        return [part.strip() for part in raw.split(";") if part.strip()]

    def _write_tags(self, issue_id: int, tags: list[str]) -> None:
        self._run(
            [
                "boards",
                "work-item",
                "update",
                "--id",
                str(issue_id),
                "--fields",
                f"System.Tags={'; '.join(tags)}",
                "-o",
                "none",
            ]
        )


def _show_fields(run: Callable[[Sequence[str]], str], issue_id: int) -> dict[str, object]:
    """Fetch a work item's fields dict."""
    out: str = run(["boards", "work-item", "show", "--id", str(issue_id), "-o", "json"])
    data: dict[str, object] = _json_object(out)
    fields: object = data.get("fields")
    return cast("dict[str, object]", fields) if isinstance(fields, dict) else {}


def _wiql_ids(payload: str) -> list[int]:
    """Extract work-item ids from a ``wit/wiql`` REST response."""
    work_items: object = _json_object(payload).get("workItems")
    if not isinstance(work_items, list):
        return []
    ids: list[int] = []
    for entry in cast("list[object]", work_items):
        if not isinstance(entry, dict):
            continue
        wid: object = cast("dict[str, object]", entry).get("id")
        if isinstance(wid, int):
            ids.append(wid)
    return ids


def _configured_project(run: Callable[[Sequence[str]], str]) -> str:
    """Read the default Azure DevOps project from az configuration."""
    out: str = run(["devops", "configure", "--list"])
    for line in out.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "project":
            project: str = value.strip()
            if project:
                return project
    raise RuntimeError(
        "fleet supervisor: no default Azure DevOps project configured; "
        "set one with `az devops configure --defaults project=<name>`"
    )


def _issue_refs_from_items(items: Sequence[object]) -> tuple[IssueRef, ...]:
    """Build IssueRefs from work-item dicts (each an ``id`` + ``fields`` map)."""
    refs: list[IssueRef] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        item: dict[str, object] = cast("dict[str, object]", entry)
        fields: object = item.get("fields")
        fields_map: dict[str, object] = (
            cast("dict[str, object]", fields) if isinstance(fields, dict) else {}
        )
        issue_id: object = item.get("id", fields_map.get("System.Id"))
        title: object = fields_map.get("System.Title", "")
        tags_raw: object = fields_map.get("System.Tags", "")
        if isinstance(issue_id, str) and issue_id.isdigit():
            issue_id = int(issue_id)
        if not isinstance(issue_id, int):
            continue
        refs.append(
            IssueRef(
                issue_id=issue_id,
                title=title if isinstance(title, str) else "",
                tags=_split_tags(tags_raw),
            )
        )
    return tuple(refs)


def _parse_issue_links(payload: str) -> IssueLinks:
    """Parse parent / predecessor ids out of an expanded work item."""
    data: dict[str, object] = _json_object(payload)
    relations: object = data.get("relations")
    parent_id: int | None = None
    predecessors: list[int] = []
    if isinstance(relations, list):
        for entry in cast("list[object]", relations):
            if not isinstance(entry, dict):
                continue
            relation: dict[str, object] = cast("dict[str, object]", entry)
            rel_type: object = relation.get("rel")
            url: object = relation.get("url")
            target: int | None = _id_from_url(url) if isinstance(url, str) else None
            if target is None:
                continue
            if rel_type == _PREDECESSOR_REL:
                predecessors.append(target)
            elif rel_type == _PARENT_REL:
                parent_id = target
    return IssueLinks(parent_id=parent_id, predecessor_ids=tuple(predecessors))


def _id_from_url(url: str) -> int | None:
    """Extract the trailing work-item id from a relation URL."""
    tail: str = url.rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _split_tags(raw: object) -> tuple[str, ...]:
    """Split ADO's `;`-separated tag string into a tuple."""
    if not isinstance(raw, str):
        return ()
    return tuple(part.strip() for part in raw.split(";") if part.strip())


def _json_object(payload: str) -> dict[str, object]:
    """Parse a JSON object, returning {} for empty or non-object payloads.

    Tolerating an empty payload keeps a transient blank board read from
    crashing a whole tick (the failure mode that motivated routing reads
    through the REST API); genuinely malformed JSON still raises.
    """
    if not payload.strip():
        return {}
    raw: object = json.loads(payload)
    return cast("dict[str, object]", raw) if isinstance(raw, dict) else {}
