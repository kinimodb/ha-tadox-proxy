"""The needs-heat sensor: the signal for a boiler that runs on demand."""
from __future__ import annotations

from unittest.mock import patch

from homeassistant.components.climate import HVACMode
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import State
from homeassistant.data_entry_flow import FlowResultType

from .helpers import (
    CONTROL_INTERVAL_S,
    ROOM_SENSOR,
    TEMPERATURE,
    async_advance,
    async_set_state,
    async_setup_proxy,
    async_submit_options,
)


def _needs_heat(proxy) -> State:
    state = proxy.hass.states.get(proxy.entity_id("binary_sensor", "needs_heat"))
    assert state is not None
    return state


async def _async_set_target(proxy, target_c: float) -> None:
    """Set the room target; the regulation cycle runs at once.

    The room sensor reads 20.0 °C (async_setup_proxy), so the target sets the
    gap: 20.5 °C makes error_c 0.5.
    """
    await proxy.call("climate", "set_temperature", proxy.climate, temperature=target_c)


async def test_sensor_is_named_after_the_proxy(hass):
    proxy = await async_setup_proxy(hass)

    assert proxy.entity_id("binary_sensor", "needs_heat") == "binary_sensor.living_needs_heat"
    state = _needs_heat(proxy)
    assert state.attributes["friendly_name"] == "Living Needs heat"
    # No device class: "heat" would show a cold room as "Hot".
    assert "device_class" not in state.attributes
    # Nothing regulated yet.
    assert state.state == STATE_OFF
    assert state.attributes["error_c"] is None


async def test_on_above_the_switch_on_gap_and_off_once_the_target_is_reached(hass):
    proxy = await async_setup_proxy(hass, trv_adopts=True)

    # Exactly the switch-on gap (0.3 °C) is not above it.
    await _async_set_target(proxy, 20.3)
    assert _needs_heat(proxy).state == STATE_OFF
    assert _needs_heat(proxy).attributes["error_c"] == 0.3

    await _async_set_target(proxy, 20.5)
    assert _needs_heat(proxy).state == STATE_ON
    assert _needs_heat(proxy).attributes["error_c"] == 0.5

    # Still 0.2 °C short: keeps running.
    await _async_set_target(proxy, 20.2)
    assert _needs_heat(proxy).state == STATE_ON

    await _async_set_target(proxy, 20.0)
    assert _needs_heat(proxy).state == STATE_OFF
    assert _needs_heat(proxy).attributes["error_c"] == 0.0

    # The same small gap does not start it again.
    await _async_set_target(proxy, 20.2)
    assert _needs_heat(proxy).state == STATE_OFF


async def test_off_while_the_heating_is_off(hass):
    proxy = await async_setup_proxy(hass, trv_adopts=True)
    await _async_set_target(proxy, 20.5)
    assert _needs_heat(proxy).state == STATE_ON

    await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.OFF)

    assert _needs_heat(proxy).state == STATE_OFF
    assert _needs_heat(proxy).attributes["error_c"] is None

    await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.HEAT)

    assert _needs_heat(proxy).state == STATE_ON
    assert _needs_heat(proxy).attributes["error_c"] == 0.5


async def test_off_while_the_room_sensor_is_missing(hass, freezer):
    proxy = await async_setup_proxy(hass, trv_adopts=True)
    await _async_set_target(proxy, 20.5)
    assert _needs_heat(proxy).state == STATE_ON

    # The coordinator polls the sensor on its own 60 s timer: two cycles make
    # sure the regulation has seen the change.
    await async_set_state(hass, ROOM_SENSOR, STATE_UNAVAILABLE)
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)

    degraded = hass.states.get(proxy.entity_id("binary_sensor", "sensor_degraded"))
    assert degraded is not None
    assert degraded.state == STATE_ON
    # The proxy still regulates on the last reading, but a stale reading is
    # no reason to keep a boiler running.
    assert _needs_heat(proxy).state == STATE_OFF
    assert _needs_heat(proxy).attributes["error_c"] == 0.5

    await async_set_state(hass, ROOM_SENSOR, "20.0", TEMPERATURE)
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)

    assert _needs_heat(proxy).state == STATE_ON


async def test_new_values_apply_at_once_without_a_reload(hass):
    proxy = await async_setup_proxy(hass, trv_adopts=True)
    # The first save adds every option the dialog shows; let that reload pass.
    result = await async_submit_options(hass, proxy.entry)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await _async_set_target(proxy, 20.2)
    assert _needs_heat(proxy).state == STATE_OFF

    with patch.object(hass.config_entries, "async_reload") as reload:
        await async_submit_options(hass, proxy.entry, needs_heat={"needs_heat_on_c": 0.1})
        assert _needs_heat(proxy).state == STATE_ON

        await async_submit_options(
            hass, proxy.entry, needs_heat={"needs_heat_on_c": 0.5, "needs_heat_off_c": 0.2}
        )
        assert _needs_heat(proxy).state == STATE_OFF

    # A reload would have reset the regulation and ended a running boost.
    reload.assert_not_called()
    assert proxy.entry.options["needs_heat_on_c"] == 0.5
    assert proxy.entry.options["needs_heat_off_c"] == 0.2


async def test_switch_on_value_must_be_above_the_switch_off_value(hass):
    proxy = await async_setup_proxy(hass)

    result = await async_submit_options(
        hass, proxy.entry, needs_heat={"needs_heat_on_c": 0.2, "needs_heat_off_c": 0.2}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "needs_heat_thresholds"}
    assert "needs_heat_on_c" not in proxy.entry.options
