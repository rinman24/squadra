"""``squadra board {queue,withdraw,origins,scope,partial-items}`` end to end on the fake (SQ4, SQ5e, SQ8).

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


@pytest.mark.parametrize(
    ("scope_toml", "parent", "in_scope"),
    [
        ('claim_scope = "whole-board"\n', 1, True),
        ('claim_scope = "whole-board"\n', 2, True),
        ('claim_scope = "parents"\nparent_scope_ids = [1]\n', 1, True),
        ('claim_scope = "parents"\nparent_scope_ids = [1]\n', 2, False),
    ],
    ids=["whole-board-1", "whole-board-2", "parents-1-in", "parents-1-out"],
)
def test_scope_says_in_scope_exactly_when_queue_is_not_refused_for_scope(
    fleet_home: Path,
    capsys: pytest.CaptureFixture[str],
    scope_toml: str,
    parent: int,
    in_scope: bool,
) -> None:
    """claude-skills DB-D12 (SQ5e, Juval): ``scope`` and ``queue`` never disagree."""
    (fleet_home / "squadra.toml").write_text(
        '[board]\nprovider = "fake"\n' + scope_toml + _STATES_TOML, encoding="utf-8"
    )
    _seed(fleet_home, _SEEDED_ITEMS)
    before: bytes | None = _snapshot(fleet_home)

    rc, out, err = _main(capsys, "scope", "--parent", str(parent))

    assert (rc, err) == (0, "")  # a read: exit 0 whether in scope or not
    assert json.loads(out) == {"parent": parent, "in_claim_scope": in_scope}
    assert _snapshot(fleet_home) == before
    rc, out, err = _main(
        capsys, *_queue_args("A:NEW", parent=parent), "--body-file", str(fleet_home / "body.md")
    )
    refused_for_scope: bool = rc == 3 and "outside [board].claim_scope" in err
    assert refused_for_scope is not in_scope
    assert rc == (0 if in_scope else 3), err


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
        (["scope"], "squadra board scope: "),
        (["scope", "--parent", "x"], "squadra board scope: "),
        (["nonsense"], "squadra board: "),
    ],
    ids=[
        "no-verb",
        "no-parent",
        "bad-parent",
        "missing-body-file",
        "no-origin",
        "extra",
        "scope-no-parent",
        "scope-bad-parent",
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


@pytest.mark.parametrize(
    "argv", [["origins"], ["scope", "--parent", "200"]], ids=["origins", "scope"]
)
def test_a_config_error_exits_2(
    fleet_home: Path, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    (fleet_home / "squadra.toml").write_text(
        '[board]\nprovider = "fake"\n' + _STATES_TOML, encoding="utf-8"
    )  # no claim_scope: ConfigError, fail closed

    rc, out, err = _main(capsys, *argv)

    assert (rc, out) == (2, "")
    _assert_one_error_line(err, argv[0], "claim_scope")


def test_a_board_validation_error_exits_2(
    fleet_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fleet_home, _SEEDED_ITEMS, states=["Backlog", "Doing", "Done"])  # no "Dropped"
    before: bytes | None = _snapshot(fleet_home)

    rc, out, err = _main(capsys, "withdraw", "--origin", "A:I1")

    assert (rc, out) == (2, "")
    _assert_one_error_line(err, "withdraw", "Dropped")
    assert _snapshot(fleet_home) == before


def test_scope_runs_validate_config_before_answering(
    fleet_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fleet_home, _SEEDED_ITEMS, states=["Backlog", "Doing", "Done"])  # no "Dropped"
    before: bytes | None = _snapshot(fleet_home)

    rc, out, err = _main(capsys, "scope", "--parent", "200")

    assert (rc, out) == (2, "")
    _assert_one_error_line(err, "scope", "Dropped")
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


def test_scope_exits_1_on_a_provider_failure(
    fleet_home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    (fleet_home / "squadra.toml").write_text(
        '[board]\nprovider = "ado"\nclaim_scope = "whole-board"\n', encoding="utf-8"
    )

    def az_down(_self: AzCliAdo) -> None:
        raise RuntimeError("az: connection refused")

    monkeypatch.setattr(AzCliAdo, "validate_config", az_down)

    rc, out, err = _main(capsys, "scope", "--parent", "200")

    assert (rc, out) == (1, "")
    _assert_one_error_line(err, "scope", "RuntimeError", "connection refused")


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


# --- partial-items: queue's N14 verdict before any write (SQ8, ledger N31) -------------

# Row R depends on P, an Increment, and on OTHER, a partial item, so a read that
# failed to map OTHER's Origin to its id would refuse what queue finishes once
# that link is on R. R's create is k = 3 + 2 writes: create, parent, P, OTHER, commit.
_P_ID: int = 1001
_OTHER_ID: int = 1002
_R_ID: int = 1003
_R_STEPS: int = 5
_PARTIAL_OTHER: dict[str, object] = {
    "title": "O",
    "body": "b",
    "state": None,
    "parent": 200,
    "origin": "A:OTHER",
}
_P_AND_OTHER: dict[str, dict[str, object]] = {
    str(_P_ID): {"title": "P", "body": "b", "state": "Backlog", "parent": 200, "origin": "A:P"},
    str(_OTHER_ID): _PARTIAL_OTHER,
}
# How design-to-board would queue each predecessor it does not see in `origins`.
_PREDECESSOR_TITLES: dict[str, str] = {"A:OTHER": "O", "A:NEW": "N"}
_AGREE: dict[str, object] = {
    "origin": "A:R",
    "parent": 200,
    "predecessor_origins": ["A:P", "A:OTHER"],
    "title": "R",
    "body": "b",
}
# (plan, the request, the first write j after which queue refuses it, and why).
_PLANS: list[tuple[str, dict[str, object], int | None, str | None]] = [
    ("agree", _AGREE, None, None),  # also maps A:OTHER, a partial item, to its id
    ("title-differs", {**_AGREE, "title": "R'"}, 1, "title"),
    ("body-differs", {**_AGREE, "body": "c"}, 1, "body"),
    ("parent-differs", {**_AGREE, "parent": 201}, 2, "parent"),
    ("p-left-out", {**_AGREE, "predecessor_origins": ["A:OTHER"]}, 3, "predecessors"),
    ("other-left-out", {**_AGREE, "predecessor_origins": ["A:P"]}, 4, "predecessors"),
    (
        "new-predecessor-not-on-the-board",
        {**_AGREE, "predecessor_origins": ["A:P", "A:OTHER", "A:NEW"]},
        None,
        None,
    ),
]


def _write_requests(home: Path, requests: object) -> str:
    path: Path = home / "requests.json"
    path.write_text(json.dumps(requests), encoding="utf-8")
    return str(path)


def _queue_or_fail(capsys: pytest.CaptureFixture[str], argv: list[str]) -> int:
    rc, out, err = _main(capsys, *argv)
    assert (rc, err) == (0, ""), err
    item_id: int = json.loads(out)["item_id"]
    return item_id


@pytest.mark.parametrize("after_step", range(1, _R_STEPS))
@pytest.mark.parametrize(
    ("request_", "refuses_from", "field"),
    [plan[1:] for plan in _PLANS],
    ids=[plan[0] for plan in _PLANS],
)
def test_partial_items_says_refuses_exactly_when_queue_refuses_with_that_message(  # noqa: PLR0913
    fleet_home: Path,
    capsys: pytest.CaptureFixture[str],
    after_step: int,
    request_: dict[str, object],
    refuses_from: int | None,
    field: str | None,
) -> None:
    """claude-skills DB-D13 (SQ8, Juval): the read and ``queue`` share N14's one predicate."""
    (fleet_home / "squadra.toml").write_text(
        '[board]\nprovider = "fake"\nclaim_scope = "parents"\nparent_scope_ids = [200, 201]\n'
        + _STATES_TOML,
        encoding="utf-8",
    )
    _seed(fleet_home, _P_AND_OTHER, fail_create_after_step=after_step)
    r_argv: list[str] = [*_queue_args("A:R", title="R"), "--body-file", str(fleet_home / "body.md")]
    rc, _, err = _main(capsys, *r_argv, "--predecessor", str(_P_ID), "--predecessor", "1002")
    assert rc == 1, err  # a real create, stopped after write `after_step`
    assert _items(fleet_home)[str(_R_ID)]["state"] is None
    before: bytes | None = _snapshot(fleet_home)

    rc, out, err = _main(
        capsys, "partial-items", "--requests-file", _write_requests(fleet_home, [request_])
    )

    assert (rc, err) == (0, "")  # a refusing verdict is an answer
    assert _snapshot(fleet_home) == before  # a read: the file is byte-identical
    other, r = json.loads(out)["partial_items"]
    assert other == {
        "item_id": _OTHER_ID,
        "origin": "A:OTHER",
        "queue": None,
        "disagrees_on": [],
        "refusal": None,
    }
    refuses: bool = refuses_from is not None and after_step >= refuses_from
    assert (r["item_id"], r["origin"]) == (_R_ID, "A:R")
    assert r["queue"] == ("refuses" if refuses else "finishes")
    assert r["disagrees_on"] == ([field] if refuses else [])
    assert (r["refusal"] is None) is not refuses

    # Then as design-to-board would: queue each predecessor `origins` does not
    # show, then queue R with the ids mapped.
    rc, out, _ = _main(capsys, "origins")
    ids: dict[str, int] = {o: entry["item_id"] for o, entry in json.loads(out).items()}
    predecessors: list[str] = cast("list[str]", request_["predecessor_origins"])
    for origin in predecessors:
        if origin not in ids:
            argv: list[str] = _queue_args(origin, title=_PREDECESSOR_TITLES[origin])
            ids[origin] = _queue_or_fail(
                capsys, [*argv, "--body-file", str(fleet_home / "body.md")]
            )
    (fleet_home / "r-body.md").write_text(cast("str", request_["body"]), encoding="utf-8")
    argv = [
        *_queue_args("A:R", parent=cast("int", request_["parent"])),
        "--title",
        cast("str", request_["title"]),
        "--body-file",
        str(fleet_home / "r-body.md"),
    ]
    for origin in predecessors:
        argv += ["--predecessor", str(ids[origin])]
    rc, out, err = _main(capsys, *argv)

    if refuses:
        assert (rc, out) == (3, "")
        assert err == f"squadra board queue: {r['refusal']}\n"  # byte for byte
    else:
        assert (rc, json.loads(out), err) == (0, {"item_id": _R_ID}, "")


