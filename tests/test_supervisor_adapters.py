"""Unit tests for the supervisor's tmux launcher + cleanup adapters (no tmux/claude).

The board adapter (``AzCliAdo``) lives in :mod:`flotilla.board` and is covered by
``tests/test_board.py``; this module covers the supervisor-owned process seams.
"""

from collections.abc import Sequence
from pathlib import Path

from flotilla.config import DEFAULT_QA_SKILL, DEFAULT_RUNNER_SKILL, DEFAULT_TDD_SKILL
from flotilla.constants import FLEET_EFFORT, FLEET_MODEL, HEARTBEAT_INTERVAL_SECONDS
from flotilla.supervisor import ClaudeCleanup, TmuxLauncher

# fleet_root is provided by tests/conftest.py


class _RecordingTmuxRunner:
    """Configurable exit codes per tmux subcommand; records every call."""

    def __init__(self, exit_codes: dict[str, int]) -> None:
        self.exit_codes = exit_codes
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> int:
        self.calls.append(list(args))
        return self.exit_codes.get(args[1], 0)


def test_launcher_creates_the_session_on_first_launch(fleet_root: Path) -> None:
    runner = _RecordingTmuxRunner({"has-session": 1})
    launcher = TmuxLauncher(Path("/repo"), fleet_root, runner)
    assert launcher.launch(41, "feat/slice-41-x", 1) is True
    new_session: list[str] = runner.calls[-1]
    assert new_session[1] == "new-session"
    assert ["-d", "-s", "fleet", "-n", "grid"] == new_session[2:7]
    command: str = new_session[-1]
    # runner-wrap.sh is resolved from the installed package data (importlib.
    # resources), NOT a path relative to FLEET_HOME (the repo flotilla operates
    # on); only the trailing invocation is stable across install layouts.
    assert "/_scripts/runner-wrap.sh 41 feat/slice-41-x 1" in command
    assert f"FLEET_ROOT={fleet_root}" in command
    assert "FLEET_HOME=/repo" in command


def test_launcher_defaults_the_heartbeat_interval_to_the_constant(fleet_root: Path) -> None:
    runner = _RecordingTmuxRunner({"has-session": 1})
    launcher = TmuxLauncher(Path("/repo"), fleet_root, runner)
    assert launcher.launch(45, "feat/slice-45-v", 1) is True
    command: str = runner.calls[-1][-1]
    assert f"FLEET_HEARTBEAT_INTERVAL_SECONDS={HEARTBEAT_INTERVAL_SECONDS} " in command


def test_launcher_injects_an_explicit_heartbeat_interval_into_the_pane_env(
    fleet_root: Path,
) -> None:
    runner = _RecordingTmuxRunner({"has-session": 1})
    launcher = TmuxLauncher(Path("/repo"), fleet_root, runner, heartbeat_interval_seconds=7)
    assert launcher.launch(46, "feat/slice-46-u", 1) is True
    command: str = runner.calls[-1][-1]
    assert "FLEET_HEARTBEAT_INTERVAL_SECONDS=7 " in command


def test_launcher_defaults_the_model_and_effort_to_the_constants(fleet_root: Path) -> None:
    runner = _RecordingTmuxRunner({"has-session": 1})
    launcher = TmuxLauncher(Path("/repo"), fleet_root, runner)
    assert launcher.launch(47, "feat/slice-47-t", 1) is True
    command: str = runner.calls[-1][-1]
    assert f"FLEET_MODEL={FLEET_MODEL} " in command
    assert f"FLEET_EFFORT={FLEET_EFFORT} " in command


def test_launcher_injects_an_explicit_model_and_effort_into_the_pane_env(
    fleet_root: Path,
) -> None:
    runner = _RecordingTmuxRunner({"has-session": 1})
    launcher = TmuxLauncher(
        Path("/repo"), fleet_root, runner, model="claude-sonnet-4-6", effort="medium"
    )
    assert launcher.launch(48, "feat/slice-48-s", 1) is True
    command: str = runner.calls[-1][-1]
    assert "FLEET_MODEL=claude-sonnet-4-6 " in command
    assert "FLEET_EFFORT=medium " in command


def test_launcher_defaults_the_skill_names_to_the_config_defaults(fleet_root: Path) -> None:
    runner = _RecordingTmuxRunner({"has-session": 1})
    launcher = TmuxLauncher(Path("/repo"), fleet_root, runner)
    assert launcher.launch(60, "feat/slice-60-a", 1) is True
    command: str = runner.calls[-1][-1]
    assert f"FLEET_RUNNER_SKILL={DEFAULT_RUNNER_SKILL} " in command
    assert f"FLEET_TDD_SKILL={DEFAULT_TDD_SKILL} " in command
    assert f"FLEET_QA_SKILL={DEFAULT_QA_SKILL} " in command


