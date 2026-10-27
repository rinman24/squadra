"""Unit tests for the GitHub adapter's native-state rules (ledger N20, ADR-0006).

Each runs the real :class:`squadra.github_board.GhApiGitHub` over an
:class:`~tests.helpers.github_stub.InMemoryGitHub`: closure wins over Status,
partial items (an Origin without the commit marker) are in no bucket while a
committed item off the project or with an empty Status is loud, the closed names
are squadra's and must all be mapped, and a closed issue is never reopened.
"""

from collections.abc import Mapping
from pathlib import Path

import pytest

from squadra.board import BoardValidationError, build_board
from squadra.config import ConfigError, GitHubBoardConfig, SquadraConfig, load_config
from squadra.domain import Lifecycle, Tags
from squadra.github_board import (
    CLOSED_COMPLETED,
    CLOSED_DUPLICATE,
    CLOSED_NO_REASON,
    CLOSED_NOT_PLANNED,
    COMMITTED_LABEL,
    GhApiGitHub,
    native_state,
    split_origin_marker,
    with_origin_marker,
)
from squadra.scaffold import render_squadra_toml
from tests.helpers.github_stub import (
    GH_STATES,
    REPOSITORY,
    InMemoryGitHub,
    StubbedGitHubBoard,
    StubIssue,
    StubPull,
)

N: int = 7


def _board(*issues: StubIssue) -> StubbedGitHubBoard:
    github = InMemoryGitHub()
    for issue in issues:
        github.add(issue)
    return StubbedGitHubBoard(github)


def _ids(board: StubbedGitHubBoard, state: Lifecycle) -> set[int]:
    return {item.item_id for item in board.items_in_state(state)}


def _all_ids(board: StubbedGitHubBoard) -> set[int]:
    return {i for state in Lifecycle for i in _ids(board, state)}


def _partial_body() -> str:
    return with_origin_marker("the body", "A:I1")


# --- closure wins over Status -------------------------------------------------------


def test_native_state_closure_wins_over_status() -> None:
    assert native_state(is_open=False, state_reason="COMPLETED", status="Todo") == CLOSED_COMPLETED
    assert native_state(is_open=False, state_reason="NOT_PLANNED", status="Todo") == (
        CLOSED_NOT_PLANNED
    )
    assert native_state(is_open=False, state_reason="DUPLICATE", status=None) == CLOSED_DUPLICATE
    assert native_state(is_open=False, state_reason=None, status="Todo") == CLOSED_NO_REASON
    assert native_state(is_open=True, state_reason=None, status="Todo") == "Todo"
    assert native_state(is_open=True, state_reason="REOPENED", status=None) is None


@pytest.mark.parametrize("reason", ["COMPLETED", "NOT_PLANNED", "DUPLICATE", None])
def test_a_closed_issue_with_a_queued_status_is_never_queued(reason: str | None) -> None:
    board = _board(StubIssue(N, "x", is_open=False, state_reason=reason, status="Todo"))
    assert board.item_state(N) is not Lifecycle.QUEUED
    assert N not in _ids(board, Lifecycle.QUEUED)


@pytest.mark.parametrize("reason", ["REOPENED", "WONTFIX"])
def test_an_unknown_close_reason_raises(reason: str) -> None:
    board = _board(StubIssue(N, "x", is_open=False, state_reason=reason, status="Todo"))
    with pytest.raises(BoardValidationError, match="unknown close reason"):
        board.item_state(N)
    assert N not in _all_ids(board)


def test_open_issue_with_a_done_status_raises() -> None:
    # a reopened issue whose Status "Item closed" left at Done, or a hand drag
    board = _board(StubIssue(N, "x", status="Done"))
    with pytest.raises(BoardValidationError, match="valid only on a closed issue"):
        board.item_state(N)
    assert N not in _all_ids(board)


# --- partial vs committed -------------------------------------------------------------


@pytest.mark.parametrize(
    ("on_project", "status"), [(False, None), (True, None), (True, "Todo")], ids=str
)
def test_a_partial_item_is_in_no_bucket(on_project: bool, status: str | None) -> None:
    # off the project, an empty Status, or an interloper's queued Status alike
    board = _board(StubIssue(N, "x", body=_partial_body(), on_project=on_project, status=status))
    with pytest.raises(BoardValidationError, match="partial"):
        board.item_state(N)
    assert N not in _all_ids(board)


