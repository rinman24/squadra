"""Unit tests for the unified ``flotilla`` CLI (ADR-0001 decision 3).

These tests stub every outward effect — ``status.main``, ``resolve_script``,
and the lazily-imported ``supervisor`` — so no real tmux / az / claude /
supervisor ever runs. They also assert the one structural invariant the
mid-migration state demands: importing :mod:`flotilla.cli` must NOT import
``flotilla.supervisor`` (the tick handler imports it lazily).
"""

from collections.abc import Sequence
from pathlib import Path
import subprocess
import sys
import tomllib
from typing import cast

import pytest

from flotilla import cli
import flotilla.board as board_module
import flotilla.supervisor as real_supervisor


def test_importing_cli_does_not_import_supervisor() -> None:
    # The supervisor is mid-migration; cli.py must import it lazily (in the tick
    # handler) so importing cli — for collection, `flotilla init`, or
    # `flotilla slice` — never drags the supervisor in. Checked in a clean
    # subprocess so an unrelated test's import cannot mask a regression.
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import flotilla.cli, sys; "
            "assert 'flotilla.supervisor' not in sys.modules, 'cli imported supervisor'",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


def test_init_writes_flotilla_toml_and_skill_templates(tmp_path: Path) -> None:
    rc: int = cli.main(["init", "--fleet-home", str(tmp_path)])
    assert rc == 0
    config_path: Path = tmp_path / "flotilla.toml"
    assert config_path.is_file()
    # init delegates to flotilla.scaffold, which also drops the consumer-owned
    # runner/cleanup skill templates alongside the config.
    assert (tmp_path / "runner-skill.md").is_file()
    assert (tmp_path / "cleanup-skill.md").is_file()

    parsed: dict[str, object] = tomllib.loads(config_path.read_text(encoding="utf-8"))
    board: dict[str, object] = _table(parsed, "board")
    pipeline: dict[str, object] = _table(parsed, "pipeline")

    assert set(board) >= {"provider", "base_branch", "tag_prefix", "parent_scope_ids"}
    assert board["provider"] == "ado"
    assert set(pipeline) >= {
        "branch_template",
        "worktree_dir",
        "runner_skill",
        "tdd_skill",
        "qa_skill",
        "cleanup_skill",
    }


def test_init_provider_flag_lands_in_toml(tmp_path: Path) -> None:
    cli.main(["init", "--fleet-home", str(tmp_path), "--provider", "github"])
    parsed: dict[str, object] = tomllib.loads(
        (tmp_path / "flotilla.toml").read_text(encoding="utf-8")
    )
    assert _table(parsed, "board")["provider"] == "github"


def test_init_skips_existing_without_force(tmp_path: Path) -> None:
    config_path: Path = tmp_path / "flotilla.toml"
    config_path.write_text("# pre-existing\n", encoding="utf-8")

    # Skipping an existing file is re-runnable, not an error (the scaffolder's
    # contract): rc is 0 and the original content is left untouched.
    rc: int = cli.main(["init", "--fleet-home", str(tmp_path)])
    assert rc == 0
    assert config_path.read_text(encoding="utf-8") == "# pre-existing\n"


def test_init_force_overwrites(tmp_path: Path) -> None:
    config_path: Path = tmp_path / "flotilla.toml"
    config_path.write_text("# pre-existing\n", encoding="utf-8")

    rc: int = cli.main(["init", "--fleet-home", str(tmp_path), "--force"])
    assert rc == 0
    assert "[board]" in config_path.read_text(encoding="utf-8")


def test_init_check_validates_against_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Stub the board seam so --check runs without a live az CLI. FLEET_EPIC_IDS
    # is set in the dev container, so an explicit --fleet-home keeps load_config
    # hermetic; we do not assert on parent_scope_ids here.
    validated: list[bool] = []

    class _OkBoard:
        def validate_config(self) -> None:
            validated.append(True)

    def _build(_config: object) -> _OkBoard:
        return _OkBoard()

    monkeypatch.setattr(board_module, "build_board", _build)

    rc: int = cli.main(["init", "--fleet-home", str(tmp_path), "--check"])
    assert rc == 0
    assert validated == [True]


