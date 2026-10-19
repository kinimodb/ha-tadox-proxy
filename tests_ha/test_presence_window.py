"""Presence and window automations driven by real binary_sensor state changes."""
from __future__ import annotations

from homeassistant.components.climate import PRESET_AWAY
from homeassistant.const import STATE_OFF, STATE_ON

from .helpers import (
    PRESENCE_AWAY_DELAY_S,
    PRESENCE_SENSOR,
    PRESET_FROST_PROTECTION,
    WINDOW_DELAY_S,
    WINDOW_SENSOR,
    async_advance,
    async_set_state,
    async_setup_proxy,
)


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


async def test_turn_on_keeps_window_frost_protection(hass, freezer):
    proxy = await async_setup_proxy(hass)
    await async_set_state(hass, WINDOW_SENSOR, STATE_ON)
    await async_advance(hass, freezer, WINDOW_DELAY_S + 1)
    assert proxy.preset == PRESET_FROST_PROTECTION

    await proxy.call("climate", "turn_on", proxy.climate)

    assert proxy.preset == PRESET_FROST_PROTECTION
    assert proxy.state.attributes["window_open_active"] is True


async def test_presence_away_under_open_window_updates_restore_preset(hass, freezer):
    """The state shows Away as the preset to restore right away, ready for a restart."""
    proxy = await async_setup_proxy(hass)
    await async_set_state(hass, WINDOW_SENSOR, STATE_ON)
    await async_advance(hass, freezer, WINDOW_DELAY_S + 1)
    await async_set_state(hass, PRESENCE_SENSOR, STATE_OFF)
    await async_advance(hass, freezer, PRESENCE_AWAY_DELAY_S + 1)

    assert proxy.preset == PRESET_FROST_PROTECTION
    assert proxy.state.attributes["presence_away_active"] is True
    assert proxy.state.attributes["window_restore_preset"] == PRESET_AWAY
