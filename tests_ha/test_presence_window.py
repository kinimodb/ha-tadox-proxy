"""Presence and window automations driven by real binary_sensor state changes."""
from __future__ import annotations

import pytest
from homeassistant.components.climate import (
    PRESET_AWAY,
    PRESET_BOOST,
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_NONE,
)
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN

from .helpers import (
    PRESENCE_AWAY_DELAY_S,
    PRESENCE_HOME_DELAY_S,
    PRESENCE_SENSOR,
    PRESET_FROST_PROTECTION,
    WINDOW_CLOSE_DELAY_S,
    WINDOW_SENSOR,
    Proxy,
    async_advance,
    async_open_window,
    async_set_state,
    async_setup_proxy,
)

BOOST_DURATION_MIN = 10


async def test_attribute_only_presence_updates_keep_away_timer(hass, freezer):
    """Attribute churn (e.g. a Zigbee sensor's linkquality) is not a new 'nobody home'."""
    proxy = await async_setup_proxy(hass)
    await async_set_state(hass, PRESENCE_SENSOR, STATE_OFF)

    # An attribute update every 20 s, past the away delay counted from "off".
    step_s = 20
    for i in range(PRESENCE_AWAY_DELAY_S // step_s + 1):
        await async_set_state(hass, PRESENCE_SENSOR, STATE_OFF, {"linkquality": 120 + i})
        await async_advance(hass, freezer, step_s)

    assert proxy.preset == PRESET_AWAY


@pytest.mark.parametrize("missing_state", [STATE_UNAVAILABLE, STATE_UNKNOWN, None])
async def test_presence_loss_during_home_delay_keeps_away(hass, freezer, missing_state):
    proxy = await async_setup_proxy(hass)
    await async_set_state(hass, PRESENCE_SENSOR, STATE_OFF)
    await async_advance(hass, freezer, PRESENCE_AWAY_DELAY_S + 1)
    assert proxy.preset == PRESET_AWAY

    await async_set_state(hass, PRESENCE_SENSOR, STATE_ON)
    await async_advance(hass, freezer, PRESENCE_HOME_DELAY_S - 1)
    if missing_state is None:
        hass.states.async_remove(PRESENCE_SENSOR)
        await hass.async_block_till_done()
    else:
        await async_set_state(hass, PRESENCE_SENSOR, missing_state)
    await async_advance(hass, freezer, PRESENCE_HOME_DELAY_S + 1)

    assert proxy.preset == PRESET_AWAY
    assert proxy.state.attributes["presence_away_active"] is True

    await async_set_state(hass, PRESENCE_SENSOR, STATE_ON)
    await async_advance(hass, freezer, PRESENCE_HOME_DELAY_S - 1)
    assert proxy.preset == PRESET_AWAY
    await async_advance(hass, freezer, 2)
    assert proxy.preset == PRESET_COMFORT


async def test_brief_presence_loss_restarts_home_delay(hass, freezer):
    proxy = await async_setup_proxy(hass)
    await async_set_state(hass, PRESENCE_SENSOR, STATE_OFF)
    await async_advance(hass, freezer, PRESENCE_AWAY_DELAY_S + 1)

    await async_set_state(hass, PRESENCE_SENSOR, STATE_ON)
    await async_advance(hass, freezer, 10)
    await async_set_state(hass, PRESENCE_SENSOR, STATE_UNAVAILABLE)
    await async_advance(hass, freezer, 1)
    await async_set_state(hass, PRESENCE_SENSOR, STATE_ON)
    await async_advance(hass, freezer, PRESENCE_HOME_DELAY_S - 1)

    assert proxy.preset == PRESET_AWAY
    await async_advance(hass, freezer, 2)
    assert proxy.preset == PRESET_COMFORT


async def test_turn_on_keeps_window_frost_protection(hass, freezer):
    proxy = await async_setup_proxy(hass)
    await async_open_window(hass, freezer)
    assert proxy.preset == PRESET_FROST_PROTECTION

    await proxy.call("climate", "turn_on", proxy.climate)

    assert proxy.preset == PRESET_FROST_PROTECTION
    assert proxy.state.attributes["window_open_active"] is True


async def test_close_delay_shows_right_away(hass, freezer):
    proxy = await async_setup_proxy(hass)
    await async_open_window(hass, freezer)

    await async_set_state(hass, WINDOW_SENSOR, STATE_OFF)
    assert proxy.state.attributes["window_close_delay_active"] is True

    # Opened again during the close delay: frost protection simply goes on.
    await async_set_state(hass, WINDOW_SENSOR, STATE_ON)
    assert proxy.state.attributes["window_close_delay_active"] is False
    assert proxy.state.attributes["window_open_active"] is True


async def test_presence_away_under_open_window_updates_restore_preset(hass, freezer):
    """The state shows Away as the preset to restore right away, ready for a restart."""
    proxy = await async_setup_proxy(hass)
    await async_open_window(hass, freezer)
    await async_set_state(hass, PRESENCE_SENSOR, STATE_OFF)
    await async_advance(hass, freezer, PRESENCE_AWAY_DELAY_S + 1)

    assert proxy.preset == PRESET_FROST_PROTECTION
    assert proxy.state.attributes["presence_away_active"] is True
    assert proxy.state.attributes["window_restore_preset"] == PRESET_AWAY


async def _async_close_window_while_away(hass, freezer, proxy: Proxy) -> None:
    """Nobody home, a window opened and closed: the close delay is running."""
    await async_set_state(hass, PRESENCE_SENSOR, STATE_OFF)
    await async_advance(hass, freezer, PRESENCE_AWAY_DELAY_S + 1)
    assert proxy.preset == PRESET_AWAY
    await async_open_window(hass, freezer)
    assert proxy.preset == PRESET_FROST_PROTECTION
    await async_set_state(hass, WINDOW_SENSOR, STATE_OFF)


async def test_preset_change_during_close_delay_brings_back_away(hass, freezer):
    """The change ends the close delay: Away comes back, not frost protection."""
    proxy = await async_setup_proxy(hass)
    await _async_close_window_while_away(hass, freezer, proxy)

    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)

    assert proxy.preset == PRESET_AWAY
    assert proxy.state.attributes["window_open_active"] is False
    assert proxy.state.attributes["window_close_delay_active"] is False
    assert proxy.state.attributes["presence_away_active"] is True

    # Eco was saved for the return.
    await async_set_state(hass, PRESENCE_SENSOR, STATE_ON)
    await async_advance(hass, freezer, PRESENCE_HOME_DELAY_S + 1)
    assert proxy.preset == PRESET_ECO


