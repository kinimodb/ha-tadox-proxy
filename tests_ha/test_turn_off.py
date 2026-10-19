"""Turning the proxy off while a window holds (or is about to hold) frost protection."""
from __future__ import annotations

from homeassistant.components.climate import (
    PRESET_AWAY,
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


async def test_turning_off_ends_frost_protection_with_the_saved_preset(hass, freezer):
    proxy = await async_setup_proxy(hass)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)
    await async_open_window(hass, freezer)

    await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.OFF)

    assert proxy.state.state == HVACMode.OFF
    assert proxy.preset == PRESET_ECO
    assert proxy.state.attributes["window_open_active"] is False


async def test_turning_off_brings_back_comfort_for_away_when_someone_is_home(hass, freezer):
    """The same rule as when the window closes."""
    proxy = await async_setup_proxy(hass)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_AWAY)
    await async_open_window(hass, freezer)

    await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.OFF)

    assert proxy.preset == PRESET_COMFORT


async def test_turning_off_brings_back_comfort_for_a_boost(hass, freezer):
    """A boost would run out while the heating is off."""
    proxy = await async_setup_proxy(hass)
    await async_open_window(hass, freezer)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_BOOST)
    assert proxy.state.attributes["window_restore_preset"] == PRESET_BOOST

    await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.OFF)

    assert proxy.preset == PRESET_COMFORT


async def test_window_keeps_frost_protection_when_the_trv_does_not_switch_off(hass, freezer):
    """No heating into the open window: it stays in charge until the TRV is off."""
    proxy = await async_setup_proxy(hass)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)
    await async_open_window(hass, freezer)

    with trv_rejects("set_hvac_mode"):
        await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.OFF)

    assert proxy.state.state == HVACMode.HEAT
    assert proxy.state.attributes["regulation_reason"] == "hvac_off_failed"
    assert proxy.preset == PRESET_FROST_PROTECTION
    assert proxy.state.attributes["window_open_active"] is True
    assert proxy.state.attributes["window_restore_preset"] == PRESET_ECO


async def test_turning_off_during_the_open_delay_ends_the_window_detection(hass, freezer):
    """Turning back on checks the window again."""
    proxy = await async_setup_proxy(hass)
    await async_set_state(hass, WINDOW_SENSOR, STATE_ON)

    await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.OFF)
    await async_advance(hass, freezer, WINDOW_DELAY_S + 1)

    assert proxy.preset == PRESET_COMFORT
    assert proxy.state.attributes["window_open_active"] is False

    await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.HEAT)
    await async_advance(hass, freezer, WINDOW_DELAY_S + 1)

    assert proxy.preset == PRESET_FROST_PROTECTION


async def test_window_closing_while_off_brings_back_comfort_for_a_boost(hass, freezer):
    """No boost timer starts while the heating is off."""
    proxy = await async_setup_proxy(hass)
    await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.OFF)
    await async_open_window(hass, freezer)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_BOOST)
    assert proxy.state.attributes["window_restore_preset"] == PRESET_BOOST

    await async_set_state(hass, WINDOW_SENSOR, STATE_OFF)
    await async_advance(hass, freezer, WINDOW_CLOSE_DELAY_S + 1)

    assert proxy.state.state == HVACMode.OFF
    assert proxy.preset == PRESET_COMFORT
