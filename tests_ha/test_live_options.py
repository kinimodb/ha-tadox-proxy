"""Option changes: number entities apply live, the options flow reloads."""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

from homeassistant.components.climate import PRESET_BOOST
from homeassistant.data_entry_flow import FlowResultType

from .helpers import PRESENCE_SENSOR, WINDOW_DELAY_S, WINDOW_SENSOR, async_setup_proxy


async def _async_submit_options_flow(hass, entry, *, window_delay_s: int) -> None:
    """Save the options dialog, changing only the window delay."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    user_input: dict[str, Any] = {
        "window_sensor": {"window_sensor_id": WINDOW_SENSOR, "window_delay_s": window_delay_s},
        "presence_sensor": {"presence_sensor_id": PRESENCE_SENSOR},
        "pi_controller": {},
        "gain_scheduling": {},
        "trv_communication": {},
        "behaviour": {},
    }
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def test_number_change_during_boost_does_not_reload(hass):
    proxy = await async_setup_proxy(hass)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_BOOST)

    with patch.object(hass.config_entries, "async_reload") as reload:
        await proxy.call("number", "set_value", proxy.number("eco_target"), value=16.0)

    reload.assert_not_called()
    assert proxy.entry.options["eco_target"] == 16.0
    # A reload would have ended the boost.
    assert proxy.preset == PRESET_BOOST


async def test_comfort_number_change_applies_live(hass):
    proxy = await async_setup_proxy(hass)

    await proxy.call("number", "set_value", proxy.number("comfort_target"), value=21.5)

    assert proxy.target == 21.5


async def test_options_flow_change_reloads(hass):
    proxy = await async_setup_proxy(hass)
    # The first save adds every option the dialog shows; let that reload pass.
    await _async_submit_options_flow(hass, proxy.entry, window_delay_s=WINDOW_DELAY_S)

    with patch.object(hass.config_entries, "async_reload") as reload:
        await _async_submit_options_flow(hass, proxy.entry, window_delay_s=WINDOW_DELAY_S + 15)

    reload.assert_called_once_with(proxy.entry.entry_id)
    assert proxy.entry.options["window_delay_s"] == WINDOW_DELAY_S + 15
