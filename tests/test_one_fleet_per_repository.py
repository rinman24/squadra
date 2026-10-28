"""One fleet per repository on a host (N34, SQ5g2).

Issue numbers are per repository, so two fleets on one repository would be two
fleets working one issue. ``install-units`` refuses the second fleet, writing
nothing, when another fleet's service on the host serves the same repository,
and fails closed when either repository can't be resolved. The service carries
the name it was installed for, and ``fleet-tick`` refuses a checkout whose
``squadra.toml`` names another fleet (N35's residue).
"""

from collections.abc import Sequence
from pathlib import Path
import subprocess

import pytest

from squadra import (
    cli,
    repo as repo_module,
    secrets as secrets_module,
    supervisor as real_supervisor,
)
from squadra.github_board import github_repository_of
from squadra.repo import fleet_repository, repository_identity
from squadra.units import UnitConflictError, UnitContext, install_units, render_unit

_ADO_FORMS: tuple[str, ...] = (
    "https://dev.azure.com/your-org/example-project/_git/app-backend",
    "https://pat:SECRET@dev.azure.com/your-org/example-project/_git/app-backend/",
    "https://your-org@dev.azure.com/Your-Org/Example-Project/_git/App-Backend",
    "https://your-org.visualstudio.com/example-project/_git/app-backend",
    "https://your-org.visualstudio.com/DefaultCollection/example-project/_git/app-backend",
    "git@ssh.dev.azure.com:v3/your-org/example-project/app-backend",
    "ssh://git@ssh.dev.azure.com/v3/your-org/example-project/app-backend",
    "your-org@vs-ssh.visualstudio.com:v3/your-org/example-project/app-backend",
)
_ADO_REPOSITORY: str = "your-org/example-project/app-backend"


def _ctx(name: str, home: Path, app_repo_url: str = "") -> UnitContext:
    return UnitContext(
        fleet_name=name,
        venv_bin=Path("/opt/squadra/venv/bin"),
        fleet_home=home,
        fleet_root=home / ".claude" / "fleet",
        key_vault="fleet-kv",
        app_repo_url=app_repo_url,
    )


class _Table:
    """A ``RepositoryOf`` keyed by fleet home name; records what it was asked."""

    def __init__(self, repositories: dict[str, str | None]) -> None:
        self.repositories: dict[str, str | None] = repositories
        self.asked: list[tuple[Path, str]] = []

    def __call__(self, home: Path, app_repo_url: str) -> str | None:
        self.asked.append((home, app_repo_url))
        return self.repositories[home.name]


def _listing(dest: Path) -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8") for path in sorted(dest.iterdir())}


# --- the refusal ---------------------------------------------------------------------


def test_a_second_fleet_on_the_same_repository_is_refused_with_nothing_written(
    tmp_path: Path,
) -> None:
    repos = _Table({"alpha": "acme/widgets", "beta": "acme/widgets"})
    install_units(_ctx("alpha", Path("/srv/alpha")), repository_of=repos, dest=tmp_path)
    before: dict[str, str] = _listing(tmp_path)

    with pytest.raises(UnitConflictError, match="squadra-alpha.service already serves") as raised:
        install_units(_ctx("beta", Path("/srv/beta")), repository_of=repos, dest=tmp_path)
    assert "acme/widgets" in str(raised.value)
    assert _listing(tmp_path) == before  # nothing written, alpha untouched


def test_two_fleets_on_different_repositories_both_install(tmp_path: Path) -> None:
    repos = _Table({"alpha": "acme/widgets", "beta": "acme/gadgets"})
    install_units(_ctx("alpha", Path("/srv/alpha")), repository_of=repos, dest=tmp_path)
    install_units(_ctx("beta", Path("/srv/beta")), repository_of=repos, dest=tmp_path)

    assert set(_listing(tmp_path)) == {
        "squadra-alpha.service",
        "squadra-alpha.timer",
        "squadra-beta.service",
        "squadra-beta.timer",
    }
    # The other fleet is resolved from its own unit's home and clone URL.
    assert (Path("/srv/alpha"), "") in repos.asked


def test_an_unresolvable_repository_refuses_while_another_fleet_is_installed(
    tmp_path: Path,
) -> None:
    repos = _Table({"alpha": "acme/widgets", "beta": None})
    install_units(_ctx("alpha", Path("/srv/alpha")), repository_of=repos, dest=tmp_path)
    before: dict[str, str] = _listing(tmp_path)

    with pytest.raises(UnitConflictError, match="pass --app-repo-url .* or clone"):
        install_units(_ctx("beta", Path("/srv/beta")), repository_of=repos, dest=tmp_path)
    assert _listing(tmp_path) == before


