"""The registered fake provider: a ``BoardAccess`` over one local JSON file.

``provider = "fake"`` in ``squadra.toml`` builds a :class:`JsonFileBoard`, so the
CLI and an outside planner's end-to-end tests run without a live board
(claude-skills DB-D1 A3). A CLI run is one process per verb, so the board lives
in a file, not in memory (board-writes SQ3, ledger N16):

- **Where.** ``<FLEET_HOME>/.squadra/fake-board.json``
  (:func:`fake_board_path`), beside the ``squadra.toml`` that names the
  provider. There is no config key: the file goes wherever ``FLEET_HOME``
  goes. A missing file is an empty board.
- **Seeding.** Write the file before the run (the format below), or call the
  seeding methods (:meth:`JsonFileBoard.add`, ``seed_pr``, ``seed_origin``,
  ``seed_partial``, ``arm_create_fault``), which write it for you.
- **Concurrency.** Every write is one read-modify-write under an exclusive
  ``flock`` on a sidecar ``fake-board.json.lock``, saved through a temp file and
  ``os.replace``, so two concurrent CLI calls never interleave inside a write
  and a reader sees one whole version. The lock keeps the file whole, not the
  verbs atomic: ``queue_increment`` reads, then writes, as it does on any
  provider.

The file is one JSON object; every key is optional::

    {
      "version": 1,
      "states": ["Backlog", "In Progress", "Done", "Withdrawn"],
      "items": {
        "101": {"title": "...", "body": "...", "state": "Backlog",
                "tags": [], "parent": 200, "predecessors": [150],
                "origin": "A:I1", "comments": []}
      },
      "completed_prs": {"feat/increment-101-x": "https://..."},
      "fail_create_after_step": null
    }

``states`` is the board's own native state list, which ``validate_config``
checks the ``[board.states]`` map against both ways (ADR-0004 rule 3). When it
is absent, the board has exactly the configured states. An item's ``state`` of
``null`` marks a partial item.

``create_increment`` is k separate writes, as a provider needing several calls
would make them (ADR-0005 decision 1, the four obligations): ``create``
(Origin, title and body, in no bucket), ``parent``, one ``predecessor`` write
per link, and ``commit`` (into QUEUED), last. ``fail_create_after_step: j``
arms a one-shot fault: the next ``create_increment`` stops after its j-th
write, as a crash would, and raises :class:`InjectedCrashError` (ledger N8,
Juval's tests 1–5).
"""

from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
import fcntl
import json
import os
from pathlib import Path
import tempfile
from typing import Final, TypeVar, cast

from squadra.board import BoardValidationError
from squadra.domain import (
    CommentEvent,
    IncrementRequest,
    Lifecycle,
    OriginRecord,
    Tags,
    WorkItem,
    WorkItemLinks,
)

FAKE_BOARD_FILE: Final[str] = ".squadra/fake-board.json"
_VERSION: Final[int] = 1
_FIRST_ID: Final[int] = 1000  # a fresh board's first item is 1001


class InjectedCrashError(RuntimeError):
    """Raised when an armed fault stops ``create_increment`` part-way."""


def fake_board_path(fleet_home: Path) -> Path:
    """Return the fake provider's board file for the target repository ``fleet_home``."""
    return fleet_home / FAKE_BOARD_FILE


def create_step_count(request: IncrementRequest) -> int:
    """How many writes a fresh ``create_increment(request)`` makes (k).

    ``create``, ``parent``, one per predecessor, ``commit``. A fault armed with
    ``after_step`` j in ``1 .. k-1`` leaves a partial item; j equal to k lands
    the item and then crashes, as a lost response would.
    """
    return 3 + len(request.predecessors)


@dataclass
class _Item:
    """One item on the fake board."""

    title: str
    state: str | None  # native state name; None while partial
    body: str = ""
    tags: list[str] = field(default_factory=list[str])
    parent: int | None = None
    predecessors: list[int] = field(default_factory=list[int])
    origin: str | None = None
    comments: list[str] = field(default_factory=list[str])


