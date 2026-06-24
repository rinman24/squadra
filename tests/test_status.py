"""Unit tests for the fleet status-file convention (ADR-0007 addendum §3)."""

from collections.abc import Callable
import dataclasses
import json
from pathlib import Path
import threading

import pytest

from squadra import status as fleet_status
from squadra.status import (
    FleetStatus,
    FleetStatusError,
    StatusUpdate,
    add_worker,
    load,
    load_or_none,
    main,
    new_status,
    stamp_heartbeat,
    status_path,
    update,
    write,
)

# fleet_root, default_status, make_status
# are provided by tests/conftest.py


def test_write_then_load_round_trips(fleet_root: Path, default_status: FleetStatus) -> None:
    write(default_status, fleet_root)
    assert load(41, fleet_root) == default_status


def test_status_file_keys_match_addendum_schema(
    fleet_root: Path, default_status: FleetStatus
) -> None:
    write(default_status, fleet_root)
    data: dict[str, object] = json.loads(status_path(41, fleet_root).read_text())
    assert list(data) == [
        "issue_id",
        "runner_id",
        "branch",
        "worktree",
        "pr_url",
        "phase",
        "parked_state",
        "worker_roster",
        "started_at",
        "last_heartbeat",
        "attempt",
        "last_error",
    ]


def test_new_status_seeds_fresh_lifecycle_fields() -> None:
    status: FleetStatus = new_status(7, "runner-7-a2", "feat/slice-7-x", "/tmp/wt7", attempt=2)
    assert status.phase == "claiming"
    assert status.parked_state is None
    assert status.worker_roster == ()
    assert status.pr_url is None
    assert status.last_error is None
    assert status.attempt == 2
    assert status.started_at == status.last_heartbeat


def test_update_changes_only_requested_fields(
    fleet_root: Path, default_status: FleetStatus
) -> None:
    write(default_status, fleet_root)
    merged: FleetStatus = update(41, StatusUpdate(phase="tdd"), fleet_root)
    assert merged.phase == "tdd"
    assert merged.branch == default_status.branch
    assert merged.last_heartbeat == default_status.last_heartbeat
    assert load(41, fleet_root) == merged


def test_update_can_park_and_unpark(fleet_root: Path, default_status: FleetStatus) -> None:
    write(default_status, fleet_root)
    parked: FleetStatus = update(
        41,
        StatusUpdate(phase="parked", parked_state="awaiting-pr-approval", pr_url="https://pr/1"),
        fleet_root,
    )
    assert parked.parked_state == "awaiting-pr-approval"
    resumed: FleetStatus = update(41, StatusUpdate(phase="tdd", parked_state=None), fleet_root)
    assert resumed.phase == "tdd"
    assert resumed.parked_state is None
    assert resumed.pr_url == "https://pr/1"


def test_parked_phase_requires_parked_state(fleet_root: Path, default_status: FleetStatus) -> None:
    write(default_status, fleet_root)
    with pytest.raises(FleetStatusError, match="requires a parked_state"):
        update(41, StatusUpdate(phase="parked"), fleet_root)


def test_parked_state_requires_parked_phase(
    fleet_root: Path, make_status: Callable[..., FleetStatus]
) -> None:
    with pytest.raises(FleetStatusError, match="requires phase 'parked'"):
        write(make_status(parked_state="failed"), fleet_root)


