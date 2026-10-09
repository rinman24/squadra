"""``squadra board {queue,withdraw,origins}`` end to end on ``provider = "fake"`` (SQ4).

Every test runs the real CLI (``cli.main`` in-process, or ``python -m
squadra.cli`` in a subprocess, one process per verb as design-to-board calls
it) against a seeded ``<FLEET_HOME>/.squadra/fake-board.json``. Nothing is
stubbed but the ADO adapter's live config check. The spec is the CLI surface
in ``docs/board-writes/verb-contract.md``: one JSON document on stdout, one
``squadra board <verb>:`` line on stderr per error, exit 0 / 1 / 2 / 3, and a
refused verb leaves the board file byte-identical.
"""

from collections.abc import Mapping
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import cast

import pytest

from squadra import cli
from squadra.board import AzCliAdo
from squadra.fake_board import fake_board_path

_STATES_TOML: str = (
    "[board.states]\n"
    'queued = ["Backlog"]\n'
    'active = ["Doing"]\n'
    'done = ["Done"]\n'
    'withdrawn = ["Dropped"]\n'
)
_TOML: str = (
    '[board]\nprovider = "fake"\nclaim_scope = "parents"\nparent_scope_ids = [200]\n' + _STATES_TOML
)
_SCRUBBED_ENV: tuple[str, ...] = ("FLEET_PROVIDER", "FLEET_TAG_PREFIX", "FLEET_BASE_BRANCH")

# One item per bucket in scope, one out of scope (parent 300).
_SEEDED_ITEMS: dict[str, dict[str, object]] = {
    "1001": {"title": "I1", "body": "b", "state": "Backlog", "parent": 200, "origin": "A:I1"},
    "1002": {"title": "I2", "body": "b", "state": "Doing", "parent": 200, "origin": "A:I2"},
    "1003": {"title": "I3", "body": "b", "state": "Dropped", "parent": 200, "origin": "A:I3"},
    "1004": {"title": "O", "body": "b", "state": "Backlog", "parent": 300, "origin": "A:OUT"},
}


@pytest.fixture
def fleet_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A target repo naming ``provider = "fake"`` under ``claim_scope = "parents"`` [200]."""
    for var in _SCRUBBED_ENV:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("FLEET_HOME", str(tmp_path))
    (tmp_path / "squadra.toml").write_text(_TOML, encoding="utf-8")
    (tmp_path / "body.md").write_text("b", encoding="utf-8")
    return tmp_path


def _seed(home: Path, items: Mapping[str, Mapping[str, object]], **extra: object) -> None:
    path: Path = fake_board_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "items": items, **extra}), encoding="utf-8")


def _items(home: Path) -> dict[str, dict[str, object]]:
    board: dict[str, object] = json.loads(fake_board_path(home).read_text(encoding="utf-8"))
    return cast("dict[str, dict[str, object]]", board["items"])


def _set_items(home: Path, items: dict[str, dict[str, object]]) -> None:
    """Change the board from the test's own process, between two CLI calls (DB-D7)."""
    path: Path = fake_board_path(home)
    board: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    board["items"] = items
    path.write_text(json.dumps(board), encoding="utf-8")


def _snapshot(home: Path) -> bytes | None:
    path: Path = fake_board_path(home)
    return path.read_bytes() if path.exists() else None


