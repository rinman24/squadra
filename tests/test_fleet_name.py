"""One fleet per repository, named (N34, SQ5g): Juval's acceptance test.

Two fleets on one host, ``alpha`` and ``beta``, both carrying item 105, must
never share a host-global name: units, compose projects, the tag prefix. A
fleet's name is required, a fleet is one repository (the GitHub board's
repository is ``FLEET_HOME``'s ``origin``), and a fleet whose ``fleet_root`` is
stamped for another name, or is unstamped from before names existed, refuses to
tick while increment dirs hold its work.
"""

from collections.abc import Callable, Sequence
from pathlib import Path
import subprocess

import pytest

from squadra.board import BoardValidationError, build_board
from squadra.config import (
    ClaimScope,
    ConfigError,
    GitHubBoardConfig,
    SquadraConfig,
    load_config,
)
from squadra.domain import Lifecycle
from squadra.engines import fleet_stamp_refusal
from squadra.github_board import GhApiGitHub, github_repository_of
from squadra.sandbox import ComposeSandbox
from squadra.status import FleetStatus, write
from squadra.supervisor import FLEET_STAMP_FILENAME, TickSeams, dry_run_seams, run_tick
from squadra.units import UnitConflictError, UnitContext, install_units
from tests.helpers.cleanup_fakes import FakeCleanup
from tests.helpers.fleet_fakes import FakeBoard, FakeIssue
from tests.helpers.github_stub import GH_STATES, REPOSITORY, InMemoryGitHub

_ITEM: int = 105
_BRANCH: str = f"feat/increment-{_ITEM}-example"


def _unit_ctx(name: str, home: str) -> UnitContext:
    return UnitContext(
        fleet_name=name,
        venv_bin=Path("/opt/squadra/venv/bin"),
        fleet_home=Path(home),
        fleet_root=Path(home) / ".claude" / "fleet",
        key_vault="fleet-kv",
    )


def _repository_of(home: Path, _app_repo_url: str) -> str | None:
    return f"acme/{home.name}"  # one repository per fleet home


# --- units -----------------------------------------------------------------------


def test_two_fleets_install_distinct_units_and_a_reinstall_is_allowed(tmp_path: Path) -> None:
    alpha: list[Path] = install_units(
        _unit_ctx("alpha", "/srv/alpha"), repository_of=_repository_of, dest=tmp_path
    )
    beta: list[Path] = install_units(
        _unit_ctx("beta", "/srv/beta"), repository_of=_repository_of, dest=tmp_path
    )

    assert {path.name for path in alpha} == {"squadra-alpha.service", "squadra-alpha.timer"}
    assert {path.name for path in beta} == {"squadra-beta.service", "squadra-beta.timer"}
    assert "Unit=squadra-beta.service" in (tmp_path / "squadra-beta.timer").read_text()
    # Re-installing a fleet over its own units (same FLEET_HOME) is an upgrade.
    assert (
        install_units(_unit_ctx("alpha", "/srv/alpha"), repository_of=_repository_of, dest=tmp_path)
        == alpha
    )


def test_install_refuses_to_overwrite_another_fleet_home(tmp_path: Path) -> None:
    install_units(_unit_ctx("alpha", "/srv/alpha"), repository_of=_repository_of, dest=tmp_path)
    before: str = (tmp_path / "squadra-alpha.service").read_text()

    with pytest.raises(UnitConflictError, match="/srv/alpha"):
        install_units(_unit_ctx("alpha", "/srv/other"), repository_of=_repository_of, dest=tmp_path)
    assert (tmp_path / "squadra-alpha.service").read_text() == before  # nothing written


# --- compose projects --------------------------------------------------------------