def test_stamp_heartbeat_touches_only_last_heartbeat(
    fleet_root: Path, default_status: FleetStatus, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(default_status, fleet_root)
    monkeypatch.setattr(fleet_status, "_utcnow_iso", lambda: "2026-06-10T13:00:00+00:00")
    stamp_heartbeat(41, fleet_root)
    loaded: FleetStatus = load(41, fleet_root)
    assert loaded.last_heartbeat == "2026-06-10T13:00:00+00:00"
    assert (
        dataclasses.replace(loaded, last_heartbeat=default_status.last_heartbeat) == default_status
    )


def test_add_worker_appends_idempotently(fleet_root: Path, default_status: FleetStatus) -> None:
    write(default_status, fleet_root)
    add_worker(41, "task-73", fleet_root)
    add_worker(41, "task-73", fleet_root)
    add_worker(41, "task-74", fleet_root)
    assert load(41, fleet_root).worker_roster == ("task-73", "task-74")


def test_load_missing_raises_file_not_found(fleet_root: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load(404, fleet_root)


def test_load_or_none_returns_none_when_missing(fleet_root: Path) -> None:
    assert load_or_none(404, fleet_root) is None


def test_corrupt_file_raises_clear_error(fleet_root: Path) -> None:
    path: Path = status_path(41, fleet_root)
    path.parent.mkdir(parents=True)
    path.write_text("{nope", encoding="utf-8")
    with pytest.raises(FleetStatusError, match="not valid JSON"):
        load(41, fleet_root)


def test_unknown_keys_rejected(fleet_root: Path, default_status: FleetStatus) -> None:
    write(default_status, fleet_root)
    path: Path = status_path(41, fleet_root)
    data: dict[str, object] = json.loads(path.read_text())
    data["surprise"] = 1
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(FleetStatusError, match="surprise"):
        load(41, fleet_root)


def test_invalid_attempt_rejected(
    fleet_root: Path, make_status: Callable[..., FleetStatus]
) -> None:
    with pytest.raises(FleetStatusError, match="attempt"):
        write(make_status(attempt=0), fleet_root)


def test_interleaved_writers_lose_no_updates(fleet_root: Path, default_status: FleetStatus) -> None:
    write(default_status, fleet_root)

    def heartbeats() -> None:
        for _ in range(25):
            stamp_heartbeat(41, fleet_root)

    def add_workers(prefix: str) -> None:
        for index in range(25):
            add_worker(41, f"{prefix}-{index}", fleet_root)

    threads: list[threading.Thread] = [
        threading.Thread(target=heartbeats),
        threading.Thread(target=add_workers, args=("a",)),
        threading.Thread(target=add_workers, args=("b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    loaded: FleetStatus = load(41, fleet_root)
    assert len(loaded.worker_roster) == 50
    assert loaded.branch == default_status.branch


def test_cli_init_then_show_round_trips(
    fleet_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(
            [
                "init",
                "--issue-id",
                "9",
                "--runner-id",
                "runner-9-a1",
                "--branch",
                "feat/slice-9-x",
                "--worktree",
                "/tmp/wt9",
                "--fleet-root",
                str(fleet_root),
            ]
        )
        == 0
    )
    assert main(["show", "--issue-id", "9", "--fleet-root", str(fleet_root)]) == 0
    shown: dict[str, object] = json.loads(capsys.readouterr().out)
    assert shown["issue_id"] == 9
    assert shown["phase"] == "claiming"
    assert shown["attempt"] == 1


def test_cli_update_park_and_heartbeat(fleet_root: Path) -> None:
    main(
        [
            "init",
            "--issue-id",
            "9",
            "--runner-id",
            "runner-9-a1",
            "--branch",
            "feat/slice-9-x",
            "--worktree",
            "/tmp/wt9",
            "--fleet-root",
            str(fleet_root),
        ]
    )
    rc: int = main(
        [
            "update",
            "--issue-id",
            "9",
            "--phase",
            "parked",
            "--parked-state",
            "awaiting-pr-approval",
            "--pr-url",
            "https://pr/9",
            "--add-worker",
            "w1",
            "--fleet-root",
            str(fleet_root),
        ]
    )
    assert rc == 0
    loaded: FleetStatus = load(9, fleet_root)
    assert loaded.phase == "parked"
    assert loaded.parked_state == "awaiting-pr-approval"
    assert loaded.pr_url == "https://pr/9"
    assert loaded.worker_roster == ("w1",)
    assert main(["heartbeat", "--issue-id", "9", "--fleet-root", str(fleet_root)]) == 0


def test_cli_update_unparks_with_none(fleet_root: Path) -> None:
    main(
        [
            "init",
            "--issue-id",
            "9",
            "--runner-id",
            "runner-9-a1",
            "--branch",
            "feat/slice-9-x",
            "--worktree",
            "/tmp/wt9",
            "--fleet-root",
            str(fleet_root),
        ]
    )
    main(
        [
            "update",
            "--issue-id",
            "9",
            "--phase",
            "parked",
            "--parked-state",
            "needs-decision",
            "--fleet-root",
            str(fleet_root),
        ]
    )
    rc: int = main(
        [
            "update",
            "--issue-id",
            "9",
            "--phase",
            "tdd",
            "--parked-state",
            "none",
            "--fleet-root",
            str(fleet_root),
        ]
    )
    assert rc == 0
    loaded: FleetStatus = load(9, fleet_root)
    assert loaded.phase == "tdd"
    assert loaded.parked_state is None


def test_cli_rejects_unknown_phase(fleet_root: Path) -> None:
    with pytest.raises(SystemExit):
        main(
            [
                "update",
                "--issue-id",
                "9",
                "--phase",
                "bogus",
                "--fleet-root",
                str(fleet_root),
            ]
        )


def test_cli_update_with_no_fields_errors(
    fleet_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc: int = main(["update", "--issue-id", "9", "--fleet-root", str(fleet_root)])
    assert rc == 2
    assert "nothing to update" in capsys.readouterr().err


def test_cli_show_missing_status_returns_error(
    fleet_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc: int = main(["show", "--issue-id", "404", "--fleet-root", str(fleet_root)])
    assert rc == 2
    assert "no status file" in capsys.readouterr().err