def test_another_fleet_whose_repository_is_unresolvable_refuses(tmp_path: Path) -> None:
    repos = _Table({"alpha": None, "beta": "acme/gadgets"})
    install_units(_ctx("alpha", Path("/srv/alpha")), repository_of=repos, dest=tmp_path)

    with pytest.raises(UnitConflictError, match="remove squadra-alpha.service"):
        install_units(_ctx("beta", Path("/srv/beta")), repository_of=repos, dest=tmp_path)
    assert not (tmp_path / "squadra-beta.service").exists()


def test_the_first_fleet_on_a_host_needs_no_repository(tmp_path: Path) -> None:
    # Nothing to compare with, so an unresolvable repository is not asked about.
    repos = _Table({"alpha": None})
    install_units(_ctx("alpha", Path("/srv/alpha")), repository_of=repos, dest=tmp_path)
    assert repos.asked == []


def test_the_unnamed_pre_sq5g_unit_counts_as_another_fleet(tmp_path: Path) -> None:
    # An upgrade must remove the old squadra.service before installing the named one,
    # or two timers tick one repository.
    legacy: str = render_unit("squadra.service", _ctx("x", Path("/opt/squadra/app-backend")))
    (tmp_path / "squadra.service").write_text(legacy, encoding="utf-8")
    repos = _Table({"app-backend": _ADO_REPOSITORY})

    with pytest.raises(UnitConflictError, match="remove squadra.service"):
        install_units(
            _ctx("app", Path("/opt/squadra/app-backend")), repository_of=repos, dest=tmp_path
        )
    assert not (tmp_path / "squadra-app.service").exists()


def test_a_reinstall_does_not_compare_a_fleet_with_itself(tmp_path: Path) -> None:
    repos = _Table({"alpha": "acme/widgets"})
    install_units(_ctx("alpha", Path("/srv/alpha")), repository_of=repos, dest=tmp_path)
    install_units(_ctx("alpha", Path("/srv/alpha")), repository_of=repos, dest=tmp_path)
    assert repos.asked == []


def test_a_service_squadra_did_not_render_is_not_a_fleet(tmp_path: Path) -> None:
    (tmp_path / "squadra-helper.service").write_text("[Service]\nExecStart=/bin/true\n")
    repos = _Table({"alpha": None})
    install_units(_ctx("alpha", Path("/srv/alpha")), repository_of=repos, dest=tmp_path)
    assert (tmp_path / "squadra-alpha.service").is_file()


# --- real resolution: origin, else the unit's clone URL -------------------------------


def _clone_with_origin(home: Path, origin: str) -> None:
    subprocess.run(["git", "init", "-q", str(home)], check=True)
    subprocess.run(["git", "-C", str(home), "remote", "add", "origin", origin], check=True)


def test_two_clones_of_one_ado_repository_by_different_remotes_are_refused(
    tmp_path: Path,
) -> None:
    dest: Path = tmp_path / "systemd"
    dest.mkdir()
    _clone_with_origin(tmp_path / "alpha", _ADO_FORMS[0])
    _clone_with_origin(tmp_path / "beta", _ADO_FORMS[5])
    install_units(_ctx("alpha", tmp_path / "alpha"), repository_of=fleet_repository, dest=dest)

    with pytest.raises(UnitConflictError, match=_ADO_REPOSITORY):
        install_units(_ctx("beta", tmp_path / "beta"), repository_of=fleet_repository, dest=dest)


def test_a_fresh_host_resolves_each_fleet_from_its_own_clone_url(tmp_path: Path) -> None:
    # Neither fleet home is cloned yet: alpha's URL comes from its rendered unit,
    # beta's from --app-repo-url; this process's FLEET_APP_REPO_URL plays no part.
    dest: Path = tmp_path / "systemd"
    dest.mkdir()
    alpha: UnitContext = _ctx("alpha", tmp_path / "alpha", "git@github.com:acme/widgets.git")
    install_units(alpha, repository_of=fleet_repository, dest=dest)

    with pytest.raises(UnitConflictError, match="acme/widgets") as raised:
        install_units(
            _ctx(
                "beta", tmp_path / "beta", "https://x-access-token:SECRET@github.com/Acme/Widgets"
            ),
            repository_of=fleet_repository,
            dest=dest,
        )
    assert "SECRET" not in str(raised.value)  # repositories are named by key, never URL
    install_units(
        _ctx("beta", tmp_path / "beta", "https://github.com/acme/gadgets"),
        repository_of=fleet_repository,
        dest=dest,
    )