def _main(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    rc: int = cli.main(["board", *argv])
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def _proc(home: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    env: dict[str, str] = {k: v for k, v in os.environ.items() if k not in _SCRUBBED_ENV}
    env["FLEET_HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-m", "squadra.cli", "board", *argv],
        capture_output=True,
        text=True,
        check=False,
        cwd=home,
        env=env,
    )


def _queue_args(origin: str, parent: int = 200, title: str = "I1") -> list[str]:
    return ["queue", "--origin", origin, "--parent", str(parent), "--title", title]


def _assert_one_error_line(err: str, verb: str, *fragments: str) -> None:
    lines: list[str] = err.splitlines()
    assert len(lines) == 1, err
    assert lines[0].startswith(f"squadra board {verb}: "), err
    for fragment in fragments:
        assert fragment in lines[0], err


# --- exit 0 ---------------------------------------------------------------------------


def test_queue_lands_a_queued_item_and_an_identical_retry_returns_it(
    fleet_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    body: Path = fleet_home / "body.md"
    body.write_text("line one\nline two\n", encoding="utf-8")
    argv: list[str] = [
        *_queue_args("A:I1"),
        "--predecessor",
        "150",
        "--predecessor",
        "151",
        "--body-file",
        str(body),
    ]

    rc, out, err = _main(capsys, *argv)

    assert (rc, err) == (0, "")
    assert json.loads(out) == {"item_id": 1001}
    item: dict[str, object] = _items(fleet_home)["1001"]
    assert item["state"] == "Backlog"
    assert (item["origin"], item["parent"], item["title"]) == ("A:I1", 200, "I1")
    assert item["predecessors"] == [150, 151]
    assert item["body"] == "line one\nline two\n"  # exactly as given, never stripped

    before: bytes | None = _snapshot(fleet_home)
    rc, out, err = _main(capsys, *argv)
    assert (rc, json.loads(out), err) == (0, {"item_id": 1001}, "")
    assert _snapshot(fleet_home) == before  # A3: an identical retry writes nothing


def test_queue_reads_the_body_from_stdin(
    fleet_home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("from stdin\n"))

    rc, out, _ = _main(capsys, *_queue_args("A:I1"), "--body-file", "-")

    assert (rc, json.loads(out)) == (0, {"item_id": 1001})
    assert _items(fleet_home)["1001"]["body"] == "from stdin\n"


def test_withdraw_moves_a_queued_increment_and_a_repeat_writes_nothing(
    fleet_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fleet_home, _SEEDED_ITEMS)

    rc, out, err = _main(capsys, "withdraw", "--origin", "A:I1")

    assert (rc, err) == (0, "")
    assert json.loads(out) == {"item_id": 1001, "lifecycle": "withdrawn"}
    assert _items(fleet_home)["1001"]["state"] == "Dropped"
    before: bytes | None = _snapshot(fleet_home)
    rc, out, _ = _main(capsys, "withdraw", "--origin", "A:I1")
    assert (rc, json.loads(out)) == (0, {"item_id": 1001, "lifecycle": "withdrawn"})
    assert _snapshot(fleet_home) == before


def test_origins_reports_every_bucket_and_names_partial_items_on_stderr(
    fleet_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    partial: dict[str, object] = {"title": "P", "body": "b", "state": None, "origin": "A:P"}
    _seed(fleet_home, {**_SEEDED_ITEMS, "1005": partial})
    before: bytes | None = _snapshot(fleet_home)

    rc, out, err = _main(capsys, "origins")

    assert rc == 0
    assert json.loads(out) == {
        "A:I1": {"item_id": 1001, "parent": 200, "lifecycle": "queued", "in_claim_scope": True},
        "A:I2": {"item_id": 1002, "parent": 200, "lifecycle": "active", "in_claim_scope": True},
        "A:I3": {"item_id": 1003, "parent": 200, "lifecycle": "withdrawn", "in_claim_scope": True},
        "A:OUT": {"item_id": 1004, "parent": 300, "lifecycle": "queued", "in_claim_scope": False},
    }
    _assert_one_error_line(err, "origins", "item 1005", '"A:P"')
    assert _snapshot(fleet_home) == before


# --- exit 3: refused by a rule, nothing written --------------------------------------


@pytest.mark.parametrize(
    ("argv", "verb", "fragment"),
    [
        (_queue_args("A:NEW", parent=300), "queue", "outside [board].claim_scope"),
        (_queue_args("A:I1", title="different"), "queue", "A:I1"),
        (_queue_args("A:I3", title="I3"), "queue", "A:I3"),
        (["withdraw", "--origin", "A:I2"], "withdraw", ""),
        (["withdraw", "--origin", "A:NONE"], "withdraw", "no increment on the board carries"),
        (["withdraw", "--origin", "A:OUT"], "withdraw", "outside [board].claim_scope"),
    ],
    ids=[
        "queue-parent-out-of-scope",
        "queue-differing-arguments",
        "queue-withdrawn-origin",
        "withdraw-active",
        "withdraw-unknown-origin",
        "withdraw-out-of-scope",
    ],
)
def test_a_refused_verb_exits_3_and_leaves_the_file_byte_identical(
    fleet_home: Path,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    verb: str,
    fragment: str,
) -> None:
    _seed(fleet_home, _SEEDED_ITEMS)
    before: bytes | None = _snapshot(fleet_home)
    body: list[str] = ["--body-file", str(fleet_home / "body.md")] if verb == "queue" else []

    rc, out, err = _main(capsys, *argv, *body)

    assert (rc, out) == (3, "")
    _assert_one_error_line(err, verb, fragment)
    assert _snapshot(fleet_home) == before


# --- exit 2: usage or configuration ---------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "prefix"),
    [
        ([], "squadra board: "),
        (
            ["queue", "--origin", "A:I1", "--title", "t", "--body-file", "-"],
            "squadra board queue: ",
        ),
        (
            ["queue", "--origin", "A", "--parent", "x", "--title", "t", "--body-file", "-"],
            "squadra board queue: ",
        ),
        ([*_queue_args("A:I1"), "--body-file", "missing.md"], "squadra board queue: "),
        (["withdraw"], "squadra board withdraw: "),
        (["origins", "--bogus"], "squadra board origins: "),
        (["nonsense"], "squadra board: "),
    ],
    ids=[
        "no-verb",
        "no-parent",
        "bad-parent",
        "missing-body-file",
        "no-origin",
        "extra",
        "bad-verb",
    ],
)
def test_a_usage_error_exits_2_on_one_prefixed_line_without_touching_the_board(
    fleet_home: Path, capsys: pytest.CaptureFixture[str], argv: list[str], prefix: str
) -> None:
    rc, out, err = _main(capsys, *argv)

    assert (rc, out) == (2, "")
    assert len(err.splitlines()) == 1, err
    assert err.startswith(prefix), err
    assert not fake_board_path(fleet_home).exists()


def test_a_config_error_exits_2(fleet_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (fleet_home / "squadra.toml").write_text(
        '[board]\nprovider = "fake"\n' + _STATES_TOML, encoding="utf-8"
    )  # no claim_scope: ConfigError, fail closed

    rc, out, err = _main(capsys, "origins")

    assert (rc, out) == (2, "")
    _assert_one_error_line(err, "origins", "claim_scope")


def test_a_board_validation_error_exits_2(
    fleet_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fleet_home, _SEEDED_ITEMS, states=["Backlog", "Doing", "Done"])  # no "Dropped"
    before: bytes | None = _snapshot(fleet_home)

    rc, out, err = _main(capsys, "withdraw", "--origin", "A:I1")

    assert (rc, out) == (2, "")
    _assert_one_error_line(err, "withdraw", "Dropped")
    assert _snapshot(fleet_home) == before


# --- exit 1: anything else ------------------------------------------------------------


def test_ado_write_primitives_exit_1(
    fleet_home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    (fleet_home / "squadra.toml").write_text(
        '[board]\nprovider = "ado"\nclaim_scope = "whole-board"\n', encoding="utf-8"
    )

    def no_live_az(_self: AzCliAdo) -> None:
        return None

    monkeypatch.setattr(AzCliAdo, "validate_config", no_live_az)

    rc, out, err = _main(capsys, "origins")

    assert (rc, out) == (1, "")
    _assert_one_error_line(err, "origins", "NotImplementedError")


def test_a_crashed_queue_exits_1_and_a_retry_in_a_second_process_finishes_it(
    fleet_home: Path,
) -> None:
    _seed(fleet_home, {}, fail_create_after_step=2)  # create, parent, then the crash
    argv: list[str] = [*_queue_args("A:I1"), "--body-file", "body.md"]

    crashed = _proc(fleet_home, *argv)

    assert (crashed.returncode, crashed.stdout) == (1, "")
    _assert_one_error_line(crashed.stderr, "queue", "InjectedCrashError")
    assert _items(fleet_home)["1001"]["state"] is None  # left partial, in no bucket
    listed = _proc(fleet_home, "origins")
    assert (listed.returncode, json.loads(listed.stdout)) == (0, {})
    _assert_one_error_line(listed.stderr, "origins", "item 1001", '"A:I1"')

    retried = _proc(fleet_home, *argv)

    assert (retried.returncode, json.loads(retried.stdout), retried.stderr) == (
        0,
        {"item_id": 1001},
        "",
    )
    assert _items(fleet_home)["1001"]["state"] == "Backlog"
    assert set(_items(fleet_home)) == {"1001"}


# --- DB-D7: the board changes between two calls, from another process -----------------


def test_each_call_sees_the_board_another_process_left(fleet_home: Path) -> None:
    """claude-skills DB-D7: design-to-board's F drives the fake this way between verbs."""
    queued = _proc(fleet_home, *_queue_args("A:I1"), "--body-file", "body.md")
    assert (queued.returncode, json.loads(queued.stdout)) == (0, {"item_id": 1001})

    for state, lifecycle in (("Doing", "active"), ("Done", "done")):
        items: dict[str, dict[str, object]] = _items(fleet_home)
        items["1001"]["state"] = state
        _set_items(fleet_home, items)

        listed = _proc(fleet_home, "origins")
        assert listed.returncode == 0, listed.stderr
        assert json.loads(listed.stdout)["A:I1"]["lifecycle"] == lifecycle
        before: bytes | None = _snapshot(fleet_home)
        refused = _proc(fleet_home, "withdraw", "--origin", "A:I1")
        assert refused.returncode == 3, refused.stderr
        _assert_one_error_line(refused.stderr, "withdraw")
        assert _snapshot(fleet_home) == before

    items = _items(fleet_home)
    items["1002"] = {"title": "I1", "body": "b", "state": "Backlog", "parent": 200}
    items["1002"]["origin"] = "A:I1"  # a second item carrying an existing Origin
    _set_items(fleet_home, items)
    before = _snapshot(fleet_home)

    for argv in (
        ["origins"],
        [*_queue_args("A:I1"), "--body-file", "body.md"],
        ["withdraw", "--origin", "A:I1"],
    ):
        duplicate = _proc(fleet_home, *argv)
        assert (duplicate.returncode, duplicate.stdout) == (3, ""), duplicate.stderr
        _assert_one_error_line(duplicate.stderr, argv[0], "1001", "1002")
        assert _snapshot(fleet_home) == before
