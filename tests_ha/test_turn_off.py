"""Turning the proxy off: window and presence detection keep running, a boost pauses."""
from __future__ import annotations

from homeassistant.components.climate import (
    PRESET_BOOST,
    PRESET_COMFORT,
    PRESET_ECO,
    HVACMode,
)
from homeassistant.const import STATE_OFF, STATE_ON

from .helpers import (
    PRESET_FROST_PROTECTION,
    WINDOW_CLOSE_DELAY_S,
    WINDOW_DELAY_S,
    WINDOW_SENSOR,
    async_advance,
    async_open_window,
    async_set_state,
    async_setup_proxy,
    trv_rejects,
)

BOOST_DURATION_MIN = 10


async def test_turning_off_leaves_an_open_window_in_charge(hass, freezer):
    proxy = await async_setup_proxy(hass)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)
    await async_open_window(hass, freezer)

    await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.OFF)

    assert proxy.state.state == HVACMode.OFF
    assert proxy.preset == PRESET_FROST_PROTECTION
    assert proxy.target == 5.0
    assert proxy.state.attributes["window_open_active"] is True
    assert proxy.state.attributes["user_preset"] == PRESET_ECO


async def test_turning_back_on_with_the_window_still_open_goes_straight_to_frost_protection(
    hass, freezer
):
    """No new open delay: the room does not heat into the open window."""
    proxy = await async_setup_proxy(hass)
    await async_open_window(hass, freezer)
    await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.OFF)

    await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.HEAT)

    assert proxy.preset == PRESET_FROST_PROTECTION
    assert proxy.target == 7.0


async def test_window_opened_while_off_holds_after_the_open_delay(hass, freezer):
    proxy = await async_setup_proxy(hass)
    await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.OFF)

    await async_set_state(hass, WINDOW_SENSOR, STATE_ON)
    await async_advance(hass, freezer, WINDOW_DELAY_S + 1)

    assert proxy.preset == PRESET_FROST_PROTECTION
    assert proxy.state.attributes["window_open_active"] is True


async def test_window_closing_while_off_ends_its_layer_after_the_close_delay(hass, freezer):
    proxy = await async_setup_proxy(hass)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)
    await async_open_window(hass, freezer)
    await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.OFF)

    await async_set_state(hass, WINDOW_SENSOR, STATE_OFF)
    await async_advance(hass, freezer, WINDOW_CLOSE_DELAY_S + 1)

    assert proxy.state.state == HVACMode.OFF
    assert proxy.preset == PRESET_ECO
    assert proxy.state.attributes["window_open_active"] is False


async def test_window_keeps_frost_protection_when_the_trv_does_not_switch_off(hass, freezer):
    """A failed attempt changes nothing at all."""
    proxy = await async_setup_proxy(hass)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)
    await async_open_window(hass, freezer)

    with trv_rejects("set_hvac_mode"):
        await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.OFF)

    assert proxy.state.state == HVACMode.HEAT
    assert proxy.state.attributes["regulation_reason"] == "hvac_off_failed"
    assert proxy.preset == PRESET_FROST_PROTECTION
    assert proxy.state.attributes["window_open_active"] is True
    assert proxy.state.attributes["user_preset"] == PRESET_ECO


async def test_boost_pauses_while_the_heating_is_off(hass, freezer):
    proxy = await async_setup_proxy(hass, options={"boost_duration": BOOST_DURATION_MIN})
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_BOOST)
    await async_advance(hass, freezer, 4 * 60)

    await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.OFF)
    await async_advance(hass, freezer, 2 * BOOST_DURATION_MIN * 60)
    assert proxy.preset == PRESET_BOOST

    await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.HEAT)
    await async_advance(hass, freezer, 5 * 60)
    assert proxy.preset == PRESET_BOOST

    await async_advance(hass, freezer, 60 + 1)
    assert proxy.preset == PRESET_COMFORT


async def test_failed_turn_off_does_not_pause_the_boost(hass, freezer):
    proxy = await async_setup_proxy(hass, options={"boost_duration": BOOST_DURATION_MIN})
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_BOOST)

    with trv_rejects("set_hvac_mode"):
        await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.OFF)
    await async_advance(hass, freezer, BOOST_DURATION_MIN * 60 + 1)

    assert proxy.preset == PRESET_COMFORT
