"""Boost: counts down only while it heats the room, then the user choice comes back."""
from __future__ import annotations

from homeassistant.components.climate import PRESET_AWAY, PRESET_BOOST, PRESET_ECO
from homeassistant.const import STATE_OFF, STATE_ON

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


def _remaining_min(proxy: Proxy) -> int:
    """The boost remaining sensor."""
    state = proxy.hass.states.get(proxy.entity_id("sensor", "boost_remaining"))
    assert state is not None
    return int(float(state.state))


async def test_boost_ends_in_the_user_choice(hass, freezer):
    proxy = await async_setup_proxy(hass, options={"boost_duration": BOOST_DURATION_MIN})
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_BOOST)
    assert proxy.state.attributes["user_preset"] == PRESET_ECO

    await async_advance(hass, freezer, BOOST_DURATION_MIN * 60 + 1)

    assert proxy.preset == PRESET_ECO


async def test_boost_pauses_behind_an_open_window_and_continues_after_it_closes(hass, freezer):
    proxy = await async_setup_proxy(hass, options={"boost_duration": BOOST_DURATION_MIN})
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_BOOST)
    await async_advance(hass, freezer, 4 * 60)

    # 4 min used, plus the 30 s open delay: 5.5 min left, standing still.
    await async_open_window(hass, freezer)
    assert proxy.preset == PRESET_FROST_PROTECTION
    assert _remaining_min(proxy) == 6
    await async_advance(hass, freezer, 30 * 60)
    assert proxy.preset == PRESET_FROST_PROTECTION
    assert _remaining_min(proxy) == 6

    await async_set_state(hass, WINDOW_SENSOR, STATE_OFF)
    await async_advance(hass, freezer, WINDOW_CLOSE_DELAY_S + 1)
    assert proxy.preset == PRESET_BOOST

    await async_advance(hass, freezer, 5 * 60)
    assert proxy.preset == PRESET_BOOST
    await async_advance(hass, freezer, 31)
    assert proxy.preset == PRESET_ECO


async def test_boost_picked_while_nobody_is_home_runs_its_full_time_on_return(hass, freezer):
    proxy = await async_setup_proxy(hass, options={"boost_duration": BOOST_DURATION_MIN})
    await async_set_state(hass, PRESENCE_SENSOR, STATE_OFF)
    await async_advance(hass, freezer, PRESENCE_AWAY_DELAY_S + 1)

    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_BOOST)
    assert proxy.preset == PRESET_AWAY
    await async_advance(hass, freezer, 2 * BOOST_DURATION_MIN * 60)

    await async_set_state(hass, PRESENCE_SENSOR, STATE_ON)
    await async_advance(hass, freezer, PRESENCE_HOME_DELAY_S + 1)
    assert proxy.preset == PRESET_BOOST
    assert _remaining_min(proxy) == BOOST_DURATION_MIN
