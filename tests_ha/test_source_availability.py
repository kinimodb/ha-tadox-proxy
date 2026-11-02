"""Availability of the proxy when its source thermostat disappears."""
from __future__ import annotations

import pytest
from homeassistant.components.climate import PRESET_ECO, PRESET_NONE, HVACMode
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN

from .helpers import (
    ROOM_SENSOR,
    TRV,
    WINDOW_DELAY_S,
    WINDOW_SENSOR,
    async_advance,
    async_set_state,
    async_setup_proxy,
)

# DEFAULT_CONTROL_INTERVAL_S – the regulation cycle runs on this timer.
CONTROL_INTERVAL_S = 60
# BehaviourConfig.follow_grace_s
FOLLOW_GRACE_S = 20
# DEFAULT_SENSOR_GRACE_S
SENSOR_GRACE_S = 300
# Setpoints the TRV reports in the Eco test: far above every Eco command (17 °C
# in a 20 °C room), so each one differs from it by more than the follow threshold.
RETURNED_C = 25.0
DIALLED_C = 23.0


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
    """The source reports its setpoint again on its return: nobody turned a dial.

    The guard is only needed with a baseline (a command the proxy sent) and no
    command at the return: a command there is already shielded by the follow
    grace.  With the room sensor gone for longer than its grace period the
    proxy holds its last command and sends nothing, which is exactly that.
    Turning the proxy Off would not do: that clears the baseline, and without
    one no dial is followed at all, return or not.
    """
    proxy = await async_setup_proxy(
        hass, options={"follow_tado_input": True}, trv_adopts=True
    )
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)
    await async_set_state(hass, ROOM_SENSOR, STATE_UNAVAILABLE)
    for _ in range(SENSOR_GRACE_S // CONTROL_INTERVAL_S + 2):
        await async_advance(hass, freezer, CONTROL_INTERVAL_S)
    assert proxy.state.attributes["active_control_mode"] == "hold"

    await async_set_state(hass, TRV, lost_state)
    await async_advance(hass, freezer, FOLLOW_GRACE_S + 1)
    sent_before = len(proxy.trv_commands("set_temperature"))

    await async_set_state(
        hass, TRV, HVACMode.HEAT,
        {"current_temperature": 22.0, "temperature": RETURNED_C},
    )

    assert proxy.state.state == HVACMode.HEAT
    # The premise: the cycle at the return held back, no command shields the report.
    assert proxy.state.attributes["regulation_reason"] == "waiting_for_sensors"
    assert len(proxy.trv_commands("set_temperature")) == sent_before
    assert proxy.preset == PRESET_ECO
    assert proxy.state.attributes["user_preset"] == PRESET_ECO

    # A dial turned after the return still counts.
    await async_advance(hass, freezer, FOLLOW_GRACE_S + 1)
    await async_set_state(
        hass, TRV, HVACMode.HEAT,
        {"current_temperature": 22.0, "temperature": DIALLED_C},
    )

    assert proxy.state.attributes["user_preset"] == PRESET_NONE
    assert proxy.state.attributes["manual_target_c"] == DIALLED_C
