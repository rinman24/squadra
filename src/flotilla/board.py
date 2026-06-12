"""The board ResourceAccess seam, its Azure DevOps adapter, and the registry.

``BoardAccess`` is the provider-neutral contract the supervisor passes depend
on — it speaks the neutral ``Lifecycle`` state, ``WorkItem`` records, and
structured ``CommentEvent`` values, never board-native state strings or markup.
``AzCliAdo`` is the concrete az-CLI-backed adapter: it maps native states to and
from ``Lifecycle`` via the configured state map, renders comment events to ADO
HTML, and validates the configured names against the live board. ``build_board``
is the hardcoded provider registry (the composition-root seam) — name maps to an
adapter factory.
"""

from collections.abc import Callable, Mapping, Sequence
import json
import os
import subprocess
import tempfile
from typing import Final, Protocol, cast

from flotilla.config import ADO_BASIC_STATES, ConfigError, FlotillaConfig
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

_PREDECESSOR_REL: Final[str] = "System.LinkTypes.Dependency-Reverse"
_PARENT_REL: Final[str] = "System.LinkTypes.Hierarchy-Reverse"


class BoardValidationError(RuntimeError):
    """Raised when the configuration does not resolve against the live board."""


class BoardAccess(Protocol):
    """The provider-neutral board operations the supervisor passes need."""

    def items_in_state(self, state: Lifecycle) -> tuple[WorkItem, ...]:
        """Return all work items whose native state maps to ``state``."""
        ...

    def completed_pr_url(self, branch: str) -> str | None:
        """Return the completed PR for ``branch`` vs the base branch, if any."""
        ...

    def item_links(self, item_id: int) -> WorkItemLinks:
        """Return parent / predecessor links of one work item."""
        ...

    def item_state(self, item_id: int) -> Lifecycle:
        """Return the neutral lifecycle bucket of one work item."""
        ...

    def set_state(self, item_id: int, state: Lifecycle) -> None:
        """Transition a work item into the native state of ``state``."""
        ...

    def add_tag(self, item_id: int, tag: str) -> None:
        """Add ``tag`` to the work item (idempotent)."""
        ...

    def remove_tag(self, item_id: int, tag: str) -> None:
        """Remove ``tag`` from the work item."""
        ...

    def add_comment(self, item_id: int, event: CommentEvent) -> None:
        """Add a discussion comment, rendering ``event`` to native markup."""
        ...

    def validate_config(self) -> None:
        """Resolve the configuration against the live board; raise loud on mismatch."""
        ...


# --- ADO comment rendering ----------------------------------------------------


