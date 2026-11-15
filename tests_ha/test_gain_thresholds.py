"""The options dialog checks the two thresholds of adaptive gain scheduling."""
from __future__ import annotations

import pytest
from homeassistant.data_entry_flow import FlowResultType

from .helpers import CONTROL_INTERVAL_S, async_advance, async_setup_proxy, async_submit_options

# Stored by a dialog that did not check yet (v1.6.0 and earlier).
INVERTED = {"gain_fine_threshold_c": 1.5, "gain_startup_threshold_c": 1.0}


@pytest.mark.parametrize("near_target_c", [1.0, 1.5])
async def test_near_target_threshold_must_be_below_the_cold_start_threshold(hass, near_target_c):
    proxy = await async_setup_proxy(hass)

    result = await async_submit_options(
        hass,
        proxy.entry,
        gain_scheduling={
            "gain_fine_threshold_c": near_target_c,
            "gain_startup_threshold_c": 1.0,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "gain_thresholds"}
    assert "gain_fine_threshold_c" not in proxy.entry.options
    assert "gain_startup_threshold_c" not in proxy.entry.options


async def test_thresholds_in_order_are_saved(hass):
    proxy = await async_setup_proxy(hass)

    result = await async_submit_options(
        hass,
        proxy.entry,
        gain_scheduling={"gain_fine_threshold_c": 0.9, "gain_startup_threshold_c": 1.0},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert proxy.entry.options["gain_fine_threshold_c"] == 0.9
    assert proxy.entry.options["gain_startup_threshold_c"] == 1.0


async def test_thresholds_are_not_checked_while_gain_scheduling_is_off(hass):
    """They have no effect then, so they must not block the dialog."""
    proxy = await async_setup_proxy(hass)

    result = await async_submit_options(
        hass, proxy.entry, gain_scheduling={"gain_scheduling_enabled": False, **INVERTED}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_room_with_stored_inverted_thresholds_still_regulates(hass, freezer):
    """The check is in the dialog only: nothing stored is refused or changed."""
    proxy = await async_setup_proxy(hass, options=INVERTED, room_state="19.0")
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)

    assert proxy.entry.options["gain_fine_threshold_c"] == 1.5
    assert proxy.state.attributes["gain_fine_threshold_c"] == 1.5
    assert proxy.state.attributes["gain_startup_threshold_c"] == 1.0
    assert len(proxy.trv_commands("set_temperature")) == 1

    # The dialog shows the stored pair and asks for a correction on saving.
    result = await async_submit_options(hass, proxy.entry)
    assert result["errors"] == {"base": "gain_thresholds"}
    result = await async_submit_options(
        hass, proxy.entry, gain_scheduling={"gain_fine_threshold_c": 0.5}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