def test_init_check_reports_validation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _BadBoard:
        def validate_config(self) -> None:
            raise board_module.BoardValidationError("state 'Doing' not found")

    def _build(_config: object) -> _BadBoard:
        return _BadBoard()

    monkeypatch.setattr(board_module, "build_board", _build)

    rc: int = cli.main(["init", "--fleet-home", str(tmp_path), "--check"])
    assert rc != 0
    assert "not found" in capsys.readouterr().err


def test_slice_show_delegates_to_status_main(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[Sequence[str] | None] = []

    def _fake_status_main(argv: Sequence[str] | None = None) -> int:
        recorded.append(argv)
        return 0

    monkeypatch.setattr(cli.status, "main", _fake_status_main)

    rc: int = cli.main(["slice", "show", "--issue-id", "5", "--fleet-root", "/tmp/x"])
    assert rc == 0
    assert recorded == [["show", "--issue-id", "5", "--fleet-root", "/tmp/x"]]


def test_slice_without_subcommand_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[Sequence[str] | None] = []

    def _fake_status_main(argv: Sequence[str] | None = None) -> int:
        called.append(argv)
        return 0

    monkeypatch.setattr(cli.status, "main", _fake_status_main)

    rc: int = cli.main(["slice"])
    assert rc != 0
    assert called == []


def test_tick_delegates_to_supervisor_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    # ``from flotilla import supervisor`` binds the already-imported submodule
    # attribute, so stub ``supervisor.main`` on the real module rather than
    # swapping sys.modules — this also proves no real tick (and no az call) runs.
    recorded: list[list[str]] = []

    def _fake_main(argv: Sequence[str] | None = None) -> int:
        recorded.append(list(argv or []))
        return 0

    monkeypatch.setattr(real_supervisor, "main", _fake_main)

    rc: int = cli.main(["tick", "--dry-run", "--fleet-home", "/tmp/y"])
    assert rc == 0
    assert recorded == [["--dry-run", "--fleet-home", "/tmp/y"]]


def test_fleetctl_subcommand_shells_to_script(monkeypatch: pytest.MonkeyPatch) -> None:
    execed: list[tuple[str, list[str], str | None]] = []

    def _fake_execvpe(file: str, argv: Sequence[str], env: dict[str, str]) -> None:
        execed.append((file, list(argv), env.get("FLEET_PYTHON")))

    def _fake_resolve(name: str) -> Path:
        return Path(f"/pkg/{name}")

    monkeypatch.setattr(cli, "resolve_script", _fake_resolve)
    monkeypatch.setattr(cli.os, "execvpe", _fake_execvpe)

    cli.main(["start", "-f"])
    assert execed
    file, argv, fleet_python = execed[0]
    assert file == "bash"
    assert argv == ["bash", "/pkg/fleetctl.sh", "start", "-f"]
    assert fleet_python == sys.executable


def test_fleetctl_missing_bash_returns_127(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_resolve(name: str) -> Path:
        return Path(f"/pkg/{name}")

    def _raise(file: str, argv: Sequence[str], env: dict[str, str]) -> None:
        raise OSError("bash not found")

    monkeypatch.setattr(cli, "resolve_script", _fake_resolve)
    monkeypatch.setattr(cli.os, "execvpe", _raise)

    rc: int = cli.main(["stop"])
    assert rc == 127


def test_deprecated_status_main_warns_and_delegates(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    recorded: list[Sequence[str] | None] = []

    def _fake_status_main(argv: Sequence[str] | None = None) -> int:
        recorded.append(argv)
        return 7

    monkeypatch.setattr(cli.status, "main", _fake_status_main)

    rc: int = cli.deprecated_status_main(["show", "--issue-id", "5"])
    assert rc == 7
    assert recorded == [["show", "--issue-id", "5"]]
    assert "deprecated" in capsys.readouterr().err


def test_no_command_prints_help_and_errors(capsys: pytest.CaptureFixture[str]) -> None:
    rc: int = cli.main([])
    assert rc == 2
    assert "flotilla" in capsys.readouterr().err


def _table(parsed: dict[str, object], name: str) -> dict[str, object]:
    """Return a top-level TOML table, asserting its shape for the type checker."""
    value: object = parsed[name]
    assert isinstance(value, dict)
    return cast("dict[str, object]", value)
