"""Window action "off": the TRV is switched off while the window is open."""
from __future__ import annotations

import pytest
from homeassistant.components.climate import (
    PRESET_AWAY,
    PRESET_COMFORT,
    PRESET_ECO,
    HVACAction,
    HVACMode,
)
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.data_entry_flow import FlowResultType

from .helpers import (
    PRESENCE_AWAY_DELAY_S,
    PRESENCE_SENSOR,
    PRESET_FROST_PROTECTION,
    WINDOW_DELAY_S,
    WINDOW_SENSOR,
    async_advance,
    async_set_state,
    async_setup_proxy,
)

OFF_ACTION = {"window_action": "off"}
# DEFAULT_CONTROL_INTERVAL_S – the regulation cycle runs on this timer.
CONTROL_INTERVAL_S = 60
# window_close_delay_s as set in BASE_OPTIONS
WINDOW_CLOSE_DELAY_S = 120


async def _async_open_window(hass, freezer) -> None:
    await async_set_state(hass, WINDOW_SENSOR, STATE_ON)
    await async_advance(hass, freezer, WINDOW_DELAY_S + 1)


async def test_open_window_switches_the_trv_off(hass, freezer):
    proxy = await async_setup_proxy(hass, options=OFF_ACTION)

    await _async_open_window(hass, freezer)

    assert proxy.preset == PRESET_FROST_PROTECTION
    assert proxy.trv_commands("set_hvac_mode")[-1]["hvac_mode"] == HVACMode.OFF
    assert proxy.state.attributes["hvac_action"] == HVACAction.OFF


async def test_trv_stays_off_while_the_window_is_open(hass, freezer):
    """Every setpoint command carries hvac_mode=heat, so none may be sent."""
    proxy = await async_setup_proxy(hass, options=OFF_ACTION)
    await _async_open_window(hass, freezer)
    sent = len(proxy.trv_commands("set_temperature"))

    for _ in range(5):
        await async_advance(hass, freezer, CONTROL_INTERVAL_S)

    assert len(proxy.trv_commands("set_temperature")) == sent
    assert proxy.state.attributes["regulation_reason"] == "window_off"


async def test_closed_window_switches_the_trv_back_on(hass, freezer):
    proxy = await async_setup_proxy(hass, options=OFF_ACTION)
    await _async_open_window(hass, freezer)

    await async_set_state(hass, WINDOW_SENSOR, STATE_OFF)
    await async_advance(hass, freezer, WINDOW_CLOSE_DELAY_S + 1)

    assert proxy.preset == PRESET_COMFORT
    assert proxy.trv_commands("set_hvac_mode")[-1]["hvac_mode"] == HVACMode.HEAT
    assert proxy.state.attributes["regulation_reason"] != "window_off"


async def test_change_during_close_delay_switches_the_trv_back_on(hass, freezer):
    """The change takes effect right away, so the TRV must heat again."""
    proxy = await async_setup_proxy(hass, options=OFF_ACTION)
    await _async_open_window(hass, freezer)
    await async_set_state(hass, WINDOW_SENSOR, STATE_OFF)

    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)
    await hass.async_block_till_done()

    assert proxy.preset == PRESET_ECO
    assert proxy.trv_commands("set_hvac_mode")[-1]["hvac_mode"] == HVACMode.HEAT
    assert proxy.state.attributes["hvac_action"] != HVACAction.OFF


@pytest.mark.parametrize(
    ("service", "data"),
    [
        ("set_preset_mode", {"preset_mode": PRESET_ECO}),
        ("set_temperature", {"temperature": 21.5}),
    ],
)
async def test_change_during_close_delay_while_away_switches_the_trv_back_on(
    hass, freezer, service, data
):
    """Nobody home: Away comes back and the TRV heats again right away."""
    proxy = await async_setup_proxy(hass, options=OFF_ACTION)
    await async_set_state(hass, PRESENCE_SENSOR, STATE_OFF)
    await async_advance(hass, freezer, PRESENCE_AWAY_DELAY_S + 1)
    await _async_open_window(hass, freezer)
    await async_set_state(hass, WINDOW_SENSOR, STATE_OFF)
    assert proxy.trv_commands("set_hvac_mode")[-1]["hvac_mode"] == HVACMode.OFF

    await proxy.call("climate", service, proxy.climate, **data)

    assert proxy.preset == PRESET_AWAY
    assert proxy.trv_commands("set_hvac_mode")[-1]["hvac_mode"] == HVACMode.HEAT
    assert proxy.state.attributes["hvac_action"] != HVACAction.OFF


async def test_frost_protection_stays_the_default(hass, freezer):
    proxy = await async_setup_proxy(hass)

    await _async_open_window(hass, freezer)

    assert proxy.trv_commands("set_hvac_mode") == []
    assert proxy.trv_commands("set_temperature")


async def test_window_action_is_set_in_the_options(hass):
    proxy = await async_setup_proxy(hass)

    result = await hass.config_entries.options.async_init(proxy.entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "window_sensor": {"window_sensor_id": WINDOW_SENSOR, "window_action": "off"},
            "presence_sensor": {"presence_sensor_id": PRESENCE_SENSOR},
            "pi_controller": {},
            "gain_scheduling": {},
            "trv_communication": {},
            "behaviour": {},
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert proxy.entry.options["window_action"] == "off"
