"""Unit tests for the supervisor's az-CLI and tmux adapters (canned I/O, no az/tmux)."""

from collections.abc import Sequence
import json
from pathlib import Path

from flotilla.constants import FLEET_EFFORT, FLEET_MODEL, HEARTBEAT_INTERVAL_SECONDS
from flotilla.supervisor import (
    AzCliAdo,
    ClaudeCleanup,
    IssueLinks,
    IssueRef,
    TmuxLauncher,
)

# fleet_root is provided by tests/conftest.py


class _RecordingAzRunner:
    """Canned az stdout per matched argv fragment; records every call."""

    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses
        self.calls: list[list[str]] = []
        self.in_files: list[str] = []

    def __call__(self, args: Sequence[str]) -> str:
        arglist: list[str] = list(args)
        self.calls.append(arglist)
        if "--in-file" in arglist:
            path: str = arglist[arglist.index("--in-file") + 1]
            self.in_files.append(Path(path).read_text(encoding="utf-8"))
        joined: str = " ".join(args)
        for fragment, response in self.responses.items():
            if fragment in joined:
                return response
        return "{}"


class _RecordingTmuxRunner:
    """Configurable exit codes per tmux subcommand; records every call."""

    def __init__(self, exit_codes: dict[str, int]) -> None:
        self.exit_codes = exit_codes
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> int:
        self.calls.append(list(args))
        return self.exit_codes.get(args[1], 0)


def test_issues_in_state_queries_via_rest_and_parses_tags() -> None:
    wiql_resp: str = json.dumps({"workItems": [{"id": 69}, {"id": 70}]})
    batch_resp: str = json.dumps(
        {
            "value": [
                {
                    "id": 69,
                    "fields": {"System.Title": "feat: status", "System.Tags": "fleet:claimed; x"},
                },
                {"id": 70, "fields": {"System.Title": "feat: runner"}},
            ]
        }
    )
    runner = _RecordingAzRunner({"resource wiql": wiql_resp, "workitemsbatch": batch_resp})
    refs: tuple[IssueRef, ...] = AzCliAdo(runner, project="example-project").issues_in_state("To Do")
    assert refs == (
        IssueRef(issue_id=69, title="feat: status", tags=("fleet:claimed", "x")),
        IssueRef(issue_id=70, title="feat: runner", tags=()),
    )
    # The WIQL body is delivered through the wiql invoke's --in-file payload.
    assert any(
        "[System.WorkItemType] = 'Issue'" in body and "[System.State] = 'To Do'" in body
        for body in runner.in_files
    )


def test_issues_in_state_short_circuits_when_no_ids_match() -> None:
    runner = _RecordingAzRunner({"resource wiql": json.dumps({"workItems": []})})
    refs: tuple[IssueRef, ...] = AzCliAdo(runner, project="example-project").issues_in_state("Done")
    assert refs == ()
    assert all("workitemsbatch" not in " ".join(call) for call in runner.calls)


def test_issues_in_state_tolerates_an_empty_board_read() -> None:
    # The original blocker: a blank stdout must yield no issues, not a JSONDecodeError.
    runner = _RecordingAzRunner({"resource wiql": ""})
    assert AzCliAdo(runner, project="example-project").issues_in_state("To Do") == ()


def test_issue_links_parses_predecessor_and_parent_relations() -> None:
    payload: str = json.dumps(
        {
            "relations": [
                {"rel": "System.LinkTypes.Dependency-Reverse", "url": "https://x/workItems/68"},
                {"rel": "System.LinkTypes.Dependency-Reverse", "url": "https://x/workItems/67"},
                {"rel": "System.LinkTypes.Hierarchy-Reverse", "url": "https://x/workItems/50"},
                {"rel": "AttachedFile", "url": "https://x/attachments/abc"},
            ]
        }
    )
    runner = _RecordingAzRunner({"--expand relations": payload})
    links: IssueLinks = AzCliAdo(runner).issue_links(70)
    assert links == IssueLinks(parent_id=50, predecessor_ids=(68, 67))


def test_issue_state_reads_system_state() -> None:
    payload: str = json.dumps({"fields": {"System.State": "Done"}})
    runner = _RecordingAzRunner({"work-item show": payload})
    assert AzCliAdo(runner).issue_state(68) == "Done"


def test_add_tag_appends_to_existing_tags() -> None:
    payload: str = json.dumps({"fields": {"System.Tags": "alpha; beta"}})
    runner = _RecordingAzRunner({"work-item show": payload})
    AzCliAdo(runner).add_tag(70, "fleet:claimed")
    update: list[str] = runner.calls[-1]
    assert "update" in update
    assert "System.Tags=alpha; beta; fleet:claimed" in update


def test_add_tag_is_idempotent() -> None:
    payload: str = json.dumps({"fields": {"System.Tags": "fleet:claimed"}})
    runner = _RecordingAzRunner({"work-item show": payload})
    AzCliAdo(runner).add_tag(70, "fleet:claimed")
    assert all("update" not in call for call in runner.calls)


def test_remove_tag_filters_the_tag_out() -> None:
    payload: str = json.dumps({"fields": {"System.Tags": "alpha; fleet:claimed; beta"}})
    runner = _RecordingAzRunner({"work-item show": payload})
    AzCliAdo(runner).remove_tag(70, "fleet:claimed")
    update: list[str] = runner.calls[-1]
    assert "System.Tags=alpha; beta" in update


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


def test_cleanup_defaults_the_model_and_effort_to_the_constants() -> None:
    runner = _RecordingCleanupRunner()
    cleaner = ClaudeCleanup(Path("/repo"), runner)
    assert cleaner.cleanup("feat/slice-9-x") is True
    argv: list[str] = runner.calls[-1][0]
    assert argv[argv.index("--model") + 1] == FLEET_MODEL
    assert argv[argv.index("--effort") + 1] == FLEET_EFFORT
    assert runner.calls[-1][1] == Path("/repo")


def test_cleanup_injects_an_explicit_model_and_effort() -> None:
    runner = _RecordingCleanupRunner()
    cleaner = ClaudeCleanup(Path("/repo"), runner, model="claude-haiku-4-5", effort="low")
    assert cleaner.cleanup("feat/slice-9-x") is True
    argv: list[str] = runner.calls[-1][0]
    assert argv[argv.index("--model") + 1] == "claude-haiku-4-5"
    assert argv[argv.index("--effort") + 1] == "low"


def test_cleanup_reports_failure_on_nonzero_exit() -> None:
    runner = _RecordingCleanupRunner(exit_code=1)
    cleaner = ClaudeCleanup(Path("/repo"), runner)
    assert cleaner.cleanup("feat/slice-9-x") is False