def test_partial_items_gives_no_entry_for_a_request_n14_does_not_judge(
    fleet_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The fence: an A3 refusal, a withdrawn Origin or an absent one is not this read's answer."""
    _seed(fleet_home, {**_SEEDED_ITEMS, "1005": {**_PARTIAL_OTHER, "origin": "A:P"}})
    differs: dict[str, object] = {**_AGREE, "origin": "A:I1", "title": "different"}
    requests: list[dict[str, object]] = [
        differs,  # an Increment with different arguments: queue refuses it (A3)
        {**_AGREE, "origin": "A:I3"},  # withdrawn
        {**_AGREE, "origin": "A:NONE"},  # on no item
    ]

    rc, out, err = _main(
        capsys, "partial-items", "--requests-file", _write_requests(fleet_home, requests)
    )

    assert (rc, err) == (0, "")
    assert json.loads(out) == {
        "partial_items": [
            {"item_id": 1005, "origin": "A:P", "queue": None, "disagrees_on": [], "refusal": None}
        ]
    }
    rc, _, err = _main(
        capsys, *_queue_args("A:I1", title="different"), "--body-file", str(fleet_home / "body.md")
    )
    assert rc == 3, err  # queue does refuse it, by A3, not N14


def test_partial_items_with_no_requests_lists_every_partial_item_by_id(
    fleet_home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(
        fleet_home,
        {
            "1007": {**_PARTIAL_OTHER, "origin": "B:I9"},
            **_SEEDED_ITEMS,
            "1005": {**_PARTIAL_OTHER, "origin": "A:I5"},
        },
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO("[]"))

    rc, out, err = _main(capsys, "partial-items", "--requests-file", "-")

    assert (rc, err) == (0, "")
    assert json.loads(out) == {
        "partial_items": [
            {"item_id": 1005, "origin": "A:I5", "queue": None, "disagrees_on": [], "refusal": None},
            {"item_id": 1007, "origin": "B:I9", "queue": None, "disagrees_on": [], "refusal": None},
        ]
    }


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("[{", "Expecting"),
        ('{"origin": "A:R"}', "expected a JSON list"),
        ("[1]", "request 0 is not a JSON object"),
        (json.dumps([{k: v for k, v in _AGREE.items() if k != "body"}]), "missing ['body']"),
        (json.dumps([{**_AGREE, "predecessors": [1001]}]), "extra ['predecessors']"),
        (json.dumps([{**_AGREE, "parent": True}]), "parent must be an integer"),
        (json.dumps([{**_AGREE, "parent": "200"}]), "parent must be an integer"),
        (json.dumps([{**_AGREE, "predecessor_origins": [1001]}]), "list of strings"),
        (json.dumps([{**_AGREE, "title": None}]), "title must be a string"),
        (json.dumps([_AGREE, {**_AGREE, "title": "again"}]), '"A:R" is requested twice'),
    ],
    ids=[
        "bad-json",
        "not-a-list",
        "not-an-object",
        "missing-key",
        "extra-key",
        "bool-parent",
        "string-parent",
        "predecessor-id",
        "null-title",
        "origin-twice",
    ],
)
def test_partial_items_exits_2_on_a_bad_request_list_before_any_board_read(
    fleet_home: Path, capsys: pytest.CaptureFixture[str], text: str, fragment: str
) -> None:
    (fleet_home / "requests.json").write_text(text, encoding="utf-8")
    (fleet_home / "squadra.toml").write_text("", encoding="utf-8")  # a board read would exit 2 too

    rc, out, err = _main(
        capsys, "partial-items", "--requests-file", str(fleet_home / "requests.json")
    )

    assert (rc, out) == (2, "")
    _assert_one_error_line(err, "partial-items", "--requests-file", fragment)
    assert "claim_scope" not in err  # usage first, before config is loaded