@dataclass
class _Board:
    """The whole file, parsed."""

    states: list[str] | None = None
    items: dict[int, _Item] = field(default_factory=dict[int, _Item])
    completed_prs: dict[str, str] = field(default_factory=dict[str, str])
    fail_create_after_step: int | None = None


_Step = Callable[[_Board], None]
_T = TypeVar("_T")


class JsonFileBoard:
    """``BoardAccess`` over one JSON file, shared by every process that opens it."""

    def __init__(
        self,
        path: Path,
        *,
        states: Mapping[Lifecycle, tuple[str, ...]],
        tags: Tags = Tags(),
    ) -> None:
        """Open the board at ``path`` under the ``[board.states]`` map ``states``.

        Nothing is read or written until the first call; a missing file is an
        empty board, created on the first write.
        """
        self._path = path
        self._states = states
        self._tags = tags

    # --- BoardAccess: reads ---------------------------------------------------

    def items_in_state(self, state: Lifecycle) -> tuple[WorkItem, ...]:
        """Return the items whose native state maps to ``state`` (none if unmapped)."""
        names: tuple[str, ...] = self._states.get(state, ())
        return tuple(
            WorkItem(item_id=item_id, title=item.title, tags=tuple(item.tags))
            for item_id, item in self._read().items.items()
            if item.state is not None and item.state in names
        )

    def completed_pr_url(self, branch: str) -> str | None:
        """Return the seeded completed PR for ``branch``, if any."""
        return self._read().completed_prs.get(branch)

    def item_links(self, item_id: int) -> WorkItemLinks:
        """Return the item's parent and predecessor links."""
        item: _Item = self._item(self._read(), item_id)
        return WorkItemLinks(parent_id=item.parent, predecessor_ids=tuple(item.predecessors))

    def item_state(self, item_id: int) -> Lifecycle:
        """Map the item's native state to its bucket; a partial or unmapped one raises."""
        return self._lifecycle_of(item_id, self._item(self._read(), item_id).state)

    def validate_config(self) -> None:
        """Check ``[board.states]`` against the board's own states, both ways (ADR-0004)."""
        configured: set[str] = {name for names in self._states.values() for name in names}
        declared: list[str] | None = self._read().states
        available: set[str] = configured if declared is None else set(declared)
        missing: list[str] = sorted(configured - available)
        if missing:
            raise BoardValidationError(
                f"configured board state(s) {missing} not found among the fake board's "
                f"states {sorted(available)} ({self._path}); fix squadra.toml [board.states]"
            )
        unmapped: list[str] = sorted(available - configured)
        if unmapped:
            raise BoardValidationError(
                f"the fake board's state(s) {unmapped} map to no lifecycle bucket; "
                "map each in squadra.toml [board.states] (queued/active/done/withdrawn)"
            )

    def items_with_origin(self) -> tuple[OriginRecord, ...]:
        """Return every item carrying an Origin, every bucket, duplicates and partial ones too."""
        return tuple(
            OriginRecord(
                item_id=item_id,
                origin=item.origin,
                parent=item.parent,
                predecessors=tuple(item.predecessors),
                title=item.title,
                body=item.body,
                lifecycle=None if item.state is None else self._lifecycle_of(item_id, item.state),
            )
            for item_id, item in self._read().items.items()
            if item.origin is not None
        )

    @property
    def comments(self) -> dict[int, list[str]]:
        """Each item's comments, one JSON object per ``add_comment`` (no markup dialect)."""
        return {item_id: item.comments for item_id, item in self._read().items.items()}

    # --- BoardAccess: writes --------------------------------------------------

    def set_state(self, item_id: int, state: Lifecycle) -> None:
        """Move the item to the first native name of ``state`` (a raw write)."""
        native: str = self._first_native(state)

        def write(board: _Board) -> None:
            self._item(board, item_id).state = native

        self._write(write)

    def add_tag(self, item_id: int, tag: str) -> None:
        """Add ``tag`` to the item (idempotent)."""

        def write(board: _Board) -> None:
            tags: list[str] = self._item(board, item_id).tags
            if tag not in tags:
                tags.append(tag)

        self._write(write)

    def remove_tag(self, item_id: int, tag: str) -> None:
        """Remove ``tag`` from the item (no-op if absent)."""

        def write(board: _Board) -> None:
            item: _Item = self._item(board, item_id)
            item.tags = [name for name in item.tags if name != tag]

        self._write(write)

    def add_comment(self, item_id: int, event: CommentEvent) -> None:
        """Record ``event`` as one JSON object: its kind and fields."""
        rendered: str = json.dumps({"event": type(event).__name__, **asdict(event)})

        def write(board: _Board) -> None:
            self._item(board, item_id).comments.append(rendered)

        self._write(write)

    def create_increment(self, request: IncrementRequest, partial_item: int | None = None) -> int:
        """Land a QUEUED item in k separate writes, or finish ``partial_item``.

        Each write is its own locked save, so another process sees every
        intermediate state: the item carries its Origin from the first write
        and is in no bucket until the last (ADR-0005 decision 1). Finishing
        adds the missing parent and predecessors one write each, then
        commits, and returns the same id. An armed fault stops the call after
        its j-th write and raises :class:`InjectedCrashError`.
        """
        queued: str = self._first_native(Lifecycle.QUEUED)
        if partial_item is None:
            armed: int | None = self._disarm(create_step_count(request))
            item_id: int = self._write(lambda board: self._create(board, request))
            self._crash_if(armed, 1, create_step_count(request))
            steps: list[_Step] = [lambda board: self._link_parent(board, item_id, request.parent)]
            steps.extend(self._link_predecessor(item_id, link) for link in request.predecessors)
            steps.append(lambda board: self._commit(board, item_id, queued))
            self._run_steps(steps, armed, done=1)
            return item_id
        item: _Item = self._item(self._read(), partial_item)
        if item.origin != request.origin or item.state is not None:
            raise ValueError(
                f"item {partial_item} is not a partial item carrying origin {request.origin!r}"
            )
        steps = []
        if item.parent is None:
            steps.append(lambda board: self._link_parent(board, partial_item, request.parent))
        steps.extend(
            self._link_predecessor(partial_item, link)
            for link in request.predecessors
            if link not in item.predecessors
        )
        steps.append(lambda board: self._commit(board, partial_item, queued))
        self._run_steps(steps, self._disarm(len(steps)), done=0)
        return partial_item

    # --- seeding (the fake's own surface, for tests) --------------------------

    def add(  # noqa: PLR0913 - one seed item, every field a keyword
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
        """Seed one item in ``lifecycle`` (or a specific ``native_state``)."""
        state: str = native_state if native_state is not None else self._first_native(lifecycle)

        def write(board: _Board) -> None:
            board.items[item_id] = _Item(
                title=title,
                state=state,
                tags=list(tags),
                parent=parent_id,
                predecessors=list(predecessor_ids),
            )

        self._write(write)

    def seed_pr(self, branch: str, url: str) -> None:
        """Seed a completed PR for ``branch``."""

        def write(board: _Board) -> None:
            board.completed_prs[branch] = url

        self._write(write)

    def seed_origin(self, item_id: int, origin: str) -> None:
        """Set an Origin on a seeded item by hand (e.g. to inject a duplicate)."""

        def write(board: _Board) -> None:
            self._item(board, item_id).origin = origin

        self._write(write)

    def seed_partial(
        self,
        origin: str,
        title: str,
        body: str,
        *,
        parent_id: int | None = None,
        predecessor_ids: tuple[int, ...] = (),
    ) -> int:
        """Place an item a crash mid-create left behind: Origin set, no state yet."""

        def write(board: _Board) -> int:
            item_id: int = _next_id(board)
            board.items[item_id] = _Item(
                title=title,
                state=None,
                body=body,
                parent=parent_id,
                predecessors=list(predecessor_ids),
                origin=origin,
            )
            return item_id

        return self._write(write)

    def arm_create_fault(self, after_step: int) -> None:
        """Make the next ``create_increment`` stop after its ``after_step``-th write."""
        if after_step < 1:
            raise ValueError(f"after_step must be at least 1, got {after_step}")

        def write(board: _Board) -> None:
            board.fail_create_after_step = after_step

        self._write(write)

    # --- internals ------------------------------------------------------------

    def _create(self, board: _Board, request: IncrementRequest) -> int:
        """Write 1: the item with its Origin, title and body, in no bucket (obligation 1)."""
        item_id: int = _next_id(board)
        board.items[item_id] = _Item(
            title=request.title, state=None, body=request.body, origin=request.origin
        )
        return item_id

    def _link_parent(self, board: _Board, item_id: int, parent: int) -> None:
        self._item(board, item_id).parent = parent

    def _link_predecessor(self, item_id: int, link: int) -> _Step:
        def write(board: _Board) -> None:
            predecessors: list[int] = self._item(board, item_id).predecessors
            if link not in predecessors:
                predecessors.append(link)

        return write

    def _commit(self, board: _Board, item_id: int, queued: str) -> None:
        """Make the last write, into QUEUED (obligation 4)."""
        self._item(board, item_id).state = queued

    def _disarm(self, steps: int) -> int | None:
        """Take the armed fault, if any, for a create of ``steps`` writes."""
        if self._read().fail_create_after_step is None:
            return None
        armed: int | None = self._write(_take_fault)
        if armed is not None and armed > steps:
            raise ValueError(f"create fault armed after step {armed}, but this create has {steps}")
        return armed

    def _run_steps(self, steps: Sequence[_Step], armed: int | None, *, done: int) -> None:
        """Run each step as its own save, after ``done`` already made."""
        for number, step in enumerate(steps, start=done + 1):
            self._write(step)
            self._crash_if(armed, number, done + len(steps))

    @staticmethod
    def _crash_if(armed: int | None, number: int, steps: int) -> None:
        if number == armed:
            raise InjectedCrashError(
                f"fake board: injected crash after create step {number} of {steps}"
            )

    def _lifecycle_of(self, item_id: int, native: str | None) -> Lifecycle:
        if native is None:
            raise BoardValidationError(
                f"item {item_id} is partial: its create has not committed it, so it is in no bucket"
            )
        for lifecycle, names in self._states.items():
            if native in names:
                return lifecycle
        raise BoardValidationError(
            f"native state {native!r} maps to no lifecycle bucket; fix squadra.toml [board.states]"
        )

    def _first_native(self, state: Lifecycle) -> str:
        names: tuple[str, ...] = self._states.get(state, ())
        if not names:
            raise BoardValidationError(
                f"no native state maps to {state.value!r}; fix squadra.toml [board.states]"
            )
        return names[0]

    def _item(self, board: _Board, item_id: int) -> _Item:
        try:
            return board.items[item_id]
        except KeyError:
            raise KeyError(f"no item {item_id} on the fake board {self._path}") from None

    def _read(self) -> _Board:
        """Load the current version (no lock: a save replaces the file whole)."""
        try:
            text: str = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return _Board()
        return _parse(text, self._path)

    def _write(self, change: Callable[[_Board], _T]) -> _T:
        """Apply ``change`` to the current version and save it, all under the lock."""
        with self._locked():
            board: _Board = self._read()
            result: _T = change(board)
            self._save(board)
            return result

    @contextmanager
    def _locked(self) -> Generator[None]:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock: Path = self._path.with_name(self._path.name + ".lock")
        with lock.open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _save(self, board: _Board) -> None:
        handle_fd, temp = tempfile.mkstemp(dir=self._path.parent, prefix=".fake-board-")
        try:
            with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
                json.dump(_serialize(board), handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self._path)
        except BaseException:
            os.unlink(temp)
            raise


def _next_id(board: _Board) -> int:
    return max(board.items, default=_FIRST_ID) + 1


def _take_fault(board: _Board) -> int | None:
    armed: int | None = board.fail_create_after_step
    board.fail_create_after_step = None
    return armed


# --- the file format ------------------------------------------------------------


def _serialize(board: _Board) -> dict[str, object]:
    out: dict[str, object] = {
        "version": _VERSION,
        "items": {str(item_id): asdict(item) for item_id, item in sorted(board.items.items())},
        "completed_prs": board.completed_prs,
        "fail_create_after_step": board.fail_create_after_step,
    }
    if board.states is not None:
        out["states"] = board.states
    return out


def _parse(text: str, path: Path) -> _Board:
    """Parse the file strictly: a malformed board fails loudly, naming the file."""
    try:
        raw: object = json.loads(text)
        data: dict[str, object] = _object(raw, "the board")
        version: object = data.get("version", _VERSION)
        if version != _VERSION:
            raise ValueError(f"version {version!r} is not {_VERSION}")
        states: object = data.get("states")
        items: dict[str, object] = _object(data.get("items", {}), "items")
        prs: dict[str, object] = _object(data.get("completed_prs", {}), "completed_prs")
        fault: object = data.get("fail_create_after_step")
        return _Board(
            states=None if states is None else _strs(states, "states"),
            items={_item_id(key): _parse_item(value, key) for key, value in items.items()},
            completed_prs={
                branch: _str(url, f"completed_prs[{branch!r}]") for branch, url in prs.items()
            },
            fail_create_after_step=None if fault is None else _int(fault, "fail_create_after_step"),
        )
    except (ValueError, TypeError) as exc:
        raise BoardValidationError(f"the fake board file {path} is malformed: {exc}") from exc


def _parse_item(raw: object, key: str) -> _Item:
    where: str = f"items[{key!r}]"
    data: dict[str, object] = _object(raw, where)
    state: object = data.get("state")
    parent: object = data.get("parent")
    origin: object = data.get("origin")
    return _Item(
        title=_str(data.get("title", ""), f"{where}.title"),
        state=None if state is None else _str(state, f"{where}.state"),
        body=_str(data.get("body", ""), f"{where}.body"),
        tags=_strs(data.get("tags", []), f"{where}.tags"),
        parent=None if parent is None else _int(parent, f"{where}.parent"),
        predecessors=[
            _int(link, f"{where}.predecessors")
            for link in _list(data.get("predecessors", []), f"{where}.predecessors")
        ],
        origin=None if origin is None else _str(origin, f"{where}.origin"),
        comments=_strs(data.get("comments", []), f"{where}.comments"),
    )


def _item_id(key: str) -> int:
    if not key.isdigit():
        raise ValueError(f"item id {key!r} is not a positive integer")
    return int(key)


def _object(raw: object, where: str) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise TypeError(f"{where} must be a JSON object, got {raw!r}")
    return cast("dict[str, object]", raw)


def _list(raw: object, where: str) -> list[object]:
    if not isinstance(raw, list):
        raise TypeError(f"{where} must be a JSON list, got {raw!r}")
    return cast("list[object]", raw)


def _strs(raw: object, where: str) -> list[str]:
    return [_str(value, where) for value in _list(raw, where)]


def _str(raw: object, where: str) -> str:
    if not isinstance(raw, str):
        raise TypeError(f"{where} must be a string, got {raw!r}")
    return raw


def _int(raw: object, where: str) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise TypeError(f"{where} must be an integer, got {raw!r}")
    return raw