def test_launcher_injects_explicit_skill_names_into_the_pane_env(fleet_root: Path) -> None:
    runner = _RecordingTmuxRunner({"has-session": 1})
    launcher = TmuxLauncher(
        Path("/repo"),
        fleet_root,
        runner,
        runner_skill="/run-slice",
        tdd_skill="/red-green",
        qa_skill="/review",
    )
    assert launcher.launch(61, "feat/slice-61-b", 1) is True
    command: str = runner.calls[-1][-1]
    assert "FLEET_RUNNER_SKILL=/run-slice " in command
    assert "FLEET_TDD_SKILL=/red-green " in command
    assert "FLEET_QA_SKILL=/review " in command


def test_launcher_injects_the_python_interpreter_into_the_pane_env(fleet_root: Path) -> None:
    runner = _RecordingTmuxRunner({"has-session": 1})
    launcher = TmuxLauncher(
        Path("/repo"), fleet_root, runner, python_executable="/opt/venv/bin/python"
    )
    assert launcher.launch(49, "feat/slice-49-r", 1) is True
    command: str = runner.calls[-1][-1]
    assert "FLEET_PYTHON=/opt/venv/bin/python " in command


def test_launcher_splits_the_grid_when_the_session_exists(fleet_root: Path) -> None:
    runner = _RecordingTmuxRunner({"has-session": 0})
    launcher = TmuxLauncher(Path("/repo"), fleet_root, runner)
    assert launcher.launch(42, "feat/slice-42-y", 2) is True
    subcommands: list[str] = [call[1] for call in runner.calls]
    assert subcommands == ["has-session", "split-window", "select-layout"]
    assert "runner-wrap.sh 42 feat/slice-42-y 2" in runner.calls[1][-1]


def test_launcher_falls_back_to_a_new_window_when_split_fails(fleet_root: Path) -> None:
    runner = _RecordingTmuxRunner({"has-session": 0, "split-window": 1})
    launcher = TmuxLauncher(Path("/repo"), fleet_root, runner)
    assert launcher.launch(43, "feat/slice-43-z", 1) is True
    subcommands: list[str] = [call[1] for call in runner.calls]
    assert subcommands == ["has-session", "split-window", "new-window"]


def test_launcher_reports_failure_when_tmux_refuses(fleet_root: Path) -> None:
    runner = _RecordingTmuxRunner({"has-session": 0, "split-window": 1, "new-window": 1})
    launcher = TmuxLauncher(Path("/repo"), fleet_root, runner)
    assert launcher.launch(44, "feat/slice-44-w", 1) is False


class _RecordingCleanupRunner:
    """Records the cleanup argv + cwd; returns a fixed exit code."""

    def __init__(self, exit_code: int = 0) -> None:
        self.exit_code = exit_code
        self.calls: list[tuple[list[str], Path | None]] = []

    def __call__(self, args: Sequence[str], cwd: Path | None = None) -> int:
        self.calls.append((list(args), cwd))
        return self.exit_code


def test_cleanup_defaults_the_skill_model_and_effort_to_the_constants() -> None:
    runner = _RecordingCleanupRunner()
    cleaner = ClaudeCleanup(Path("/repo"), runner)
    assert cleaner.cleanup("feat/slice-9-x") is True
    argv: list[str] = runner.calls[-1][0]
    assert argv[argv.index("-p") + 1] == "/cleanup-merged-branches feat/slice-9-x"
    assert argv[argv.index("--model") + 1] == FLEET_MODEL
    assert argv[argv.index("--effort") + 1] == FLEET_EFFORT
    assert runner.calls[-1][1] == Path("/repo")


def test_cleanup_injects_an_explicit_skill_model_and_effort() -> None:
    runner = _RecordingCleanupRunner()
    cleaner = ClaudeCleanup(
        Path("/repo"), runner, model="claude-haiku-4-5", effort="low", cleanup_skill="/tidy"
    )
    assert cleaner.cleanup("feat/slice-9-x") is True
    argv: list[str] = runner.calls[-1][0]
    assert argv[argv.index("-p") + 1] == "/tidy feat/slice-9-x"
    assert argv[argv.index("--model") + 1] == "claude-haiku-4-5"
    assert argv[argv.index("--effort") + 1] == "low"


def test_cleanup_reports_failure_on_nonzero_exit() -> None:
    runner = _RecordingCleanupRunner(exit_code=1)
    cleaner = ClaudeCleanup(Path("/repo"), runner)
    assert cleaner.cleanup("feat/slice-9-x") is False
