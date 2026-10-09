"""The registered fake provider's own behaviour: its file, seeding and locking (SQ3).

The ``BoardAccess`` behaviour is the contract suites' (``tests/contract``, where
the fake is the third shape). This file covers what is the fake's alone: it is
built by ``provider = "fake"``, it keeps its board in a file that outlives the
process, a test can seed it by writing that file, and concurrent processes
never corrupt it (ledger N16).
"""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from squadra.board import BoardValidationError, build_board
from squadra.config import SquadraConfig, load_config
from squadra.domain import IncrementRequest, Lifecycle, OriginRecord
from squadra.fake_board import FAKE_BOARD_FILE, JsonFileBoard, fake_board_path

_TOML: str = (
    "[board]\n"
    'provider = "fake"\n'
    'claim_scope = "whole-board"\n'
    "[board.states]\n"
    'queued = ["Backlog"]\n'
    'active = ["Doing"]\n'
    'done = ["Done"]\n'
    'withdrawn = ["Dropped"]\n'
)


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SquadraConfig:
    for var in ("FLEET_PROVIDER", "FLEET_TAG_PREFIX", "FLEET_BASE_BRANCH"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / "squadra.toml").write_text(_TOML, encoding="utf-8")
    return load_config(fleet_home=tmp_path)


def _seed(path: Path, board: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(board), encoding="utf-8")


def test_provider_fake_builds_a_board_on_the_file_under_fleet_home(
    config: SquadraConfig, tmp_path: Path
) -> None:
    board = build_board(config)
    assert isinstance(board, JsonFileBoard)
    assert fake_board_path(tmp_path) == tmp_path / FAKE_BOARD_FILE
    board.validate_config()  # a missing file is an empty board with the configured states
    assert board.items_in_state(Lifecycle.QUEUED) == ()
    assert board.items_with_origin() == ()
    assert not fake_board_path(tmp_path).exists()  # reads never create it


def test_the_board_outlives_the_object_that_wrote_it(config: SquadraConfig) -> None:
    """One process per CLI verb: a second build_board sees the first one's writes."""
    item_id: int = build_board(config).create_increment(
        IncrementRequest("A:I1", 200, (), "I1", "body")
    )
    again = build_board(config)
    assert again.item_state(item_id) is Lifecycle.QUEUED
    assert [record.origin for record in again.items_with_origin()] == ["A:I1"]


def test_a_test_seeds_the_board_by_writing_the_file(config: SquadraConfig, tmp_path: Path) -> None:
    _seed(
        fake_board_path(tmp_path),
        {
            "version": 1,
            "states": ["Backlog", "Doing", "Done", "Dropped"],
            "items": {
                "7": {"title": "I7", "state": "Doing", "tags": ["fleet:claimed"], "parent": 200},
                "8": {"title": "I8", "body": "b", "state": None, "origin": "A:I8"},
            },
            "completed_prs": {"feat/increment-7-x": "https://example.invalid/pr/7"},
        },
    )
    board = build_board(config)
    board.validate_config()
    assert [(item.item_id, item.tags) for item in board.items_in_state(Lifecycle.ACTIVE)] == [
        (7, ("fleet:claimed",))
    ]
    assert board.completed_pr_url("feat/increment-7-x") == "https://example.invalid/pr/7"
    assert board.items_with_origin() == (
        OriginRecord(8, "A:I8", None, (), "I8", "b", lifecycle=None),
    )


def test_validate_config_fails_on_a_board_state_the_map_leaves_out(
    config: SquadraConfig, tmp_path: Path
) -> None:
    _seed(fake_board_path(tmp_path), {"states": ["Backlog", "Doing", "Done", "Dropped", "Icebox"]})
    with pytest.raises(BoardValidationError, match="Icebox"):
        build_board(config).validate_config()


def test_validate_config_fails_on_a_configured_state_the_board_lacks(
    config: SquadraConfig, tmp_path: Path
) -> None:
    _seed(fake_board_path(tmp_path), {"states": ["Backlog", "Doing", "Done"]})
    with pytest.raises(BoardValidationError, match="Dropped"):
        build_board(config).validate_config()


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        '{"version": 2}',
        '{"items": {"x": {}}}',
        '{"items": {"1": {"parent": "200"}}}',
    ],
)
def test_a_malformed_file_fails_loudly_naming_it(
    config: SquadraConfig, tmp_path: Path, content: str
) -> None:
    path: Path = fake_board_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    with pytest.raises(BoardValidationError, match="fake-board.json is malformed"):
        build_board(config).items_in_state(Lifecycle.QUEUED)


def test_an_armed_fault_crosses_processes(config: SquadraConfig, tmp_path: Path) -> None:
    """A test arms the crash in its own process; the next CLI call's create stops."""
    _seed(fake_board_path(tmp_path), {"fail_create_after_step": 1})
    with pytest.raises(RuntimeError, match="after create step 1 of 3"):
        build_board(config).create_increment(IncrementRequest("A:I1", 200, (), "I1", ""))
    board = build_board(config)
    (record,) = board.items_with_origin()
    assert record.partial
    assert board.create_increment(IncrementRequest("A:I1", 200, (), "I1", "")) != record.item_id


def test_an_unknown_item_names_the_board_file(config: SquadraConfig) -> None:
    with pytest.raises(KeyError, match="no item 42 on the fake board"):
        build_board(config).item_links(42)


_WRITER: str = """
import sys
from pathlib import Path
from squadra.domain import IncrementRequest, Lifecycle
from squadra.fake_board import JsonFileBoard

states = {Lifecycle.QUEUED: ("Backlog",), Lifecycle.ACTIVE: ("Doing",), Lifecycle.DONE: ("Done",)}
board = JsonFileBoard(Path(sys.argv[1]), states=states)
for n in range(int(sys.argv[3])):
    board.create_increment(IncrementRequest(f"{sys.argv[2]}:{n}", 200, (1, 2), "t", "b"))
"""


def test_concurrent_processes_never_corrupt_the_file_or_share_an_id(tmp_path: Path) -> None:
    path: Path = tmp_path / FAKE_BOARD_FILE
    per_writer: int = 15
    writers: list[subprocess.Popen[bytes]] = [
        subprocess.Popen([sys.executable, "-c", _WRITER, str(path), name, str(per_writer)])
        for name in ("A", "B", "C")
    ]
    assert [writer.wait(timeout=60) for writer in writers] == [0, 0, 0]
    board = JsonFileBoard(
        path,
        states={
            Lifecycle.QUEUED: ("Backlog",),
            Lifecycle.ACTIVE: ("Doing",),
            Lifecycle.DONE: ("Done",),
        },
    )
    records: tuple[OriginRecord, ...] = board.items_with_origin()
    assert len(records) == 3 * per_writer
    assert len({record.item_id for record in records}) == 3 * per_writer
    assert all(record.lifecycle is Lifecycle.QUEUED for record in records)
    assert all(record.predecessors == (1, 2) for record in records)