class _RecordingDocker:
    """A fake docker runner recording every argv (the host's one docker)."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(list(argv), 0, "", "")


def _finalize_105(
    name: str,
    fleet_root: Path,
    make_config: Callable[..., SquadraConfig],
    make_seams: Callable[..., TickSeams],
    make_status: Callable[..., FleetStatus],
) -> FakeCleanup:
    """Run one tick of fleet ``name`` that finalizes its merged item 105."""
    board = FakeBoard()
    board.add_issue(
        FakeIssue(
            item_id=_ITEM,
            title="feat: increment 105",
            state=Lifecycle.DONE,
            tags=["fleet:claimed", "fleet:awaiting-pr-approval"],
        )
    )
    board.completed_prs[_BRANCH] = "https://pr/105"
    root: Path = fleet_root / name
    root.mkdir(parents=True)
    write(
        make_status(
            issue_id=_ITEM, branch=_BRANCH, phase="parked", parked_state="awaiting-pr-approval"
        ),
        root,
    )
    cleanup = FakeCleanup()
    config: SquadraConfig = make_config(fleet_name=name, fleet_root=root)
    assert run_tick(make_seams(board=board, cleanup=cleanup), config) == 0
    return cleanup


def test_finalizing_one_fleets_105_never_names_the_others_project(
    fleet_root: Path,
    make_config: Callable[..., SquadraConfig],
    make_seams: Callable[..., TickSeams],
    make_status: Callable[..., FleetStatus],
) -> None:
    alpha: FakeCleanup = _finalize_105("alpha", fleet_root, make_config, make_seams, make_status)
    beta: FakeCleanup = _finalize_105("beta", fleet_root, make_config, make_seams, make_status)

    assert alpha.composed_down == ["squadra-alpha-increment-105"]
    assert beta.composed_down == ["squadra-beta-increment-105"]


def test_a_teardown_of_alphas_105_names_only_alphas_project(
    tmp_path: Path, make_config: Callable[..., SquadraConfig]
) -> None:
    from squadra.supervisor import _spec_for  # noqa: PLC0415  # pyright: ignore[reportPrivateUsage]

    docker = _RecordingDocker()
    alpha = _spec_for(_ITEM, _BRANCH, make_config(fleet_name="alpha"))
    beta = _spec_for(_ITEM, _BRANCH, make_config(fleet_name="beta"))

    assert ComposeSandbox(run=docker).teardown(alpha)
    assert alpha.project != beta.project
    assert docker.calls == [
        ["compose", "-p", alpha.project, "-f", str(alpha.compose_file), "down", "-v"]
    ]
    assert all(beta.project not in call for call in docker.calls)


# --- the name is required ------------------------------------------------------------


def _toml(tmp_path: Path, text: str) -> Path:
    path: Path = tmp_path / "squadra.toml"
    path.write_text(text + '[board]\nclaim_scope = "whole-board"\n', encoding="utf-8")
    return path


@pytest.mark.parametrize("fleet", ["", "[fleet]\n", '[fleet]\nname = ""\n'])
def test_a_missing_name_fails_config_load(tmp_path: Path, fleet: str) -> None:
    with pytest.raises(ConfigError, match=r"\[fleet\] name is required"):
        load_config(config_path=_toml(tmp_path, fleet), fleet_home=tmp_path)


@pytest.mark.parametrize("name", ["Alpha", "-alpha", "alpha_1", "a.b", "a:b", "a" * 25])
def test_a_name_outside_the_charset_fails_config_load(tmp_path: Path, name: str) -> None:
    with pytest.raises(ConfigError, match="lowercase letters, digits or hyphens"):
        load_config(config_path=_toml(tmp_path, f'[fleet]\nname = "{name}"\n'), fleet_home=tmp_path)


def test_the_name_has_no_env_layer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_NAME", "beta")
    config: SquadraConfig = load_config(
        config_path=_toml(tmp_path, '[fleet]\nname = "alpha"\n'), fleet_home=tmp_path
    )
    assert config.fleet_name == "alpha"


def test_tag_prefix_defaults_from_the_name_and_an_explicit_one_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FLEET_TAG_PREFIX", raising=False)
    path: Path = _toml(tmp_path, '[fleet]\nname = "alpha"\n')
    defaulted: SquadraConfig = load_config(config_path=path, fleet_home=tmp_path)
    assert (defaulted.tag_prefix, defaulted.tag_prefix_explicit) == ("alpha:", False)

    path.write_text(
        '[fleet]\nname = "alpha"\n[board]\nclaim_scope = "whole-board"\ntag_prefix = "gs:"\n',
        encoding="utf-8",
    )
    explicit: SquadraConfig = load_config(config_path=path, fleet_home=tmp_path)
    assert (explicit.tag_prefix, explicit.tag_prefix_explicit) == ("gs:", True)


# --- a fleet is one repository ---------------------------------------------------------


def _github(origin: str | None) -> GhApiGitHub:
    return GhApiGitHub(
        GitHubBoardConfig(REPOSITORY, "acme", 7),
        run=InMemoryGitHub(),
        states=GH_STATES,
        claim_scope=ClaimScope.WHOLE_BOARD,
        parent_scope_ids=(),
        read_origin=lambda: origin,
    )


@pytest.mark.parametrize(
    "origin",
    [
        f"git@github.com:{REPOSITORY}.git",
        f"https://github.com/{REPOSITORY}",
        "ssh://git@github.com/ACME/Widgets.git",
    ],
)
def test_validate_config_passes_when_origin_is_the_board_repository(origin: str) -> None:
    _github(origin).validate_config()


@pytest.mark.parametrize(
    "origin",
    [
        "git@github.com:acme/other.git",
        "https://x-access-token:SECRET@github.com/acme/other.git",
        "/srv/local/clone",
        None,
    ],
)
def test_validate_config_fails_closed_when_origin_is_another_repository(
    origin: str | None,
) -> None:
    with pytest.raises(BoardValidationError, match="is not FLEET_HOME's origin") as raised:
        _github(origin).validate_config()
    assert "SECRET" not in str(raised.value)  # an HTTPS origin's token is never echoed


def test_build_board_checks_fleet_homes_real_origin(
    tmp_path: Path, make_config: Callable[..., SquadraConfig], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FLEET_APP_REPO_URL", raising=False)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "remote", "add", "origin", "git@github.com:acme/other.git"],
        check=True,
    )
    config: SquadraConfig = make_config(
        provider="github",
        states=GH_STATES,
        github=GitHubBoardConfig(REPOSITORY, "acme", 7),
        fleet_home=tmp_path,
    )
    board = build_board(config)
    with pytest.raises(BoardValidationError, match=r"origin \(acme/other\)"):
        board.validate_config()


def test_github_repository_of_parses_the_three_remote_forms() -> None:
    assert github_repository_of("https://github.com/Acme/Widgets.git") == "acme/widgets"
    assert github_repository_of("git@github.com:acme/widgets") == "acme/widgets"
    assert github_repository_of("ssh://git@ghe.example.com/acme/widgets.git") == "acme/widgets"
    assert github_repository_of("https://github.com/acme") is None


# --- the fleet_root stamp ----------------------------------------------------------------


def _in_flight(root: Path) -> None:
    (root / str(_ITEM)).mkdir(parents=True)


def test_a_renamed_fleet_with_increments_in_flight_refuses_to_tick(
    fleet_root: Path,
    fake_board: FakeBoard,
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    capsys: pytest.CaptureFixture[str],
) -> None:
    fleet_root.mkdir(parents=True, exist_ok=True)
    (fleet_root / FLEET_STAMP_FILENAME).write_text("alpha\n")
    _in_flight(fleet_root)

    assert run_tick(make_seams(), make_config(fleet_name="beta")) == 1
    assert "stamped for fleet 'alpha'" in capsys.readouterr().out
    assert fake_board.calls == []  # refused before any board read or write
    assert (fleet_root / FLEET_STAMP_FILENAME).read_text() == "alpha\n"


def test_an_unstamped_root_with_increments_and_a_defaulted_prefix_refuses(
    fleet_root: Path,
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _in_flight(fleet_root)
    config: SquadraConfig = make_config(
        fleet_name="alpha", tag_prefix="alpha:", tag_prefix_explicit=False
    )
    assert run_tick(make_seams(), config) == 1
    assert "no fleet name stamp" in capsys.readouterr().out
    assert not (fleet_root / FLEET_STAMP_FILENAME).exists()


def test_an_unstamped_root_with_an_explicit_prefix_ticks_and_is_stamped(
    fleet_root: Path,
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    _in_flight(fleet_root)
    assert run_tick(make_seams(), make_config(fleet_name="alpha")) == 0
    assert (fleet_root / FLEET_STAMP_FILENAME).read_text() == "alpha\n"


def test_an_empty_root_is_restamped_under_a_new_name(
    fleet_root: Path,
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
) -> None:
    fleet_root.mkdir(parents=True, exist_ok=True)
    (fleet_root / FLEET_STAMP_FILENAME).write_text("alpha\n")
    assert run_tick(make_seams(), make_config(fleet_name="beta")) == 0
    assert (fleet_root / FLEET_STAMP_FILENAME).read_text() == "beta\n"


def test_a_dry_run_tick_checks_the_stamp_but_never_writes_it(
    fleet_root: Path,
    make_seams: Callable[..., TickSeams],
    make_config: Callable[..., SquadraConfig],
    capsys: pytest.CaptureFixture[str],
) -> None:
    seams: TickSeams = dry_run_seams(make_seams())
    assert run_tick(seams, make_config(fleet_name="alpha")) == 0
    assert not (fleet_root / FLEET_STAMP_FILENAME).exists()
    assert "WOULD stamp fleet_root with fleet name 'alpha'" in capsys.readouterr().out

    fleet_root.mkdir(parents=True, exist_ok=True)
    (fleet_root / FLEET_STAMP_FILENAME).write_text("alpha\n")
    _in_flight(fleet_root)
    assert run_tick(seams, make_config(fleet_name="beta")) == 1


@pytest.mark.parametrize(
    ("stamp", "has_increments", "explicit", "refused"),
    [
        (None, False, False, False),
        (None, True, True, False),
        (None, True, False, True),
        ("alpha", True, False, False),
        ("beta", False, False, False),
        ("beta", True, True, True),
    ],
)
def test_fleet_stamp_refusal(
    stamp: str | None, has_increments: bool, explicit: bool, refused: bool
) -> None:
    reason: str | None = fleet_stamp_refusal(
        stamp, "alpha", has_increments=has_increments, tag_prefix_explicit=explicit
    )
    assert (reason is not None) == refused
