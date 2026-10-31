"""Timed return from Manual to the preset selected before it."""
from __future__ import annotations

from homeassistant.components.climate import PRESET_ECO, PRESET_NONE

from .helpers import async_advance, async_setup_proxy

TIMED = {"manual_mode_action": "timer", "manual_mode_duration_min": 1}


async def test_timed_manual_returns_to_previous_preset(hass, freezer):
    proxy = await async_setup_proxy(hass, options=TIMED)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)
    await proxy.call("climate", "set_temperature", proxy.climate, temperature=21.5)

    assert proxy.preset == PRESET_NONE
    assert proxy.state.attributes["manual_return_preset"] == PRESET_ECO
    await async_advance(hass, freezer, 61)

    assert proxy.preset == PRESET_ECO
    assert "manual_return_preset" not in proxy.state.attributes


async def test_new_manual_temperature_restarts_timer_and_keeps_origin(hass, freezer):
    proxy = await async_setup_proxy(hass, options=TIMED)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)
    await proxy.call("climate", "set_temperature", proxy.climate, temperature=21.5)
    await async_advance(hass, freezer, 45)
    await proxy.call("climate", "set_temperature", proxy.climate, temperature=22.0)

    await async_advance(hass, freezer, 45)
    assert proxy.preset == PRESET_NONE
    assert proxy.state.attributes["manual_return_preset"] == PRESET_ECO
    await async_advance(hass, freezer, 16)
    assert proxy.preset == PRESET_ECO


async def test_named_preset_cancels_manual_return(hass, freezer):
    proxy = await async_setup_proxy(hass, options=TIMED)
    await proxy.call("climate", "set_temperature", proxy.climate, temperature=21.5)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)

    assert "manual_return_preset" not in proxy.state.attributes
    await async_advance(hass, freezer, 61)
    assert proxy.preset == PRESET_ECO


async def test_manual_return_deadline_survives_reload(hass, freezer):
    proxy = await async_setup_proxy(hass, options=TIMED)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)
    await proxy.call("climate", "set_temperature", proxy.climate, temperature=21.5)
    deadline = proxy.state.attributes["manual_return_at_ts"]
    await async_advance(hass, freezer, 30)

    assert await hass.config_entries.async_reload(proxy.entry.entry_id)
    await hass.async_block_till_done()
    assert proxy.preset == PRESET_NONE
    assert proxy.state.attributes["manual_return_at_ts"] == deadline
    await async_advance(hass, freezer, 31)
    assert proxy.preset == PRESET_ECO


async def test_default_manual_mode_still_has_no_timer(hass, freezer):
    proxy = await async_setup_proxy(hass)
    await proxy.call("climate", "set_temperature", proxy.climate, temperature=21.5)
    await async_advance(hass, freezer, 16 * 60)

    assert proxy.preset == PRESET_NONE
    assert "manual_return_preset" not in proxy.state.attributes
