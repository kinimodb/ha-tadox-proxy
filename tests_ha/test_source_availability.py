"""Availability of the proxy when its source thermostat disappears."""
from __future__ import annotations

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN

from .helpers import (
    TRV,
    WINDOW_DELAY_S,
    WINDOW_SENSOR,
    async_advance,
    async_set_state,
    async_setup_proxy,
)


@pytest.mark.parametrize("lost_state", [STATE_UNAVAILABLE, STATE_UNKNOWN, None])
async def test_source_loss_marks_proxy_unavailable_and_recovers(hass, freezer, lost_state):
    proxy = await async_setup_proxy(hass)
    assert proxy.state.state == HVACMode.HEAT

    if lost_state is None:
        hass.states.async_remove(TRV)
        await hass.async_block_till_done()
    else:
        await async_set_state(hass, TRV, lost_state)

    assert proxy.state.state == STATE_UNAVAILABLE
    command_count = len(proxy.trv_commands("set_temperature"))
    await async_advance(hass, freezer, 61)
    assert len(proxy.trv_commands("set_temperature")) == command_count

    await async_set_state(
        hass, TRV, HVACMode.HEAT,
        {"current_temperature": 22.0, "temperature": 20.0},
    )

    assert proxy.state.state == HVACMode.HEAT
    assert proxy.state.attributes["regulation_reason"] != "source_unavailable"


async def test_source_off_is_not_unavailable(hass):
    proxy = await async_setup_proxy(hass)
    await async_set_state(
        hass, TRV, HVACMode.OFF,
        {"current_temperature": 22.0, "temperature": 20.0},
    )
    assert proxy.state.state == HVACMode.HEAT


async def test_source_unavailable_at_startup(hass):
    proxy = await async_setup_proxy(hass, source_state=STATE_UNAVAILABLE)
    assert proxy.state.state == STATE_UNAVAILABLE


async def test_window_action_does_not_command_unavailable_source(hass, freezer):
    proxy = await async_setup_proxy(hass, options={"window_action": "off"})
    await async_set_state(hass, TRV, STATE_UNAVAILABLE)
    await async_set_state(hass, WINDOW_SENSOR, STATE_ON)
    await async_advance(hass, freezer, WINDOW_DELAY_S + 1)

    assert proxy.state.state == STATE_UNAVAILABLE
    assert proxy.trv_commands("set_hvac_mode") == []

    await async_set_state(
        hass, TRV, HVACMode.HEAT,
        {"current_temperature": 22.0, "temperature": 20.0},
    )
    assert proxy.state.state == HVACMode.HEAT
    assert proxy.trv_commands("set_hvac_mode")[-1]["hvac_mode"] == HVACMode.OFF
