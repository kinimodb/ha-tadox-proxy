"""Timed return from Manual to the preset selected before it."""
from __future__ import annotations

import time

from homeassistant.components.climate import (
    PRESET_BOOST,
    PRESET_ECO,
    PRESET_NONE,
    HVACMode,
)
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import State
from pytest_homeassistant_custom_component.common import mock_restore_cache

from .helpers import (
    CLIMATE,
    PRESENCE_HOME_DELAY_S,
    PRESENCE_SENSOR,
    PRESET_FROST_PROTECTION,
    TRV,
    WINDOW_CLOSE_DELAY_S,
    WINDOW_SENSOR,
    async_advance,
    async_open_window,
    async_set_state,
    async_setup_proxy,
)

TIMED = {"manual_mode_action": "timer", "manual_mode_duration_min": 15}


async def test_manual_stays_indefinitely_by_default(hass, freezer):
    proxy = await async_setup_proxy(hass, trv_adopts=True)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)
    await proxy.call("climate", "set_temperature", proxy.climate, temperature=21.5)

    await async_advance(hass, freezer, 16 * 60)

    assert proxy.preset == PRESET_NONE
    assert proxy.target == 21.5
    assert "manual_return_preset" not in proxy.state.attributes


async def test_timed_manual_returns_to_previous_preset(hass, freezer):
    proxy = await async_setup_proxy(hass, options=TIMED, trv_adopts=True)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)
    await proxy.call("climate", "set_temperature", proxy.climate, temperature=21.5)
    assert proxy.state.attributes["manual_return_preset"] == PRESET_ECO

    await async_advance(hass, freezer, 14 * 60 + 59)
    assert proxy.preset == PRESET_NONE
    await async_advance(hass, freezer, 2)

    assert proxy.preset == PRESET_ECO
    assert "manual_return_preset" not in proxy.state.attributes


async def test_new_manual_temperature_restarts_timer_but_keeps_origin(hass, freezer):
    proxy = await async_setup_proxy(hass, options=TIMED, trv_adopts=True)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)
    await proxy.call("climate", "set_temperature", proxy.climate, temperature=21.5)
    await async_advance(hass, freezer, 10 * 60)
    await proxy.call("climate", "set_temperature", proxy.climate, temperature=22.0)

    await async_advance(hass, freezer, 10 * 60)
    assert proxy.preset == PRESET_NONE
    assert proxy.state.attributes["manual_return_preset"] == PRESET_ECO
    await async_advance(hass, freezer, 5 * 60 + 1)
    assert proxy.preset == PRESET_ECO


async def test_selecting_named_preset_cancels_manual_return(hass, freezer):
    proxy = await async_setup_proxy(hass, options=TIMED, trv_adopts=True)
    await proxy.call("climate", "set_temperature", proxy.climate, temperature=21.5)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)

    assert "manual_return_preset" not in proxy.state.attributes
    await async_advance(hass, freezer, 16 * 60)
    assert proxy.preset == PRESET_ECO


async def test_reload_keeps_original_deadline(hass, freezer):
    proxy = await async_setup_proxy(hass, options=TIMED, trv_adopts=True)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)
    await proxy.call("climate", "set_temperature", proxy.climate, temperature=21.5)
    deadline = proxy.state.attributes["manual_return_at_ts"]
    await async_advance(hass, freezer, 5 * 60)

    assert await hass.config_entries.async_reload(proxy.entry.entry_id)
    await hass.async_block_till_done()
    assert proxy.preset == PRESET_NONE
    assert proxy.state.attributes["manual_return_at_ts"] == deadline

    await async_advance(hass, freezer, 10 * 60 + 1)
    assert proxy.preset == PRESET_ECO


async def test_expired_during_downtime_returns_on_startup(hass, freezer):
    mock_restore_cache(hass, [State(CLIMATE, HVACMode.HEAT, {
        "preset_mode": PRESET_NONE,
        "temperature": 21.5,
        "manual_target_c": 21.5,
        "manual_return_preset": PRESET_ECO,
        "manual_return_target_c": 21.5,
        "manual_return_at_ts": time.time() - 60,
    })])
    proxy = await async_setup_proxy(hass, options=TIMED, trv_adopts=True)
    await async_advance(hass, freezer, 1)

    assert proxy.preset == PRESET_ECO


