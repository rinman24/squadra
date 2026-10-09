"""Contract test: the real ``squadra tick --dry-run`` honours claim scope and withdrawal.

End to end through the CLI: ``squadra tick --dry-run`` → ``supervisor.main`` →
``load_config`` (reading ``squadra.toml``) → ``build_seams(dry_run=True)`` →
``run_tick``. Only the provider substrates are replaced: ``build_board`` returns
an in-memory board fixture and ``ComposeSandbox`` an in-memory sandbox, so no
az / docker / git runs. Everything between them is production code.

The board fixture holds one item per scope case (WSQ1, WD18):

- #5 in scope, unblocked — the only item the tick may claim;
- #6 in scope, blocked on #5 — reported blocked;
- #7 out of scope, unblocked — reported out of scope, not blocked;
- #8 withdrawn, in scope — never claimed, never counted done;
- #9 in scope, behind withdrawn #8 — reported predecessor-withdrawn, not blocked;
- #40 merged, fleet-claimed, reparented out of scope — still finalizes;
- #41 crashed in flight, fleet-claimed, reparented out of scope — still reaped.

The runner cap is raised well above the candidate count so the claim budget
cannot mask a scope leak: if scope failed open, #7 would be claimed too, and if
withdrawal read as queued or done, #8 or #9 would be.
"""

from collections.abc import Callable
import copy
from pathlib import Path
import re

import pytest

from squadra import cli, supervisor
import squadra.config as config_module
from squadra.config import SquadraConfig
from squadra.domain import Lifecycle, SandboxExited
from squadra.status import FleetStatus, write
from tests.helpers.fleet_fakes import FakeBoard, FakeIssue
from tests.helpers.sandbox_fakes import FakeSandbox

# fake_board, make_issue, fake_sandbox, make_status are provided by tests/conftest.py

_ANCIENT: str = "2020-01-01T00:00:00+00:00"  # stale for any real clock
_MUTATING_CALLS: tuple[str, ...] = ("set_state", "add_tag", "remove_tag", "add_comment")
_IN_SCOPE_PARENT: int = 68
_OTHER_PARENT: int = 99


def _would(pattern: str, out: str) -> set[int]:
    """The item ids a ``[dry-run] WOULD …`` line names, for one line pattern."""
    return {int(found) for found in re.findall(pattern, out)}


def _reported_states(out: str) -> dict[int, str]:
    """Parse the tick's ``states={id: 'state', …}`` report line."""
    match = re.search(r"states=\{([^}]*)\}", out)
    assert match is not None, out
    return {int(k): v for k, v in re.findall(r"(\d+): '([a-z-]+)'", match.group(1))}


def test_tick_dry_run_claims_exactly_the_in_scope_unblocked_items(
    tmp_path: Path,
    fake_board: FakeBoard,
    fake_sandbox: FakeSandbox,
    make_issue: Callable[..., FakeIssue],
    make_status: Callable[..., FleetStatus],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fleet_home: Path = tmp_path / "home"
    fleet_root: Path = tmp_path / "fleet"
    fleet_home.mkdir()
    (fleet_home / "squadra.toml").write_text(
        "[board]\n"
        'provider = "ado"\n'
        'claim_scope = "parents"\n'
        f"parent_scope_ids = [{_IN_SCOPE_PARENT}]\n",
        encoding="utf-8",
    )
    for var in ("FLEET_PROVIDER", "FLEET_TAG_PREFIX", "FLEET_BASE_BRANCH", "FLEET_DRY_RUN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config_module, "FLEET_MAX_RUNNERS", 10)

    # Queued candidates.
    make_issue(5, title="feat: in scope", parent_id=_IN_SCOPE_PARENT)
    make_issue(6, title="feat: blocked", parent_id=_IN_SCOPE_PARENT, predecessor_ids=(5,))
    make_issue(7, title="feat: not ours", parent_id=_OTHER_PARENT)
    make_issue(8, title="feat: withdrawn", state=Lifecycle.WITHDRAWN, parent_id=_IN_SCOPE_PARENT)
    make_issue(9, title="feat: stranded", parent_id=_IN_SCOPE_PARENT, predecessor_ids=(8,))
    # In flight, then reparented out of scope: merged (finalize) and crashed (reap).
    make_issue(
        40,
        title="feat: merged",
        state=Lifecycle.DONE,
        tags=["fleet:claimed"],
        parent_id=_OTHER_PARENT,
    )
    write(
        make_status(issue_id=40, runner_id="r-40", branch="feat/increment-40-merged"),
        fleet_root,
    )
    fake_board.completed_prs["feat/increment-40-merged"] = "https://pr/40"
    make_issue(
        41,
        title="feat: example",
        state=Lifecycle.ACTIVE,
        tags=["fleet:claimed"],
        parent_id=_OTHER_PARENT,
    )
    write(make_status(phase="tdd", last_heartbeat=_ANCIENT), fleet_root)
    fake_sandbox.seed("squadra-increment-41", SandboxExited(exit_code=1))

    def _build_board(_config: SquadraConfig) -> FakeBoard:
        return fake_board

    def _build_sandbox() -> FakeSandbox:
        return fake_sandbox

    monkeypatch.setattr(supervisor, "build_board", _build_board)
    monkeypatch.setattr(supervisor, "ComposeSandbox", _build_sandbox)
    issues_before = copy.deepcopy(fake_board.issues)

    rc: int = cli.main(
        ["tick", "--dry-run", "--fleet-home", str(fleet_home), "--fleet-root", str(fleet_root)]
    )

    assert rc == 0
    out: str = capsys.readouterr().out
    # The would-claim set is exactly the in-scope, unblocked items.
    assert _would(r"WOULD move #(\d+) to active", out) == {5}
    assert _would(r"WOULD launch sandbox squadra-increment-(\d+)", out) == {5}
    assert _would(r"WOULD add tag 'fleet:claimed' to #(\d+)", out) == {5}
    # Out of scope is reported as such, never as blocked; in-scope blocked stays blocked.
    states: dict[int, str] = _reported_states(out)
    assert states[5] == "claimable"
    assert states[6] == "blocked"
    assert states[7] == "out-of-scope"
    # Withdrawn is neither claimable nor done; its successor is stranded, not "blocked".
    assert 8 not in states
    assert states[9] == "predecessor-withdrawn"
    # The reparented in-flight items are still driven: #40 finalizes, #41 is reaped.
    assert states[40] == "finalizing"
    assert "WOULD delete branch 'feat/increment-40-merged'" in out
    assert "WOULD remove tag 'fleet:claimed' from #40" in out
    assert states[41] == "agent-failed"
    assert "WOULD archive worktree" in out
    assert "WOULD move #41 to queued" in out
    # And it is still a dry run: nothing on the board changed.
    assert [call for call in fake_board.calls if call[0] in _MUTATING_CALLS] == []
    assert fake_board.issues == issues_before
    assert fake_sandbox.launches == []
