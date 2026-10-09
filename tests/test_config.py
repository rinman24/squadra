"""Unit tests for ``squadra.config``: the mandatory, fail-closed claim scope.

``[board].claim_scope`` is required and has no default (WSQ1): nothing on a
board is claimable until the operator has declared what is. Loading fails with
``ConfigError`` when the declaration is missing, empty, unknown, or
contradicts ``parent_scope_ids``. The scope env vars (``FLEET_EPIC_IDS``,
``FLEET_PARENT_SCOPE_IDS``) are gone, so the toml is the only way in.
"""

from pathlib import Path

import pytest

from squadra.config import CONFIG_FILENAME, ClaimScope, ConfigError, SquadraConfig, load_config


def _load(tmp_path: Path, board_lines: str) -> SquadraConfig:
    path: Path = tmp_path / CONFIG_FILENAME
    path.write_text(f'[board]\nprovider = "ado"\n{board_lines}', encoding="utf-8")
    return load_config(config_path=path, fleet_home=tmp_path)


def test_missing_claim_scope_fails_naming_both_options(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="claim_scope") as raised:
        _load(tmp_path, "")
    assert '"parents"' in str(raised.value)
    assert '"whole-board"' in str(raised.value)


def test_empty_claim_scope_fails_naming_both_options(tmp_path: Path) -> None:
    # `squadra init` scaffolds the key with an empty value; it must not load.
    with pytest.raises(ConfigError, match="claim_scope") as raised:
        _load(tmp_path, 'claim_scope = ""\n')
    assert '"parents"' in str(raised.value)
    assert '"whole-board"' in str(raised.value)


def test_unknown_claim_scope_fails(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="claim_scope"):
        _load(tmp_path, 'claim_scope = "everything"\n')


def test_parents_scope_without_ids_fails(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="parent_scope_ids"):
        _load(tmp_path, 'claim_scope = "parents"\n')
    with pytest.raises(ConfigError, match="parent_scope_ids"):
        _load(tmp_path, 'claim_scope = "parents"\nparent_scope_ids = []\n')


def test_whole_board_scope_with_ids_fails(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="parent_scope_ids"):
        _load(tmp_path, 'claim_scope = "whole-board"\nparent_scope_ids = [105]\n')


def test_parents_scope_with_ids_loads(tmp_path: Path) -> None:
    config = _load(tmp_path, 'claim_scope = "parents"\nparent_scope_ids = [105, 106]\n')
    assert config.claim_scope is ClaimScope.PARENTS
    assert config.parent_scope_ids == (105, 106)


def test_whole_board_scope_loads(tmp_path: Path) -> None:
    config = _load(tmp_path, 'claim_scope = "whole-board"\n')
    assert config.claim_scope is ClaimScope.WHOLE_BOARD
    assert config.parent_scope_ids == ()


@pytest.mark.parametrize("var", ["FLEET_EPIC_IDS", "FLEET_PARENT_SCOPE_IDS"])
def test_scope_env_vars_are_not_an_entry_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, var: str
) -> None:
    # The env layer can neither satisfy a missing declaration nor override one.
    monkeypatch.setenv(var, "139")
    with pytest.raises(ConfigError, match="claim_scope"):
        _load(tmp_path, "")
    config = _load(tmp_path, 'claim_scope = "whole-board"\n')
    assert config.parent_scope_ids == ()