@pytest.mark.parametrize(
    "argv",
    [["partial-items"], ["partial-items", "--requests-file", "/nonexistent/requests.json"]],
    ids=["no-requests-file", "unreadable-requests-file"],
)
def test_partial_items_usage_errors_exit_2(
    fleet_home: Path, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    rc, out, err = _main(capsys, *argv)

    assert (rc, out) == (2, "")
    _assert_one_error_line(err, "partial-items", "--requests-file")
    assert not fake_board_path(fleet_home).exists()


def test_partial_items_exits_2_on_a_config_error(
    fleet_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (fleet_home / "squadra.toml").write_text(
        '[board]\nprovider = "fake"\n' + _STATES_TOML, encoding="utf-8"
    )  # no claim_scope: ConfigError, fail closed

    rc, out, err = _main(
        capsys, "partial-items", "--requests-file", _write_requests(fleet_home, [])
    )

    assert (rc, out) == (2, "")
    _assert_one_error_line(err, "partial-items", "claim_scope")


def test_partial_items_runs_validate_config_before_answering(
    fleet_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fleet_home, _SEEDED_ITEMS, states=["Backlog", "Doing", "Done"])  # no "Dropped"
    before: bytes | None = _snapshot(fleet_home)

    rc, out, err = _main(
        capsys, "partial-items", "--requests-file", _write_requests(fleet_home, [])
    )

    assert (rc, out) == (2, "")
    _assert_one_error_line(err, "partial-items", "Dropped")
    assert _snapshot(fleet_home) == before


def test_partial_items_exits_3_only_on_a_board_level_duplicate_origin(
    fleet_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _seed(fleet_home, {**_SEEDED_ITEMS, "1005": {**_PARTIAL_OTHER, "origin": "A:I1"}})
    before: bytes | None = _snapshot(fleet_home)

    rc, out, err = _main(
        capsys, "partial-items", "--requests-file", _write_requests(fleet_home, [])
    )

    assert (rc, out) == (3, "")
    _assert_one_error_line(err, "partial-items", "1001", "1005")
    assert _snapshot(fleet_home) == before


def test_partial_items_exits_1_on_a_provider_failure(
    fleet_home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    (fleet_home / "squadra.toml").write_text(
        '[board]\nprovider = "ado"\nclaim_scope = "whole-board"\n', encoding="utf-8"
    )

    def no_live_az(_self: AzCliAdo) -> None:
        return None

    monkeypatch.setattr(AzCliAdo, "validate_config", no_live_az)

    rc, out, err = _main(
        capsys, "partial-items", "--requests-file", _write_requests(fleet_home, [])
    )

    assert (rc, out) == (1, "")
    _assert_one_error_line(err, "partial-items", "NotImplementedError")
