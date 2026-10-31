"""State restore after a Home Assistant restart (RestoreEntity)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    PRESET_AWAY,
    PRESET_BOOST,
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_NONE,
    HVACMode,
)
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import State
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import mock_restore_cache

from .helpers import (
    CLIMATE,
    CONTROL_INTERVAL_S,
    PRESENCE_HOME_DELAY_S,
    PRESENCE_SENSOR,
    PRESET_FROST_PROTECTION,
    WINDOW_DELAY_S,
    WINDOW_SENSOR,
    async_advance,
    async_set_state,
    async_setup_proxy,
    async_submit_options,
)

# PresetConfig.comfort_target_c – used when no comfort_target option is stored.
DEFAULT_COMFORT_C = 20.0
# PresetConfig.frost_protection_target_c
FROST_C = 7.0
# window_close_delay_s as set in BASE_OPTIONS
WINDOW_CLOSE_DELAY_S = 120


def _window_frost_state(restore_preset: str, restore_target_c: float, **extra: Any) -> State:
    """State written by v1.4.x while an open window held frost protection."""
    return State(
        CLIMATE,
        HVACMode.HEAT,
        {
            "temperature": FROST_C,
            "preset_mode": PRESET_FROST_PROTECTION,
            "window_open_active": True,
            "window_restore_preset": restore_preset,
            "window_restore_target_c": restore_target_c,
            **extra,
        },
    )


async def test_manual_target_survives_restart_while_off(hass):
    """OFF shows the 5 °C effective setpoint; HEAT must bring back the manual 21.5 °C."""
    mock_restore_cache(
        hass,
        [State(CLIMATE, HVACMode.OFF, {"temperature": 5.0, "preset_mode": PRESET_NONE, "manual_target_c": 21.5})],
    )
    proxy = await async_setup_proxy(hass, options={"comfort_target": 20.0})
    assert proxy.climate == CLIMATE
    assert proxy.state.state == HVACMode.OFF

    await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.HEAT)

    assert proxy.preset == PRESET_NONE
    assert proxy.target == 21.5


async def test_comfort_default_after_restart_in_eco(hass):
    """Without a stored comfort_target, Comfort must not inherit the restored eco setpoint."""
    mock_restore_cache(hass, [State(CLIMATE, HVACMode.HEAT, {"temperature": 17.0, "preset_mode": PRESET_ECO})])
    proxy = await async_setup_proxy(hass)
    assert "comfort_target" not in proxy.entry.options
    assert proxy.preset == PRESET_ECO

    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_COMFORT)

    assert proxy.target == DEFAULT_COMFORT_C


async def test_away_with_unavailable_presence_recovers(hass, freezer):
    """A presence sensor that is not ready at boot must still end AWAY once it reports home."""
    mock_restore_cache(
        hass,
        [
            State(
                CLIMATE,
                HVACMode.HEAT,
                {"temperature": 17.0, "preset_mode": PRESET_AWAY, "presence_away_active": True},
            )
        ],
    )
    proxy = await async_setup_proxy(hass, presence=STATE_UNAVAILABLE)
    assert proxy.preset == PRESET_AWAY

    await async_set_state(hass, PRESENCE_SENSOR, STATE_ON)
    await async_advance(hass, freezer, PRESENCE_HOME_DELAY_S + 1)

    assert proxy.preset == PRESET_COMFORT


async def test_user_choice_is_part_of_the_state(hass, freezer):
    """The user choice is written to the state for a restart, whatever holds the room."""
    proxy = await async_setup_proxy(hass)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)
    await async_set_state(hass, WINDOW_SENSOR, STATE_ON)
    await async_advance(hass, freezer, WINDOW_DELAY_S + 1)
    assert proxy.preset == PRESET_FROST_PROTECTION
    assert proxy.state.attributes["user_preset"] == PRESET_ECO

    # A change while the window is open is the new user choice.
    await proxy.call("climate", "set_temperature", proxy.climate, temperature=21.5)
    assert proxy.state.attributes["user_preset"] == PRESET_NONE
    assert proxy.state.attributes["manual_target_c"] == 21.5
    assert "window_restore_preset" not in proxy.state.attributes


async def test_user_choice_comes_back_after_a_restart_in_away(hass, freezer):
    """Not Comfort: the user choice survives whatever held the room."""
    mock_restore_cache(
        hass,
        [
            State(
                CLIMATE,
                HVACMode.HEAT,
                {
                    "temperature": 17.0,
                    "preset_mode": PRESET_AWAY,
                    "user_preset": PRESET_ECO,
                    "presence_away_active": True,
                },
            )
        ],
    )
    proxy = await async_setup_proxy(hass, presence=STATE_OFF)
    assert proxy.preset == PRESET_AWAY

    await async_set_state(hass, PRESENCE_SENSOR, STATE_ON)
    await async_advance(hass, freezer, PRESENCE_HOME_DELAY_S + 1)

    assert proxy.preset == PRESET_ECO


async def test_user_choice_comes_back_after_a_restart_during_a_boost(hass):
    mock_restore_cache(
        hass,
        [
            State(
                CLIMATE,
                HVACMode.HEAT,
                {"temperature": 25.0, "preset_mode": PRESET_BOOST, "user_preset": PRESET_ECO},
            )
        ],
    )
    proxy = await async_setup_proxy(hass)

    assert proxy.preset == PRESET_ECO


async def test_window_saved_preset_comes_back_after_restart(hass):
    """Upgrade from v1.4.x, window closed while Home Assistant was down: Eco comes back."""
    mock_restore_cache(hass, [_window_frost_state(PRESET_ECO, DEFAULT_COMFORT_C)])
    proxy = await async_setup_proxy(hass, window=STATE_OFF)

    assert proxy.preset == PRESET_ECO
    assert proxy.state.attributes["window_open_active"] is False
    assert "window_restore_preset" not in proxy.state.attributes


async def test_window_saved_manual_temperature_comes_back_after_restart(hass):
    mock_restore_cache(hass, [_window_frost_state(PRESET_NONE, 21.5)])
    proxy = await async_setup_proxy(hass, window=STATE_OFF)

    assert proxy.preset == PRESET_NONE
    assert proxy.target == 21.5


async def test_open_window_keeps_frost_protection_across_restart(hass, freezer):
    """Still open after the restart: frost protection goes on without a new open delay."""
    mock_restore_cache(hass, [_window_frost_state(PRESET_ECO, DEFAULT_COMFORT_C)])
    proxy = await async_setup_proxy(hass, window=STATE_ON)

    assert proxy.preset == PRESET_FROST_PROTECTION
    assert proxy.state.attributes["window_open_active"] is True
    assert proxy.state.attributes["user_preset"] == PRESET_ECO

    await async_set_state(hass, WINDOW_SENSOR, STATE_OFF)
    await async_advance(hass, freezer, WINDOW_CLOSE_DELAY_S + 1)

    assert proxy.preset == PRESET_ECO


async def test_away_saved_by_open_window_survives_restart(hass, freezer):
    """Upgrade from v1.4.x, Away under an open window: presence takes over again."""
    mock_restore_cache(
        hass, [_window_frost_state(PRESET_AWAY, DEFAULT_COMFORT_C, presence_away_active=True)]
    )
    proxy = await async_setup_proxy(hass, presence=STATE_OFF, window=STATE_ON)
    assert proxy.preset == PRESET_FROST_PROTECTION
    assert proxy.state.attributes["presence_away_active"] is True

    await async_set_state(hass, WINDOW_SENSOR, STATE_OFF)
    await async_advance(hass, freezer, WINDOW_CLOSE_DELAY_S + 1)
    assert proxy.preset == PRESET_AWAY

    await async_set_state(hass, PRESENCE_SENSOR, STATE_ON)
    await async_advance(hass, freezer, PRESENCE_HOME_DELAY_S + 1)
    assert proxy.preset == PRESET_COMFORT


async def test_away_picked_by_hand_and_kept_by_the_window_comes_back(hass, freezer):
    """Upgrade from v1.4.x: an Away picked by hand stays the user choice."""
    mock_restore_cache(
        hass, [_window_frost_state(PRESET_AWAY, 16.0, presence_away_active=False)]
    )
    proxy = await async_setup_proxy(hass, window=STATE_ON)

    await async_set_state(hass, WINDOW_SENSOR, STATE_OFF)
    await async_advance(hass, freezer, WINDOW_CLOSE_DELAY_S + 1)

    assert proxy.preset == PRESET_AWAY
    assert proxy.state.attributes["user_preset"] == PRESET_AWAY
    assert proxy.state.attributes["presence_away_active"] is False


async def test_off_window_action_switches_the_trv_off_again_after_restart(hass, freezer):
    """Frost protection resumes as "off": the first cycle switches the TRV off, no setpoint."""
    mock_restore_cache(hass, [_window_frost_state(PRESET_ECO, DEFAULT_COMFORT_C)])
    proxy = await async_setup_proxy(hass, options={"window_action": "off"}, window=STATE_ON)

    await async_advance(hass, freezer, CONTROL_INTERVAL_S)

    assert proxy.preset == PRESET_FROST_PROTECTION
    assert proxy.trv_commands("set_hvac_mode")[-1]["hvac_mode"] == HVACMode.OFF
    assert proxy.trv_commands("set_temperature") == []


async def test_window_sensor_not_ready_at_restart_keeps_frost_protection(hass, freezer):
    """A sensor that comes up late is no news, not a closed window."""
    mock_restore_cache(hass, [_window_frost_state(PRESET_ECO, DEFAULT_COMFORT_C)])
    proxy = await async_setup_proxy(hass, window=STATE_UNAVAILABLE)
    assert proxy.preset == PRESET_FROST_PROTECTION

    await async_set_state(hass, WINDOW_SENSOR, STATE_ON)
    await async_advance(hass, freezer, WINDOW_DELAY_S + 1)
    assert proxy.preset == PRESET_FROST_PROTECTION

    await async_set_state(hass, WINDOW_SENSOR, STATE_OFF)
    await async_advance(hass, freezer, WINDOW_CLOSE_DELAY_S + 1)
    assert proxy.preset == PRESET_ECO


async def test_trv_switched_off_by_window_is_switched_on_after_restart(hass, freezer):
    """The window closed while Home Assistant was down: the TRV must heat again."""
    mock_restore_cache(
        hass, [_window_frost_state(PRESET_ECO, DEFAULT_COMFORT_C, window_trv_off=True)]
    )
    proxy = await async_setup_proxy(hass, options={"window_action": "off"}, window=STATE_OFF)
    assert proxy.preset == PRESET_ECO

    await async_advance(hass, freezer, CONTROL_INTERVAL_S)

    assert proxy.trv_commands("set_hvac_mode")[-1]["hvac_mode"] == HVACMode.HEAT
    assert "window_trv_off" not in proxy.state.attributes


async def test_user_choice_survives_an_options_reload_with_an_open_window(hass, freezer):
    """Saving the options reloads the entry while the window is open."""
    proxy = await async_setup_proxy(hass)
    await proxy.call("climate", "set_preset_mode", proxy.climate, preset_mode=PRESET_ECO)
    await async_set_state(hass, WINDOW_SENSOR, STATE_ON)
    await async_advance(hass, freezer, WINDOW_DELAY_S + 1)

    result = await async_submit_options(hass, proxy.entry)
    assert result["type"] is FlowResultType.CREATE_ENTRY

    assert proxy.preset == PRESET_FROST_PROTECTION
    assert proxy.state.attributes["user_preset"] == PRESET_ECO

    await async_set_state(hass, WINDOW_SENSOR, STATE_OFF)
    await async_advance(hass, freezer, WINDOW_CLOSE_DELAY_S + 1)
    assert proxy.preset == PRESET_ECO