async def test_temperature_change_during_close_delay_brings_back_away(hass, freezer):
    proxy = await async_setup_proxy(hass)
    await _async_close_window_while_away(hass, freezer, proxy)

    await proxy.call("climate", "set_temperature", proxy.climate, temperature=21.5)

    assert proxy.preset == PRESET_AWAY
    assert proxy.state.attributes["window_open_active"] is False
    assert proxy.state.attributes["presence_away_active"] is True

    await async_set_state(hass, PRESENCE_SENSOR, STATE_ON)
    await async_advance(hass, freezer, PRESENCE_HOME_DELAY_S + 1)
    assert proxy.preset == PRESET_NONE
    assert proxy.target == 21.5


async def test_temperature_change_during_close_delay_takes_effect(hass, freezer):
    """Someone home: the manual temperature applies right away."""
    proxy = await async_setup_proxy(hass)
    await async_open_window(hass, freezer)
    await async_set_state(hass, WINDOW_SENSOR, STATE_OFF)

    await proxy.call("climate", "set_temperature", proxy.climate, temperature=21.5)

    assert proxy.preset == PRESET_NONE
    assert proxy.target == 21.5
    assert proxy.state.attributes["window_open_active"] is False


async def test_boost_during_close_delay_ends_in_the_saved_preset(hass, freezer):
    """After the boost, the room returns to what the window saved, not to frost protection."""
    proxy = await async_setup_proxy(hass, options={"boost_duration": BOOST_DURATION_MIN})
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)
    await async_open_window(hass, freezer)
    await async_set_state(hass, WINDOW_SENSOR, STATE_OFF)

    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_BOOST)
    assert proxy.preset == PRESET_BOOST

    await async_advance(hass, freezer, BOOST_DURATION_MIN * 60 + 1)
    assert proxy.preset == PRESET_ECO


async def test_temperature_while_away_with_an_open_window_waits_for_the_return(hass, freezer):
    """Nobody home: the window keeps Away to restore, the temperature waits for the return."""
    proxy = await async_setup_proxy(hass)
    await async_set_state(hass, PRESENCE_SENSOR, STATE_OFF)
    await async_advance(hass, freezer, PRESENCE_AWAY_DELAY_S + 1)
    await async_open_window(hass, freezer)

    await proxy.call("climate", "set_temperature", proxy.climate, temperature=21.5)

    assert proxy.preset == PRESET_FROST_PROTECTION
    assert proxy.state.attributes["window_restore_preset"] == PRESET_AWAY

    await async_set_state(hass, WINDOW_SENSOR, STATE_OFF)
    await async_advance(hass, freezer, WINDOW_CLOSE_DELAY_S + 1)
    assert proxy.preset == PRESET_AWAY

    await async_set_state(hass, PRESENCE_SENSOR, STATE_ON)
    await async_advance(hass, freezer, PRESENCE_HOME_DELAY_S + 1)
    assert proxy.preset == PRESET_NONE
    assert proxy.target == 21.5