def test_a_closed_partial_item_is_still_partial() -> None:
    board = _board(
        StubIssue(N, "x", body=_partial_body(), is_open=False, state_reason="NOT_PLANNED")
    )
    with pytest.raises(BoardValidationError, match="partial"):
        board.item_state(N)


@pytest.mark.parametrize(
    ("on_project", "match"),
    [(False, "not on the project"), (True, "empty Status")],
)
def test_a_committed_item_off_the_project_or_with_an_empty_status_raises(
    on_project: bool, match: str
) -> None:
    board = _board(
        StubIssue(N, "x", body=_partial_body(), labels=[COMMITTED_LABEL], on_project=on_project)
    )
    with pytest.raises(BoardValidationError, match=match):
        board.item_state(N)
    assert N not in _all_ids(board)


def test_an_issue_without_an_origin_off_the_project_raises() -> None:
    board = _board(StubIssue(N, "x", on_project=False))
    with pytest.raises(BoardValidationError, match="not on the project"):
        board.item_state(N)


def test_a_committed_item_maps_from_its_status_and_hides_the_marker() -> None:
    board = _board(
        StubIssue(
            N, "x", body=_partial_body(), labels=[COMMITTED_LABEL, "fleet:claimed"], status="Todo"
        )
    )
    assert board.item_state(N) is Lifecycle.QUEUED
    (item,) = board.items_in_state(Lifecycle.QUEUED)
    assert item.tags == ("fleet:claimed",)


def test_archived_and_foreign_project_items_are_left_out() -> None:
    github = InMemoryGitHub()
    github.add(StubIssue(1, "archived", status="Todo", archived=True))
    github.add(StubIssue(2, "other repo", status="Todo", repository="acme/other"))
    github.add(StubIssue(3, "ours", status="Todo"))
    assert _ids(StubbedGitHubBoard(github), Lifecycle.QUEUED) == {3}


# --- the Origin marker --------------------------------------------------------------------


def test_origin_marker_round_trips_any_string() -> None:
    origin: str = 'A:I1 --> "q" \\ <b>π</b>\n-->'
    assert split_origin_marker(with_origin_marker("body\n", origin)) == ("body\n", origin)
    assert split_origin_marker("no marker here") is None


def test_a_malformed_origin_marker_raises() -> None:
    with pytest.raises(BoardValidationError, match="malformed"):
        split_origin_marker("body\n<!-- squadra-origin: not json -->")


# --- validate_config --------------------------------------------------------------------


def _states(**overrides: tuple[str, ...]) -> Mapping[Lifecycle, tuple[str, ...]]:
    merged: dict[Lifecycle, tuple[str, ...]] = dict(GH_STATES)
    for key, names in overrides.items():
        merged[Lifecycle(key)] = names
    return merged


def test_validate_config_passes_on_the_test_map() -> None:
    StubbedGitHubBoard().validate_config()


def test_validate_config_fails_when_closed_duplicate_is_unmapped() -> None:
    board = StubbedGitHubBoard(states=_states(withdrawn=(CLOSED_NOT_PLANNED, CLOSED_NO_REASON)))
    with pytest.raises(BoardValidationError, match="closed:duplicate"):
        board.validate_config()


def test_validate_config_fails_on_a_closed_prefixed_status_option() -> None:
    github = InMemoryGitHub(status_options=("Todo", "Triage", "In Progress", "Done", "closed:x"))
    with pytest.raises(BoardValidationError, match="closed:x"):
        StubbedGitHubBoard(github).validate_config()


def test_validate_config_fails_on_an_unmapped_status_option() -> None:
    github = InMemoryGitHub(status_options=("Todo", "Triage", "In Progress", "Done", "Blocked"))
    with pytest.raises(BoardValidationError, match="Blocked"):
        StubbedGitHubBoard(github).validate_config()


def test_validate_config_fails_on_a_configured_name_the_project_lacks() -> None:
    github = InMemoryGitHub(status_options=("Todo", "In Progress", "Done"))
    with pytest.raises(BoardValidationError, match="Triage"):
        StubbedGitHubBoard(github).validate_config()


def test_validate_config_refuses_a_closed_name_in_queued() -> None:
    board = StubbedGitHubBoard(
        states=_states(
            queued=("Todo", "Triage", CLOSED_NO_REASON),
            withdrawn=(CLOSED_NOT_PLANNED, CLOSED_DUPLICATE),
        )
    )
    with pytest.raises(BoardValidationError, match="never queued or active"):
        board.validate_config()


