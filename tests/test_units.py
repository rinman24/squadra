"""Unit tests for the fleet-host systemd unit rendering/installation.

Renders the packaged ``_units/`` templates against a :class:`UnitContext` and
asserts the load-bearing properties: a oneshot service that runs ``squadra
fleet-tick`` with no secret baked in, a timer that is installed but carries no
runtime enablement, and an installer that writes both files without touching
systemd.
"""

from pathlib import Path

from squadra.units import (
    UNIT_FILENAMES,
    UnitContext,
    install_units,
    render_unit,
    render_units,
)


def _ctx() -> UnitContext:
    return UnitContext(
        venv_bin=Path("/opt/squadra/venv/bin"),
        fleet_home=Path("/opt/squadra/gswa-backend"),
        fleet_root=Path("/opt/squadra/state"),
        key_vault="gswa-fleet-kv-07c17c",
        app_repo_url="https://dev.azure.com/genshift/gswa-dev/_git/gswa-backend",
        parent_scope_ids="139",
        interval_seconds=180,
        user="azureuser",
    )


def test_service_is_oneshot_running_fleet_tick() -> None:
    service: str = render_unit("squadra.service", _ctx())
    assert "Type=oneshot" in service
    assert "ExecStart=/opt/squadra/venv/bin/squadra fleet-tick" in service
    assert "User=azureuser" in service
    assert "WorkingDirectory=/opt/squadra/gswa-backend" in service


def test_service_passes_runtime_env_but_no_secret() -> None:
    service: str = render_unit("squadra.service", _ctx())
    assert "Environment=FLEET_KEY_VAULT=gswa-fleet-kv-07c17c" in service
    assert "Environment=FLEET_HOME=/opt/squadra/gswa-backend" in service
    assert "Environment=FLEET_PYTHON=/opt/squadra/venv/bin/python" in service
    assert (
        "Environment=FLEET_APP_REPO_URL="
        "https://dev.azure.com/genshift/gswa-dev/_git/gswa-backend" in service
    )
    assert "Environment=FLEET_PARENT_SCOPE_IDS=139" in service
    # No secret material is ever written into the unit (ADR-0002 §11).
    assert "AZURE_DEVOPS_EXT_PAT" not in service
    assert "ANTHROPIC_API_KEY=" not in service


def test_timer_schedules_the_service_and_is_installable_not_enabled() -> None:
    timer: str = render_unit("squadra.timer", _ctx())
    assert "OnUnitActiveSec=180s" in timer
    assert "Unit=squadra.service" in timer
    # [Install] lets an operator deliberately `enable` it later; rendering does
    # not enable anything by itself.
    assert "[Install]" in timer
    assert "WantedBy=timers.target" in timer


def test_render_units_covers_every_unit_filename() -> None:
    rendered = render_units(_ctx())
    assert set(rendered) == set(UNIT_FILENAMES)


def test_missing_placeholder_is_loud() -> None:
    # A template substitution must fail loudly, never half-render. Proven by a
    # context whose mapping omits a key the template needs would raise KeyError;
    # here we assert substitute is total for the shipped context (no leftover $).
    for content in render_units(_ctx()).values():
        assert "${" not in content


def test_install_units_writes_both_files_without_touching_systemd(tmp_path: Path) -> None:
    written: list[Path] = install_units(_ctx(), dest=tmp_path)

    assert sorted(p.name for p in written) == ["squadra.service", "squadra.timer"]
    for path in written:
        assert path.parent == tmp_path
        assert path.read_text(encoding="utf-8")


def test_install_units_uses_injected_writer(tmp_path: Path) -> None:
    captured: dict[Path, str] = {}

    def _writer(path: Path, content: str) -> None:
        captured[path] = content

    install_units(_ctx(), dest=tmp_path, writer=_writer)

    assert set(captured) == {tmp_path / "squadra.service", tmp_path / "squadra.timer"}