async def test_window_keeps_protection_and_timer_updates_saved_preset(hass, freezer):
    proxy = await async_setup_proxy(hass, options=TIMED, trv_adopts=True)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)
    await proxy.call("climate", "set_temperature", proxy.climate, temperature=21.5)
    await async_open_window(hass, freezer)
    assert proxy.preset == PRESET_FROST_PROTECTION

    await async_advance(hass, freezer, 15 * 60)
    assert proxy.preset == PRESET_FROST_PROTECTION
    assert proxy.state.attributes["window_restore_preset"] == PRESET_ECO

    await async_set_state(hass, WINDOW_SENSOR, STATE_OFF)
    await async_advance(hass, freezer, WINDOW_CLOSE_DELAY_S + 1)
    assert proxy.preset == PRESET_ECO


async def test_manual_requested_while_away_survives_reload(hass, freezer):
    proxy = await async_setup_proxy(hass, options=TIMED, trv_adopts=True)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)
    await async_set_state(hass, PRESENCE_SENSOR, STATE_OFF)
    await async_advance(hass, freezer, 61)
    await proxy.call("climate", "set_temperature", proxy.climate, temperature=21.5)
    assert proxy.state.attributes["manual_return_preset"] == PRESET_ECO

    assert await hass.config_entries.async_reload(proxy.entry.entry_id)
    await hass.async_block_till_done()
    assert proxy.preset != PRESET_NONE
    await async_set_state(hass, PRESENCE_SENSOR, STATE_ON)
    await async_advance(hass, freezer, PRESENCE_HOME_DELAY_S + 1)
    assert proxy.preset == PRESET_NONE
    assert proxy.target == 21.5

    await async_advance(hass, freezer, 15 * 60)
    assert proxy.preset == PRESET_ECO


async def test_presence_return_during_downtime_restores_timed_manual(hass, freezer):
    mock_restore_cache(hass, [State(CLIMATE, HVACMode.HEAT, {
        "preset_mode": "away",
        "temperature": 17.0,
        "presence_away_active": True,
        "manual_return_preset": PRESET_ECO,
        "manual_return_target_c": 21.5,
        "manual_return_at_ts": time.time() + 10 * 60,
    })])
    proxy = await async_setup_proxy(hass, options=TIMED, presence=STATE_ON, trv_adopts=True)

    assert proxy.preset == PRESET_NONE
    assert proxy.target == 21.5
    await async_advance(hass, freezer, 10 * 60 + 1)
    assert proxy.preset == PRESET_ECO


async def test_unavailable_source_keeps_timer_through_reload(hass, freezer):
    proxy = await async_setup_proxy(hass, options=TIMED, trv_adopts=True)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)
    await proxy.call("climate", "set_temperature", proxy.climate, temperature=21.5)
    await async_set_state(hass, TRV, STATE_UNAVAILABLE)
    assert proxy.state.state == STATE_UNAVAILABLE

    assert await hass.config_entries.async_reload(proxy.entry.entry_id)
    await hass.async_block_till_done()
    await async_advance(hass, freezer, 15 * 60 + 1)
    await async_set_state(
        hass, TRV, HVACMode.HEAT,
        {"current_temperature": 22.0, "temperature": 20.0},
    )
    assert proxy.preset == PRESET_ECO


async def test_manual_after_boost_returns_to_preboost_preset(hass, freezer):
    proxy = await async_setup_proxy(hass, options=TIMED, trv_adopts=True)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_BOOST)
    await proxy.call("climate", "set_temperature", proxy.climate, temperature=21.5)

    assert proxy.state.attributes["manual_return_preset"] == PRESET_ECO
    await async_advance(hass, freezer, 15 * 60 + 1)
    assert proxy.preset == PRESET_ECO


async def test_manual_without_named_origin_has_no_timer(hass, freezer):
    mock_restore_cache(hass, [State(CLIMATE, HVACMode.HEAT, {
        "preset_mode": PRESET_NONE, "temperature": 21.0, "manual_target_c": 21.0,
    })])
    proxy = await async_setup_proxy(hass, options=TIMED, trv_adopts=True)
    await proxy.call("climate", "set_temperature", proxy.climate, temperature=21.5)

    assert "manual_return_preset" not in proxy.state.attributes
    await async_advance(hass, freezer, 16 * 60)
    assert proxy.preset == PRESET_NONE
    assert proxy.target == 21.5