def test_validate_config_needs_completed_in_done_and_not_planned_in_withdrawn() -> None:
    swapped = StubbedGitHubBoard(
        states=_states(
            done=("Done", CLOSED_NOT_PLANNED),
            withdrawn=(CLOSED_COMPLETED, CLOSED_DUPLICATE, CLOSED_NO_REASON),
        )
    )
    with pytest.raises(BoardValidationError, match="closed:completed"):
        swapped.validate_config()


def test_validate_config_refuses_a_tag_prefix_covering_the_commit_marker() -> None:
    board = GhApiGitHub(
        GitHubBoardConfig(REPOSITORY, "acme", 7),
        run=InMemoryGitHub(),
        states=GH_STATES,
        tags=Tags("squadra:"),
    )
    with pytest.raises(BoardValidationError, match="commit marker"):
        board.validate_config()


def test_validate_config_fails_on_a_missing_project() -> None:
    board = GhApiGitHub(
        GitHubBoardConfig(REPOSITORY, "acme", 99), run=InMemoryGitHub(), states=GH_STATES
    )
    with pytest.raises(BoardValidationError, match="no project 99"):
        board.validate_config()


# --- set_state ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "reason"),
    [(Lifecycle.DONE, "COMPLETED"), (Lifecycle.WITHDRAWN, "NOT_PLANNED")],
)
def test_done_and_withdrawn_close_the_issue_in_one_write(target: Lifecycle, reason: str) -> None:
    board = _board(StubIssue(N, "x", status="In Progress"))
    board.set_state(N, target)
    issue: StubIssue = board.github.issues[N]
    assert (issue.is_open, issue.state_reason) == (False, reason)
    assert board.github.writes == [("close", N)]


def test_queued_and_active_write_one_status() -> None:
    board = _board(StubIssue(N, "x", status="Triage"))
    board.set_state(N, Lifecycle.ACTIVE)
    board.set_state(N, Lifecycle.QUEUED)
    assert board.github.issues[N].status == "Todo"
    assert board.github.writes == [("status", N), ("status", N)]


@pytest.mark.parametrize(
    ("issue", "target"),
    [
        (StubIssue(N, "x", status="Triage"), Lifecycle.QUEUED),
        (StubIssue(N, "x", is_open=False, state_reason="COMPLETED"), Lifecycle.DONE),
        (StubIssue(N, "x", is_open=False, state_reason="DUPLICATE"), Lifecycle.WITHDRAWN),
    ],
    ids=["queued", "done", "withdrawn-duplicate"],
)
def test_set_state_into_the_current_bucket_writes_nothing(
    issue: StubIssue, target: Lifecycle
) -> None:
    board = _board(issue)
    board.set_state(N, target)
    assert board.github.writes == []


@pytest.mark.parametrize("reason", ["COMPLETED", "NOT_PLANNED", "DUPLICATE"])
@pytest.mark.parametrize("target", [Lifecycle.QUEUED, Lifecycle.ACTIVE])
def test_a_status_write_on_a_closed_issue_refuses(reason: str, target: Lifecycle) -> None:
    # an ACTIVE item closed between ticks, with each reason: never reopened
    board = _board(StubIssue(N, "x", is_open=False, state_reason=reason, status="In Progress"))
    with pytest.raises(BoardValidationError, match="never does"):
        board.set_state(N, target)
    assert board.github.writes == []
    assert board.github.issues[N].is_open is False


def test_a_close_never_changes_a_closed_issues_reason() -> None:
    board = _board(StubIssue(N, "x", is_open=False, state_reason="COMPLETED"))
    with pytest.raises(BoardValidationError, match="never does"):
        board.set_state(N, Lifecycle.WITHDRAWN)
    assert board.github.writes == []


def test_set_state_refuses_a_partial_item() -> None:
    board = _board(StubIssue(N, "x", body=_partial_body(), status="Todo"))
    with pytest.raises(BoardValidationError, match="partial"):
        board.set_state(N, Lifecycle.ACTIVE)
    assert board.github.writes == []


def test_a_status_write_off_the_project_refuses() -> None:
    board = _board(StubIssue(N, "x", on_project=False))
    with pytest.raises(BoardValidationError, match="not on the project"):
        board.set_state(N, Lifecycle.QUEUED)