def render_ado_html(event: CommentEvent, tags: Tags) -> str:
    """Render a structured ``CommentEvent`` to one ADO discussion HTML comment."""
    match event:
        case Claimed(runner_id=runner_id, branch=branch, when=when):
            return (
                f"<p>fleet: claimed by supervisor — runner <code>{runner_id}</code>, "
                f"branch <code>{branch}</code>, {when}.</p>"
            )
        case RolledBack(reason=reason):
            return f"<p>fleet: {reason} — claim rolled back.</p>"
        case Finalized(pr_url=pr_url, branch=branch):
            return (
                f'<p>fleet: finalized — PR completed (<a href="{pr_url}">{pr_url}</a>), '
                f"branch <code>{branch}</code> cleaned up.</p>"
            )
        case Reaped(evidence=evidence, attempt=attempt):
            return (
                f"<p>fleet: reaped — {evidence} (attempt {attempt}); "
                f"worktree archived, requeued for retry.</p>"
            )
        case Escalated(attempt=attempt, cap=cap):
            return (
                f"<p>fleet: retry cap exhausted (next attempt would be {attempt}, cap {cap}) "
                f"— escalated to <code>{tags.failed}</code>. Triage via the status file, then "
                f"remove the tag (and the fleet status dir for a clean restart) to requeue.</p>"
            )


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
        *,
        states: Mapping[Lifecycle, tuple[str, ...]] = ADO_BASIC_STATES,
        base_branch: str = "main",
        tags: Tags = Tags(),
    ) -> None:
        """Wire the adapter to a command runner and its board configuration.

        ``project`` names the Azure DevOps project for the REST route parameter;
        when omitted it is resolved once from the configured az default on first
        use. ``states`` is the Lifecycle→native-state map (set writes use the
        first native name of a bucket); ``base_branch`` is the PR target;
        ``tags`` is the fleet tag vocabulary used when rendering comments.
        """
        self._run = run
        self._project = project
        self._states = states
        self._base_branch = base_branch
        self._tags = tags

    def items_in_state(self, state: Lifecycle) -> tuple[WorkItem, ...]:
        """Return work items whose native state maps to ``state``, via WIQL REST.

        ``az boards query`` produces no output under the devbox's az-CLI /
        azure-devops extension pairing, so the query goes through ``az devops
        invoke`` instead: ``wit/wiql`` for the matching ids, then
        ``wit/workitemsbatch`` for their fields.
        """
        in_clause: str = ", ".join(f"'{name}'" for name in self._states[state])
        wiql: str = (
            "SELECT [System.Id] FROM WorkItems "
            "WHERE [System.TeamProject] = @project "
            "AND [System.WorkItemType] = 'Issue' "
            f"AND [System.State] IN ({in_clause})"
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
        return _work_items_from_items(items)

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

    def item_links(self, item_id: int) -> WorkItemLinks:
        """Read parent / predecessor relations from the work item."""
        out: str = self._run(
            [
                "boards",
                "work-item",
                "show",
                "--id",
                str(item_id),
                "--expand",
                "relations",
                "-o",
                "json",
            ]
        )
        return _parse_item_links(out)

    def completed_pr_url(self, branch: str) -> str | None:
        """Find a completed PR from ``branch`` into the base branch, if one exists."""
        out: str = self._run(
            [
                "repos",
                "pr",
                "list",
                "--source-branch",
                branch,
                "--target-branch",
                self._base_branch,
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

    def item_state(self, item_id: int) -> Lifecycle:
        """Read the work item's native ``System.State`` and map it to a bucket."""
        fields: dict[str, object] = _show_fields(self._run, item_id)
        native: object = fields.get("System.State")
        return self._lifecycle_of(native if isinstance(native, str) else "")

    def _lifecycle_of(self, native: str) -> Lifecycle:
        """Reverse-map a native state to its bucket; unmapped → QUEUED (not done)."""
        for lifecycle, names in self._states.items():
            if native in names:
                return lifecycle
        return Lifecycle.QUEUED

    def set_state(self, item_id: int, state: Lifecycle) -> None:
        """Transition the work item into the first native name of ``state``."""
        native: str = self._states[state][0]
        self._run(
            ["boards", "work-item", "update", "--id", str(item_id), "--state", native, "-o", "none"]
        )

    def add_tag(self, item_id: int, tag: str) -> None:
        """Append ``tag`` to System.Tags (read-append-write)."""
        tags: list[str] = self._current_tags(item_id)
        if tag in tags:
            return
        self._write_tags(item_id, [*tags, tag])

    def remove_tag(self, item_id: int, tag: str) -> None:
        """Filter ``tag`` out of System.Tags."""
        tags: list[str] = self._current_tags(item_id)
        if tag not in tags:
            return
        self._write_tags(item_id, [item for item in tags if item != tag])

    def add_comment(self, item_id: int, event: CommentEvent) -> None:
        """Render ``event`` to ADO HTML and add it as a discussion comment."""
        self._run(
            [
                "boards",
                "work-item",
                "update",
                "--id",
                str(item_id),
                "--discussion",
                render_ado_html(event, self._tags),
                "-o",
                "none",
            ]
        )

    def validate_config(self) -> None:
        """Check every configured native state exists among the project's states.

        This is the safety mechanism: a misconfigured state name fails the
        startup preflight loudly rather than silently mis-mutating the board.
        """
        available: set[str] = self._board_state_names()
        configured: set[str] = {name for names in self._states.values() for name in names}
        missing: list[str] = sorted(name for name in configured if name not in available)
        if missing:
            raise BoardValidationError(
                f"configured board state(s) {missing} not found among this project's "
                f"Issue states {sorted(available)}; fix flotilla.toml [board.states]"
            )

    def _board_state_names(self) -> set[str]:
        """Return the names of the Issue work-item-type's states on the live board."""
        out: str = self._run(
            [
                "devops",
                "invoke",
                "--area",
                "wit",
                "--resource",
                "workItemTypeStates",
                "--route-parameters",
                f"project={self._resolve_project()}",
                "type=Issue",
                "--api-version",
                "7.1",
                "-o",
                "json",
            ]
        )
        value: object = _json_object(out).get("value")
        names: set[str] = set()
        if isinstance(value, list):
            for entry in cast("list[object]", value):
                if isinstance(entry, dict):
                    name: object = cast("dict[str, object]", entry).get("name")
                    if isinstance(name, str):
                        names.add(name)
        return names

    def _current_tags(self, item_id: int) -> list[str]:
        fields: dict[str, object] = _show_fields(self._run, item_id)
        raw: object = fields.get("System.Tags")
        if not isinstance(raw, str) or not raw.strip():
            return []
        return [part.strip() for part in raw.split(";") if part.strip()]

    def _write_tags(self, item_id: int, tags: list[str]) -> None:
        self._run(
            [
                "boards",
                "work-item",
                "update",
                "--id",
                str(item_id),
                "--fields",
                f"System.Tags={'; '.join(tags)}",
                "-o",
                "none",
            ]
        )


# --- provider registry (composition-root seam) --------------------------------

ProviderFactory = Callable[[FlotillaConfig], BoardAccess]


def _build_ado(config: FlotillaConfig) -> BoardAccess:
    """Construct the ADO adapter from the resolved configuration."""
    return AzCliAdo(states=config.states, base_branch=config.base_branch, tags=config.tags)


# Hardcoded name → adapter factory. New providers register here; out-of-tree
# entry-point plugins are a deferred future volatility, not built now.
PROVIDERS: Final[dict[str, ProviderFactory]] = {"ado": _build_ado}


def build_board(config: FlotillaConfig) -> BoardAccess:
    """Build the configured provider's ``BoardAccess`` adapter (the registry)."""
    try:
        factory: ProviderFactory = PROVIDERS[config.provider]
    except KeyError:
        raise ConfigError(
            f"unknown board provider {config.provider!r}; known providers: {sorted(PROVIDERS)}"
        ) from None
    return factory(config)


# --- parsing helpers ----------------------------------------------------------


def _show_fields(run: Callable[[Sequence[str]], str], item_id: int) -> dict[str, object]:
    """Fetch a work item's fields dict."""
    out: str = run(["boards", "work-item", "show", "--id", str(item_id), "-o", "json"])
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


def _work_items_from_items(items: Sequence[object]) -> tuple[WorkItem, ...]:
    """Build WorkItems from work-item dicts (each an ``id`` + ``fields`` map)."""
    refs: list[WorkItem] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        item: dict[str, object] = cast("dict[str, object]", entry)
        fields: object = item.get("fields")
        fields_map: dict[str, object] = (
            cast("dict[str, object]", fields) if isinstance(fields, dict) else {}
        )
        item_id: object = item.get("id", fields_map.get("System.Id"))
        title: object = fields_map.get("System.Title", "")
        tags_raw: object = fields_map.get("System.Tags", "")
        if isinstance(item_id, str) and item_id.isdigit():
            item_id = int(item_id)
        if not isinstance(item_id, int):
            continue
        refs.append(
            WorkItem(
                item_id=item_id,
                title=title if isinstance(title, str) else "",
                tags=_split_tags(tags_raw),
            )
        )
    return tuple(refs)


def _parse_item_links(payload: str) -> WorkItemLinks:
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
    return WorkItemLinks(parent_id=parent_id, predecessor_ids=tuple(predecessors))


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
