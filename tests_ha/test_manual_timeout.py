"""Manual's timer, the return preset and who can change them."""
from __future__ import annotations

import time

import pytest
from homeassistant.components.climate import PRESET_COMFORT, PRESET_ECO, PRESET_NONE, HVACMode
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import Context, State
from pytest_homeassistant_custom_component.common import mock_restore_cache_with_extra_data

from .helpers import CLIMATE, TRV, async_advance, async_set_state, async_setup_proxy

TIMED = {"manual_mode_action": "timer", "manual_mode_duration_min": 1}


async def test_timed_manual_returns_to_the_preset_picked_before(hass, freezer):
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


async def test_a_person_picking_a_preset_ends_the_timer(hass, freezer, hass_admin_user):
    proxy = await async_setup_proxy(hass, options=TIMED)
    await proxy.call("climate", "set_temperature", proxy.climate, temperature=21.5)
    await proxy.call(
        "climate",
        "set_preset_mode",
        proxy.climate,
        context=Context(user_id=hass_admin_user.id),
        preset_mode=PRESET_ECO,
    )

    assert proxy.preset == PRESET_ECO
    assert "manual_return_preset" not in proxy.state.attributes
    await async_advance(hass, freezer, 61)
    assert proxy.preset == PRESET_ECO


@pytest.mark.parametrize(
    "context",
    [
        pytest.param(Context(parent_id="the-trigger-event"), id="triggered_automation"),
        pytest.param(Context(), id="time_triggered_automation"),
        pytest.param(None, id="no_context"),
    ],
)
async def test_a_command_without_a_person_only_changes_the_return_preset(hass, freezer, context):
    proxy = await async_setup_proxy(hass, options=TIMED)
    await proxy.call("climate", "set_temperature", proxy.climate, temperature=21.5)
    deadline = proxy.state.attributes["manual_return_at_ts"]

    await proxy.call(
        "climate", "set_preset_mode", proxy.climate, context=context, preset_mode=PRESET_ECO
    )

    assert proxy.preset == PRESET_NONE
    assert proxy.target == 21.5
    assert proxy.state.attributes["manual_return_preset"] == PRESET_ECO
    assert proxy.state.attributes["manual_return_at_ts"] == deadline
    await async_advance(hass, freezer, 61)
    assert proxy.preset == PRESET_ECO


async def test_without_a_timer_a_command_without_a_person_sets_the_preset(hass):
    proxy = await async_setup_proxy(hass)
    await proxy.call("climate", "set_temperature", proxy.climate, temperature=21.5)

    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)

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


async def test_changed_return_preset_survives_reload(hass, freezer):
    proxy = await async_setup_proxy(hass, options=TIMED)
    await proxy.call("climate", "set_temperature", proxy.climate, temperature=21.5)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)

    assert await hass.config_entries.async_reload(proxy.entry.entry_id)
    await hass.async_block_till_done()
    assert proxy.preset == PRESET_NONE
    assert proxy.state.attributes["manual_return_preset"] == PRESET_ECO
    await async_advance(hass, freezer, 61)
    assert proxy.preset == PRESET_ECO


async def test_timer_goes_on_after_a_restart_during_an_outage(hass, freezer):
    """HA stored a bare state plus the extra data, which carries the deadline."""
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(CLIMATE, STATE_UNAVAILABLE),
                {
                    "desired_hvac_mode": "heat",
                    "user_preset": PRESET_NONE,
                    "manual_target_c": 21.5,
                    "manual_return_preset": PRESET_COMFORT,
                    "manual_return_at_ts": time.time() + 30,
                },
            )
        ],
    )
    proxy = await async_setup_proxy(hass, options=TIMED, source_state=STATE_UNAVAILABLE)
    assert proxy.state.state == STATE_UNAVAILABLE

    await async_set_state(
        hass, TRV, HVACMode.HEAT, {"current_temperature": 22.0, "temperature": 20.0}
    )

    assert proxy.preset == PRESET_NONE
    assert proxy.target == 21.5
    assert proxy.state.attributes["manual_return_preset"] == PRESET_COMFORT
    await async_advance(hass, freezer, 31)
    assert proxy.preset == PRESET_COMFORT


async def test_default_manual_mode_still_has_no_timer(hass, freezer):
    proxy = await async_setup_proxy(hass)
    await proxy.call("climate", "set_temperature", proxy.climate, temperature=21.5)
    await async_advance(hass, freezer, 16 * 60)

    assert proxy.preset == PRESET_NONE
    assert "manual_return_preset" not in proxy.state.attributes