# --- links, PRs, tags -------------------------------------------------------------------------


def test_item_links_refuse_a_link_outside_the_repository() -> None:
    board = _board(StubIssue(N, "x", status="Todo", blocked_by=[("acme/other", 3)]))
    with pytest.raises(BoardValidationError, match="outside acme/widgets"):
        board.item_links(N)


def test_completed_pr_url_ignores_an_unmerged_pr() -> None:
    board = _board()
    board.github.pulls.append(StubPull("acme:feat/x", "main", "https://x/1", merged=False))
    assert board.completed_pr_url("feat/x") is None
    board.github.pulls.append(StubPull("acme:feat/x", "main", "https://x/2", merged=True))
    assert board.completed_pr_url("feat/x") == "https://x/2"


def test_remove_tag_of_an_absent_label_writes_nothing() -> None:
    board = _board(StubIssue(N, "x", status="Todo"))
    board.remove_tag(N, "fleet:claimed")
    assert board.github.writes == []


def test_board_writes_are_not_built_yet() -> None:
    board = _board()
    with pytest.raises(NotImplementedError, match="SQ5b"):
        board.items_with_origin()


# --- [board.github] config ------------------------------------------------------------------

_GITHUB_TOML: str = """
[board]
provider = "github"
claim_scope = "whole-board"
[board.states]
queued = ["Todo"]
active = ["In Progress"]
done = ["Done", "closed:completed"]
withdrawn = ["closed:not_planned", "closed:duplicate", "closed:"]
"""


def _load(tmp_path: Path, extra: str, provider: str | None = None) -> SquadraConfig:
    path: Path = tmp_path / "squadra.toml"
    path.write_text(_GITHUB_TOML + extra, encoding="utf-8")
    return load_config(config_path=path, fleet_home=tmp_path, provider=provider)


def test_board_github_resolves_and_builds_the_adapter(tmp_path: Path) -> None:
    config = _load(
        tmp_path,
        '[board.github]\nrepository = "acme/widgets"\nproject_owner = "acme"\nproject_number = 7\n',
    )
    assert config.github == GitHubBoardConfig("acme/widgets", "acme", 7)
    assert isinstance(build_board(config), GhApiGitHub)


@pytest.mark.parametrize(
    ("table", "missing"),
    [
        ("", "[board.github]"),
        ('[board.github]\nproject_owner = "acme"\nproject_number = 7\n', "'repository'"),
        ('[board.github]\nrepository = "acme/w"\nproject_number = 7\n', "'project_owner'"),
        ('[board.github]\nrepository = "acme/w"\nproject_owner = "acme"\n', "'project_number'"),
    ],
)
def test_a_missing_board_github_key_names_it(tmp_path: Path, table: str, missing: str) -> None:
    with pytest.raises(ConfigError) as caught:
        _load(tmp_path, table)
    assert missing in str(caught.value)


@pytest.mark.parametrize(
    "table",
    [
        '[board.github]\nrepository = "widgets"\nproject_owner = "a"\nproject_number = 7\n',
        '[board.github]\nrepository = "a/b/c"\nproject_owner = "a"\nproject_number = 7\n',
        '[board.github]\nrepository = "a/b"\nproject_owner = "a"\nproject_number = 0\n',
        '[board.github]\nrepository = "a/b"\nproject_owner = "a"\nproject_number = "7"\n',
    ],
)
def test_a_malformed_board_github_fails(tmp_path: Path, table: str) -> None:
    with pytest.raises(ConfigError, match=r"\[board.github\]"):
        _load(tmp_path, table)


def test_board_github_is_not_required_for_another_provider(tmp_path: Path) -> None:
    assert _load(tmp_path, "", provider="fake").github is None


def test_the_scaffolded_github_map_validates_against_a_default_project(tmp_path: Path) -> None:
    # GitHub's default project template: Todo / In Progress / Done
    text: str = render_squadra_toml("github").replace(
        'claim_scope = ""', 'claim_scope = "whole-board"'
    )
    path: Path = tmp_path / "squadra.toml"
    path.write_text(text, encoding="utf-8")
    config: SquadraConfig = load_config(config_path=path, fleet_home=tmp_path)
    github = InMemoryGitHub(status_options=("Todo", "In Progress", "Done"))
    GhApiGitHub(
        GitHubBoardConfig(REPOSITORY, "acme", 7), run=github, states=config.states
    ).validate_config()
