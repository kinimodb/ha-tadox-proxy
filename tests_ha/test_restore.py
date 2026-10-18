"""State restore after a Home Assistant restart (RestoreEntity)."""
from __future__ import annotations

from homeassistant.components.climate import (
    PRESET_AWAY,
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_NONE,
    HVACMode,
)
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import State
from pytest_homeassistant_custom_component.common import mock_restore_cache

from .helpers import (
    CLIMATE,
    PRESENCE_HOME_DELAY_S,
    PRESENCE_SENSOR,
    async_advance,
    async_set_state,
    async_setup_proxy,
)

# PresetConfig.comfort_target_c – used when no comfort_target option is stored.
DEFAULT_COMFORT_C = 20.0


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
    mock_restore_cache(hass, [State(CLIMATE, HVACMode.HEAT, {"temperature": 17.0, "preset_mode": PRESET_AWAY})])
    proxy = await async_setup_proxy(hass, presence=STATE_UNAVAILABLE)
    assert proxy.preset == PRESET_AWAY

    await async_set_state(hass, PRESENCE_SENSOR, STATE_ON)
    await async_advance(hass, freezer, PRESENCE_HOME_DELAY_S + 1)

    assert proxy.preset == PRESET_COMFORT