def test_fleet_repository_ignores_this_process_clone_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FLEET_APP_REPO_URL", "https://github.com/acme/widgets")
    assert fleet_repository(tmp_path / "absent", "") is None


# --- repository identity -----------------------------------------------------------------


@pytest.mark.parametrize("url", _ADO_FORMS)
def test_every_ado_remote_form_names_one_repository(url: str) -> None:
    assert repository_identity(url) == _ADO_REPOSITORY


def test_an_ado_repository_named_after_its_project() -> None:
    assert repository_identity("https://dev.azure.com/org/_git/proj") == "org/proj/proj"
    assert repository_identity("https://dev.azure.com/org/proj/_git/proj") == "org/proj/proj"


def test_an_ado_project_name_is_percent_decoded() -> None:
    assert (
        repository_identity("https://dev.azure.com/org/My%20Project/_git/repo")
        == "org/my project/repo"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/Acme/Widgets.git",
        "git@github.com:acme/widgets",
        "ssh://git@ghe.example.com/acme/widgets.git",
        "https://x-access-token:SECRET@github.com/acme/widgets/",
    ],
)
def test_a_github_remote_is_the_key_github_repository_of_reads(url: str) -> None:
    assert repository_identity(url) == github_repository_of(url) == "acme/widgets"


def test_ado_and_github_repositories_never_collide() -> None:
    assert repository_identity("https://github.com/your-org/example-project") != (
        repository_identity("https://dev.azure.com/your-org/example-project/_git/example-project")
    )


@pytest.mark.parametrize(
    "url",
    ["/srv/local/clone", "file:///srv/clone.git", "https://gitlab.com/group/sub/repo.git", ""],
)
def test_an_unrecognised_remote_has_no_identity(url: str) -> None:
    assert repository_identity(url) is None


# --- the installed name (N35's residue) ---------------------------------------------------


def test_the_service_carries_the_name_it_was_installed_for() -> None:
    service: str = render_unit("squadra.service", _ctx("alpha", Path("/srv/alpha")))
    assert "Environment=FLEET_INSTALLED_NAME=alpha" in service


def _stub_fleet_tick(monkeypatch: pytest.MonkeyPatch, home: Path, name: str) -> list[list[str]]:
    monkeypatch.setenv("FLEET_KEY_VAULT", "the-vault")
    monkeypatch.setenv("FLEET_HOME", str(home))
    monkeypatch.setenv("FLEET_INSTALLED_NAME", "alpha")
    (home / "squadra.toml").write_text(f'[fleet]\nname = "{name}"\n', encoding="utf-8")
    fleet_secrets = secrets_module.FleetSecrets(anthropic_api_key="k", ado_pat="p")

    def _load(_access: object, _names: object = None) -> secrets_module.FleetSecrets:
        return fleet_secrets

    def _apply(_secrets: secrets_module.FleetSecrets) -> None:
        return None

    def _sync(*, mutate: bool = True) -> bool:
        return True

    ticked: list[list[str]] = []

    def _fake_main(argv: Sequence[str] | None = None) -> int:
        ticked.append(list(argv or []))
        return 0

    monkeypatch.setattr(secrets_module, "load_fleet_secrets", _load)
    monkeypatch.setattr(secrets_module, "apply_supervisor_environ", _apply)
    monkeypatch.setattr(repo_module, "ensure_app_repo_from_env", _sync)
    monkeypatch.setattr(real_supervisor, "main", _fake_main)
    return ticked


def test_fleet_tick_refuses_a_checkout_naming_another_fleet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ticked: list[list[str]] = _stub_fleet_tick(monkeypatch, tmp_path, "beta")

    assert cli.main(["fleet-tick", "--dry-run"]) == 2
    assert ticked == []
    err: str = capsys.readouterr().err
    assert "installed for fleet 'alpha'" in err
    assert "'beta'" in err


def test_fleet_tick_ticks_when_the_checkout_names_the_installed_fleet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticked: list[list[str]] = _stub_fleet_tick(monkeypatch, tmp_path, "alpha")

    assert cli.main(["fleet-tick", "--dry-run"]) == 0
    assert ticked == [["--dry-run"]]
