"""Guest mode: the switch suspends the presence-away automation."""
from __future__ import annotations

from homeassistant.components.climate import PRESET_AWAY, PRESET_COMFORT, PRESET_ECO
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import State
from pytest_homeassistant_custom_component.common import mock_restore_cache

from .helpers import (
    CLIMATE,
    PRESENCE_AWAY_DELAY_S,
    PRESENCE_SENSOR,
    async_advance,
    async_set_state,
    async_setup_proxy,
)


async def test_switch_exists_and_starts_off(hass):
    proxy = await async_setup_proxy(hass)

    assert hass.states.get(proxy.guest_switch).state == STATE_OFF
    assert proxy.state.attributes["guest_mode"] is False


async def test_nobody_home_does_not_switch_to_away(hass, freezer):
    proxy = await async_setup_proxy(hass)
    await proxy.call("switch", "turn_on", proxy.guest_switch)
    assert hass.states.get(proxy.guest_switch).state == STATE_ON

    await async_set_state(hass, PRESENCE_SENSOR, STATE_OFF)
    await async_advance(hass, freezer, 2 * PRESENCE_AWAY_DELAY_S)

    assert proxy.preset == PRESET_COMFORT
    assert proxy.state.attributes["presence_away_active"] is False


async def test_turning_on_while_away_restores_previous_preset(hass, freezer):
    proxy = await async_setup_proxy(hass)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)
    await async_set_state(hass, PRESENCE_SENSOR, STATE_OFF)
    await async_advance(hass, freezer, PRESENCE_AWAY_DELAY_S + 1)
    assert proxy.preset == PRESET_AWAY

    await proxy.call("switch", "turn_on", proxy.guest_switch)

    # Immediately, without waiting for the home delay.
    assert proxy.preset == PRESET_ECO
    assert proxy.state.attributes["presence_away_active"] is False
    assert hass.states.get(proxy.guest_switch).state == STATE_ON


async def test_turning_off_while_nobody_home_rearms_away(hass, freezer):
    proxy = await async_setup_proxy(hass)
    await proxy.call("switch", "turn_on", proxy.guest_switch)
    await async_set_state(hass, PRESENCE_SENSOR, STATE_OFF)

    await proxy.call("switch", "turn_off", proxy.guest_switch)
    assert hass.states.get(proxy.guest_switch).state == STATE_OFF
    assert proxy.preset == PRESET_COMFORT

    await async_advance(hass, freezer, PRESENCE_AWAY_DELAY_S + 1)
    assert proxy.preset == PRESET_AWAY


async def test_survives_restart(hass, freezer):
    mock_restore_cache(
        hass,
        [State(CLIMATE, "heat", {"temperature": 20.0, "preset_mode": PRESET_COMFORT, "guest_mode": True})],
    )
    proxy = await async_setup_proxy(hass, presence=STATE_OFF)

    assert proxy.climate == CLIMATE
    assert hass.states.get(proxy.guest_switch).state == STATE_ON
    assert proxy.state.attributes["guest_mode"] is True

    await async_advance(hass, freezer, 2 * PRESENCE_AWAY_DELAY_S)
    assert proxy.preset == PRESET_COMFORT
