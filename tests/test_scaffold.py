"""Unit tests for ``squadra.scaffold`` (the ``squadra init`` scaffolding)."""

from pathlib import Path
import tomllib

from squadra._resources import resolve_template
from squadra.config import (
    CONFIG_FILENAME,
    DEFAULT_CLEANUP_SKILL,
    DEFAULT_QA_SKILL,
    DEFAULT_RUNNER_SKILL,
    DEFAULT_TDD_SKILL,
    load_config,
)
from squadra.domain import Lifecycle
from squadra.scaffold import (
    CLEANUP_SKILL_FILENAME,
    RUNNER_SKILL_FILENAME,
    init_project,
    main,
    render_squadra_toml,
)


def test_render_ado_toml_parses_and_loads(tmp_path: Path) -> None:
    """The default (ado) toml parses and round-trips through load_config."""
    text: str = render_squadra_toml("ado")
    parsed: dict[str, object] = tomllib.loads(text)
    assert isinstance(parsed["board"], dict)

    path: Path = tmp_path / CONFIG_FILENAME
    path.write_text(text, encoding="utf-8")
    cfg = load_config(config_path=path, fleet_home=tmp_path)

    assert cfg.provider == "ado"
    assert cfg.runner_skill == DEFAULT_RUNNER_SKILL
    assert cfg.tdd_skill == DEFAULT_TDD_SKILL
    assert cfg.qa_skill == DEFAULT_QA_SKILL
    assert cfg.cleanup_skill == DEFAULT_CLEANUP_SKILL
    # The inferable ADO-Basic states come through the declared table verbatim.
    assert cfg.states[Lifecycle.QUEUED] == ("To Do",)
    assert cfg.states[Lifecycle.ACTIVE] == ("Doing",)
    assert cfg.states[Lifecycle.DONE] == ("Done",)


def test_render_nonado_toml_includes_states_placeholder(tmp_path: Path) -> None:
    """A non-ado provider emits a REQUIRED, valid [board.states] placeholder.

    The placeholder is emitted with concrete values (not commented out) so the
    file both parses as TOML and loads through load_config — load_config requires
    all three buckets for any provider other than ado.
    """
    text: str = render_squadra_toml("github")
    parsed: dict[str, object] = tomllib.loads(text)
    board = parsed["board"]
    assert isinstance(board, dict)
    assert "states" in board, "non-ado provider must declare [board.states]"

    path: Path = tmp_path / CONFIG_FILENAME
    path.write_text(text, encoding="utf-8")
    cfg = load_config(config_path=path, fleet_home=tmp_path)
    assert cfg.provider == "github"
    # All three buckets resolved from the declared placeholder.
    assert cfg.states[Lifecycle.QUEUED]
    assert cfg.states[Lifecycle.ACTIVE]
    assert cfg.states[Lifecycle.DONE]


def test_init_writes_all_three_artifacts(tmp_path: Path) -> None:
    """init_project emits squadra.toml + both skill templates."""
    results = init_project(tmp_path, provider="ado")
    assert results == {
        CONFIG_FILENAME: True,
        RUNNER_SKILL_FILENAME: True,
        CLEANUP_SKILL_FILENAME: True,
    }
    assert (tmp_path / CONFIG_FILENAME).is_file()
    assert (tmp_path / RUNNER_SKILL_FILENAME).is_file()
    assert (tmp_path / CLEANUP_SKILL_FILENAME).is_file()


def test_skill_templates_have_fillins_and_skill_args(tmp_path: Path) -> None:
    """The runner template is non-empty, marks fill-ins, and references tdd/qa args."""
    init_project(tmp_path, provider="ado")
    runner: str = (tmp_path / RUNNER_SKILL_FILENAME).read_text(encoding="utf-8")
    cleanup: str = (tmp_path / CLEANUP_SKILL_FILENAME).read_text(encoding="utf-8")

    assert runner.strip()
    assert cleanup.strip()
    assert "FILL IN" in runner
    assert "## Gates" in runner
    # The runner must read the tdd/qa skill args rather than hardcoding them.
    assert "tdd-skill" in runner
    assert "qa-skill" in runner
    assert "FILL IN" in cleanup
    assert "patch-equivalence" in cleanup.lower()


def test_init_refuses_overwrite_without_force(tmp_path: Path) -> None:
    """A second init skips pre-existing files unless force is set."""
    init_project(tmp_path, provider="ado")
    # Mark the config so we can prove it was/wasn't clobbered.
    (tmp_path / CONFIG_FILENAME).write_text("# sentinel\n", encoding="utf-8")

    skipped = init_project(tmp_path, provider="ado")
    assert skipped[CONFIG_FILENAME] is False
    assert (tmp_path / CONFIG_FILENAME).read_text(encoding="utf-8") == "# sentinel\n"

    forced = init_project(tmp_path, provider="ado", force=True)
    assert forced[CONFIG_FILENAME] is True
    assert (tmp_path / CONFIG_FILENAME).read_text(encoding="utf-8") != "# sentinel\n"


def test_resolve_template_resolves() -> None:
    """resolve_template finds the packaged runner-skill template."""
    path: Path = resolve_template("runner-skill.md")
    assert path.is_file()
    assert path.read_text(encoding="utf-8").strip()


def test_main_init_writes_into_dir(tmp_path: Path) -> None:
    """`main(['init', '--dir', ...])` writes the artifacts and returns 0."""
    rc: int = main(["init", "--dir", str(tmp_path), "--provider", "ado"])
    assert rc == 0
    assert (tmp_path / CONFIG_FILENAME).is_file()
