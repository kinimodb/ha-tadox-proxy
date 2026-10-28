"""Integration-owned weekly schedule, Manual return and Presence priority."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from homeassistant.components.climate import (
    PRESET_AWAY,
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_NONE,
    HVACMode,
)
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.util import dt as dt_util

from .helpers import (
    PRESENCE_SENSOR,
    async_advance,
    async_set_state,
    async_setup_proxy,
)

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _now(freezer) -> datetime:
    freezer.move_to(datetime(2026, 4, 6, 6, tzinfo=UTC))
    return dt_util.now()


def _options(now: datetime, first: str, next_preset: str | None = None) -> dict:
    blocks = [{"start": "00:00", "preset": first}]
    if next_preset is not None:
        blocks.append({
            "start": (now + timedelta(minutes=1)).strftime("%H:%M"),
            "preset": next_preset,
        })
    return {"weekly_preset_schedule": {WEEKDAYS[now.weekday()]: blocks}}


async def test_schedule_applies_current_and_next_block(hass, freezer):
    now = _now(freezer)
    proxy = await async_setup_proxy(
        hass, options=_options(now, PRESET_ECO, PRESET_COMFORT), trv_adopts=True
    )
    assert proxy.preset == PRESET_ECO
    assert proxy.state.attributes["scheduled_action"] == PRESET_ECO

    await async_advance(hass, freezer, 61)
    assert proxy.preset == PRESET_COMFORT
    assert proxy.state.attributes["scheduled_action"] == PRESET_COMFORT


async def test_weekly_schedule_survives_reload_and_applies_next_block(hass, freezer):
    now = _now(freezer)
    options = _options(now, PRESET_ECO, PRESET_COMFORT)
    proxy = await async_setup_proxy(hass, options=options, trv_adopts=True)
    await async_advance(hass, freezer, 20)

    assert await hass.config_entries.async_reload(proxy.entry.entry_id)
    await hass.async_block_till_done()
    assert proxy.entry.options["weekly_preset_schedule"] == options["weekly_preset_schedule"]
    assert proxy.preset == PRESET_ECO

    await async_advance(hass, freezer, 41)
    assert proxy.preset == PRESET_COMFORT


async def test_manual_until_next_block_and_restart(hass, freezer):
    now = _now(freezer)
    proxy = await async_setup_proxy(
        hass,
        options={**_options(now, PRESET_ECO, PRESET_COMFORT), "manual_mode_action": "schedule"},
        trv_adopts=True,
    )
    await proxy.call("climate", "set_temperature", proxy.climate, temperature=21.5)
    deadline = proxy.state.attributes["manual_return_at_ts"]
    assert proxy.preset == PRESET_NONE

    await async_advance(hass, freezer, 20)
    assert await hass.config_entries.async_reload(proxy.entry.entry_id)
    await hass.async_block_till_done()
    assert proxy.preset == PRESET_NONE
    assert proxy.state.attributes["manual_return_at_ts"] == deadline

    await async_advance(hass, freezer, 41)
    assert proxy.preset == PRESET_COMFORT
    assert "manual_return_at_ts" not in proxy.state.attributes


async def test_timed_manual_returns_to_active_block(hass, freezer):
    now = _now(freezer)
    proxy = await async_setup_proxy(
        hass,
        options={
            **_options(now, PRESET_ECO, PRESET_COMFORT),
            "manual_mode_action": "timer",
            "manual_mode_duration_min": 2,
        },
        trv_adopts=True,
    )
    await proxy.call("climate", "set_temperature", proxy.climate, temperature=21.5)

    await async_advance(hass, freezer, 61)
    assert proxy.preset == PRESET_NONE
    await async_advance(hass, freezer, 60)
    assert proxy.preset == PRESET_COMFORT


async def test_schedule_off_yields_to_presence_away_then_returns(hass, freezer):
    now = _now(freezer)
    proxy = await async_setup_proxy(
        hass,
        options={**_options(now, "off"), "presence_away_delay_s": 1, "presence_home_delay_s": 1},
        trv_adopts=True,
    )
    assert proxy.state.state == HVACMode.OFF

    await async_set_state(hass, PRESENCE_SENSOR, STATE_OFF)
    await async_advance(hass, freezer, 2)
    assert proxy.preset == PRESET_AWAY
    assert proxy.state.state == HVACMode.HEAT

    await async_set_state(hass, PRESENCE_SENSOR, STATE_ON)
    await async_advance(hass, freezer, 2)
    assert proxy.state.state == HVACMode.OFF


async def test_presence_home_uses_block_activated_while_away(hass, freezer):
    now = _now(freezer)
    proxy = await async_setup_proxy(
        hass,
        options={
            **_options(now, PRESET_COMFORT, PRESET_ECO),
            "presence_away_delay_s": 1,
            "presence_home_delay_s": 1,
        },
        trv_adopts=True,
    )
    await async_set_state(hass, PRESENCE_SENSOR, STATE_OFF)
    await async_advance(hass, freezer, 2)
    assert proxy.preset == PRESET_AWAY

    await async_advance(hass, freezer, 59)
    assert proxy.preset == PRESET_AWAY
    await async_set_state(hass, PRESENCE_SENSOR, STATE_ON)
    await async_advance(hass, freezer, 2)
    assert proxy.preset == PRESET_ECO


async def test_schedule_off_and_frost_blocks(hass, freezer):
    now = _now(freezer)
    proxy = await async_setup_proxy(
        hass, options=_options(now, "off", "frost_protection"), trv_adopts=True
    )
    assert proxy.state.state == HVACMode.OFF

    await async_advance(hass, freezer, 61)
    assert proxy.state.state == HVACMode.HEAT
    assert proxy.preset == "frost_protection"
