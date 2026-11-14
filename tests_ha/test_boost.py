"""Boost: counts down only while it heats the room, then the user choice comes back.

A boost a person starts is sent to the TRV at once; one without a person
waits for the send limit like every other increase.
"""
from __future__ import annotations

import pytest
from homeassistant.components.climate import PRESET_AWAY, PRESET_BOOST, PRESET_ECO
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import Context

from .helpers import (
    CONTROL_INTERVAL_S,
    PRESENCE_AWAY_DELAY_S,
    PRESENCE_HOME_DELAY_S,
    PRESENCE_SENSOR,
    PRESET_FROST_PROTECTION,
    TRV,
    WINDOW_CLOSE_DELAY_S,
    WINDOW_SENSOR,
    Proxy,
    async_advance,
    async_open_window,
    async_set_state,
    async_setup_proxy,
)

BOOST_DURATION_MIN = 10
# RegulationConfig.min_command_interval_s – the send limit.
MIN_COMMAND_INTERVAL_S = 180
# The TRV reads 22 °C in the 20 °C room of the test setup.
TRV_INTERNAL_C = 22.0


def _remaining_min(proxy: Proxy) -> int:
    """The boost remaining sensor."""
    state = proxy.hass.states.get(proxy.entity_id("sensor", "boost_remaining"))
    assert state is not None
    return int(float(state.state))


def _setpoints(proxy: Proxy) -> list[float]:
    """Every setpoint the proxy sent to the TRV so far."""
    return [c["temperature"] for c in proxy.trv_commands("set_temperature")]


async def _async_setup_with_a_command_just_sent(hass, freezer, **options) -> Proxy:
    """A room on Comfort whose first command just went out: the send limit
    holds back every further increase for the next three minutes."""
    proxy = await async_setup_proxy(hass, options=options, trv_adopts=True)
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)
    assert len(_setpoints(proxy)) == 1
    return proxy


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


async def test_a_boost_started_by_a_person_is_sent_at_once(hass, freezer, hass_admin_user):
    """Tapping a preset and then Boost: the boost must not wait three of its
    minutes for the send limit the command before it used up."""
    proxy = await _async_setup_with_a_command_just_sent(hass, freezer)
    comfort_c = _setpoints(proxy)[0]

    await proxy.call(
        "climate",
        "set_preset_mode",
        proxy.climate,
        context=Context(user_id=hass_admin_user.id),
        preset_mode=PRESET_BOOST,
    )

    sent = _setpoints(proxy)
    assert len(sent) == 2
    assert sent[1] > comfort_c
    assert sent[1] == proxy.state.attributes["target_for_tado_c"]
    assert proxy.state.attributes["regulation_reason"] == "sent(urgent_increase)"


async def test_tapping_boost_again_sends_nothing_more(hass, freezer, hass_admin_user):
    """One command per start: tapping Boost again and again sends nothing."""
    proxy = await _async_setup_with_a_command_just_sent(hass, freezer)
    person = Context(user_id=hass_admin_user.id)
    await proxy.call(
        "climate", "set_preset_mode", proxy.climate, context=person, preset_mode=PRESET_BOOST
    )
    sent = _setpoints(proxy)

    for _ in range(3):
        await proxy.call(
            "climate", "set_preset_mode", proxy.climate, context=person, preset_mode=PRESET_BOOST
        )

    assert _setpoints(proxy) == sent


async def test_boost_and_another_preset_in_turns_send_one_boost_at_once(
    hass, freezer, hass_admin_user
):
    """Boost, Eco, Boost, Eco, Boost, seconds apart.  The way down is an
    urgent decrease, so without a bound every tap would be a command.  One
    boost per send interval goes out at once; the next one waits for it."""
    proxy = await _async_setup_with_a_command_just_sent(hass, freezer)
    person = Context(user_id=hass_admin_user.id)

    for preset in (PRESET_BOOST, PRESET_ECO, PRESET_BOOST, PRESET_ECO, PRESET_BOOST):
        await async_advance(hass, freezer, 5)
        await proxy.call(
            "climate", "set_preset_mode", proxy.climate, context=person, preset_mode=preset
        )

    # Comfort from the setup, the first boost, the way down to Eco.
    sent = _setpoints(proxy)
    assert len(sent) == 3
    assert sent[1] > sent[0] > sent[2]
    assert proxy.preset == PRESET_BOOST
    assert proxy.state.attributes["regulation_reason"].startswith("rate_limited(")

    await async_advance(hass, freezer, MIN_COMMAND_INTERVAL_S)

    assert len(_setpoints(proxy)) == 4
    assert _setpoints(proxy)[3] == sent[1]


@pytest.mark.parametrize(
    "context",
    [
        pytest.param(Context(parent_id="the-trigger-event"), id="triggered_automation"),
        pytest.param(Context(), id="time_triggered_automation"),
        pytest.param(None, id="no_context"),
    ],
)
async def test_a_boost_without_a_person_waits_for_the_send_limit(hass, freezer, context):
    """A schedule or an automation is nobody waiting in a cold room."""
    proxy = await _async_setup_with_a_command_just_sent(hass, freezer)
    sent = _setpoints(proxy)

    await proxy.call(
        "climate", "set_preset_mode", proxy.climate, context=context, preset_mode=PRESET_BOOST
    )

    assert proxy.preset == PRESET_BOOST
    assert _setpoints(proxy) == sent
    assert proxy.state.attributes["regulation_reason"].startswith("rate_limited(")

    await async_advance(hass, freezer, MIN_COMMAND_INTERVAL_S)

    assert len(_setpoints(proxy)) == 2
    assert _setpoints(proxy)[1] > sent[0]


async def test_a_boost_sent_at_once_is_not_taken_for_a_dial_change(
    hass, freezer, hass_admin_user
):
    """Follow Tado on: two commands seconds apart.  The TRV's late report of
    the first one, after the second went out, is our own echo, not a dial."""
    proxy = await _async_setup_with_a_command_just_sent(hass, freezer, follow_tado_input=True)
    comfort_c = _setpoints(proxy)[0]
    await async_advance(hass, freezer, 2)

    await proxy.call(
        "climate",
        "set_preset_mode",
        proxy.climate,
        context=Context(user_id=hass_admin_user.id),
        preset_mode=PRESET_BOOST,
    )
    boost_c = _setpoints(proxy)[-1]
    assert boost_c > comfort_c

    await async_advance(hass, freezer, 3)
    for reported_c in (comfort_c, boost_c):
        await async_set_state(
            hass, TRV, "heat", {"current_temperature": TRV_INTERNAL_C, "temperature": reported_c}
        )

    assert proxy.preset == PRESET_BOOST
    assert _setpoints(proxy)[-1] == boost_c
