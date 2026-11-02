"""Availability of the proxy when its source thermostat disappears."""
from __future__ import annotations

import pytest
from homeassistant.components.climate import PRESET_ECO, HVACMode
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN

from .helpers import (
    TRV,
    WINDOW_DELAY_S,
    WINDOW_SENSOR,
    async_advance,
    async_set_state,
    async_setup_proxy,
)

# BehaviourConfig.follow_grace_s
FOLLOW_GRACE_S = 20


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


@pytest.mark.parametrize("lost_state", [STATE_UNAVAILABLE, STATE_UNKNOWN])
async def test_source_coming_back_is_not_a_dial_change(hass, freezer, lost_state):
    """Heating off, so no command after the return shields it by the follow grace."""
    proxy = await async_setup_proxy(hass, options={"follow_tado_input": True})
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)
    await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.OFF)
    await async_set_state(hass, TRV, lost_state)
    await async_advance(hass, freezer, FOLLOW_GRACE_S + 1)

    await async_set_state(
        hass, TRV, HVACMode.OFF,
        {"current_temperature": 22.0, "temperature": 25.0},
    )

    assert proxy.state.state == HVACMode.OFF
    assert proxy.preset == PRESET_ECO
    assert proxy.state.attributes["user_preset"] == PRESET_ECO

    # A dial turned after the return still counts.
    await async_advance(hass, freezer, FOLLOW_GRACE_S + 1)
    await async_set_state(
        hass, TRV, HVACMode.OFF,
        {"current_temperature": 22.0, "temperature": 23.0},
    )

    assert proxy.state.attributes["user_preset"] == "none"
    assert proxy.state.attributes["manual_target_c"] == 23.0
