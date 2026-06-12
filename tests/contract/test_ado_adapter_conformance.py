"""Run a representative subset of the contract against the SHIPPED ``AzCliAdo``.

This proves the real adapter already satisfies the seam — not just the fakes.
The az transport is replaced by a canned ``run`` callable returning realistic
JSON for the exact subcommands ``AzCliAdo`` issues (WIQL + workitemsbatch for
``items_in_state``; ``boards work-item show`` for ``item_state``;
``workItemTypeStates`` for ``validate_config``).

Coverage is intentionally scoped to ``items_in_state(DONE)``, ``item_state``,
and ``validate_config`` (pass + raise) per the task brief. ``item_links`` /
``completed_pr_url`` / mutation methods are covered by the existing
``tests/test_supervisor_adapters.py`` against the same adapter and are not
re-faked here.
"""

from collections.abc import Sequence
import json

import pytest

from flotilla.board import AzCliAdo, BoardValidationError
from flotilla.config import ADO_BASIC_STATES
from flotilla.domain import Lifecycle, WorkItem

# native ADO-Basic done state ("Done") that DONE maps onto
_DONE_ITEM_ID: int = 77


class _CannedAz:
    """Canned az stdout keyed by the resource / subcommand in the argv.

    ``available_states`` controls what ``workItemTypeStates`` advertises, so a
    single runner can drive both the passing and the mismatch validate_config
    cases.
    """

    def __init__(self, *, available_states: tuple[str, ...]) -> None:
        self._available = available_states
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> str:
        arglist: list[str] = list(args)
        self.calls.append(arglist)
        joined: str = " ".join(arglist)

        if "--resource wiql" in joined:
            return json.dumps({"workItems": [{"id": _DONE_ITEM_ID}]})
        if "--resource workitemsbatch" in joined:
            return json.dumps(
                {
                    "value": [
                        {
                            "id": _DONE_ITEM_ID,
                            "fields": {
                                "System.Id": _DONE_ITEM_ID,
                                "System.Title": "feat: shipped slice",
                                "System.Tags": "fleet:claimed; ready",
                            },
                        }
                    ]
                }
            )
        if "--resource workItemTypeStates" in joined:
            return json.dumps({"value": [{"name": name} for name in self._available]})
        if "boards work-item show" in joined:
            return json.dumps({"fields": {"System.State": "Done"}})
        return "{}"


def _adapter(available_states: tuple[str, ...]) -> AzCliAdo:
    return AzCliAdo(
        run=_CannedAz(available_states=available_states),
        project="proj",
        states=ADO_BASIC_STATES,
    )


def test_items_in_state_done_returns_the_done_workitem() -> None:
    items: tuple[WorkItem, ...] = _adapter(("To Do", "Doing", "Done")).items_in_state(
        Lifecycle.DONE
    )
    assert len(items) == 1
    assert items[0].item_id == _DONE_ITEM_ID
    assert items[0].title == "feat: shipped slice"
    assert "fleet:claimed" in items[0].tags


def test_item_state_maps_native_done_to_lifecycle_done() -> None:
    assert _adapter(("To Do", "Doing", "Done")).item_state(_DONE_ITEM_ID) == Lifecycle.DONE


def test_validate_config_passes_when_board_advertises_configured_states() -> None:
    _adapter(("To Do", "Doing", "Done")).validate_config()  # must not raise


def test_validate_config_raises_when_a_configured_state_is_absent() -> None:
    # board is missing "Done" → preflight must fail loud
    with pytest.raises(BoardValidationError):
        _adapter(("To Do", "Doing")).validate_config()
