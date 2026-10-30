"""Regulation diagnostics on the proxy climate entity."""
from __future__ import annotations

from homeassistant.components.climate import HVACMode
from homeassistant.const import STATE_UNAVAILABLE

from .helpers import (
    ROOM_SENSOR,
    async_advance,
    async_set_state,
    async_setup_proxy,
    on_trv_command,
    trv_rejects,
)

# DEFAULT_CONTROL_INTERVAL_S – the regulation cycle runs on this timer.
CONTROL_INTERVAL_S = 60
# DEFAULT_SENSOR_GRACE_S – how long the last valid room reading is used.
SENSOR_GRACE_S = 300

TEMPERATURE = {"device_class": "temperature", "unit_of_measurement": "°C"}

REGULATION_ATTRS = (
    "error_c",
    "feedforward_offset_c",
    "p_correction_c",
    "i_correction_c",
    "target_for_tado_c",
    "is_saturated",
)


def _regulation_attrs(proxy) -> set[str]:
    return set(REGULATION_ATTRS) & set(proxy.state.attributes)


async def test_regulation_values_disappear_when_turned_off(hass, freezer):
    proxy = await async_setup_proxy(hass)
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)
    assert _regulation_attrs(proxy) == set(REGULATION_ATTRS)

    await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.OFF)
    assert _regulation_attrs(proxy) == set()

    # Later cycles while OFF must not bring the old values back.
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)
    assert _regulation_attrs(proxy) == set()


async def test_regulation_values_are_gone_while_the_trv_is_switched_off(hass, freezer):
    """The state that shows OFF has none, also while the TRV command is on its way."""
    proxy = await async_setup_proxy(hass)
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)
    assert _regulation_attrs(proxy) == set(REGULATION_ATTRS)
    shown_during_trv_command: list[set[str]] = []
    on_trv_command(
        hass, "set_hvac_mode", lambda: shown_during_trv_command.append(_regulation_attrs(proxy))
    )

    await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.OFF)

    assert shown_during_trv_command == [set()]


async def test_regulation_values_stay_when_the_trv_does_not_switch_off(hass, freezer):
    proxy = await async_setup_proxy(hass)
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)

    with trv_rejects("set_hvac_mode"):
        await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.OFF)

    assert proxy.state.state == HVACMode.HEAT
    assert _regulation_attrs(proxy) == set(REGULATION_ATTRS)


async def test_regulation_values_return_after_turning_back_on(hass, freezer):
    proxy = await async_setup_proxy(hass)
    await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.OFF)

    await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.HEAT)
    assert _regulation_attrs(proxy) == set(REGULATION_ATTRS)


async def test_regulation_values_disappear_after_sensor_grace_period(hass, freezer):
    proxy = await async_setup_proxy(hass, trv_adopts=True)
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)
    assert _regulation_attrs(proxy) == set(REGULATION_ATTRS)

    await async_set_state(hass, ROOM_SENSOR, "unavailable")
    for _ in range(SENSOR_GRACE_S // CONTROL_INTERVAL_S + 2):
        await async_advance(hass, freezer, CONTROL_INTERVAL_S)

    assert proxy.state.attributes["regulation_reason"] == "waiting_for_sensors"
    assert _regulation_attrs(proxy) == set()


def _degraded_sensor_attrs(proxy) -> dict:
    state = proxy.hass.states.get(proxy.entity_id("binary_sensor", "sensor_degraded"))
    assert state is not None
    return dict(state.attributes)


async def test_last_valid_reading_shows_only_while_the_sensor_is_missing(hass, freezer):
    """Shown all the time, its growing age made every write a new recorder row."""
    proxy = await async_setup_proxy(hass, trv_adopts=True)
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)
    assert "room_temp_last_valid_c" not in proxy.state.attributes
    assert "room_temp_last_valid_age_s" not in proxy.state.attributes
    assert "last_valid_age_s" not in _degraded_sensor_attrs(proxy)

    # The coordinator polls the sensor on its own 60 s timer: two cycles make
    # sure the regulation has seen the change.
    await async_set_state(hass, ROOM_SENSOR, STATE_UNAVAILABLE)
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)
    assert proxy.state.attributes["room_temp_last_valid_c"] == 20.0
    assert proxy.state.attributes["room_temp_last_valid_age_s"] >= CONTROL_INTERVAL_S
    assert _degraded_sensor_attrs(proxy)["last_valid_reading"] == 20.0
    assert _degraded_sensor_attrs(proxy)["last_valid_age_s"] >= CONTROL_INTERVAL_S

    await async_set_state(hass, ROOM_SENSOR, "20.5", TEMPERATURE)
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)
    assert "room_temp_last_valid_c" not in proxy.state.attributes
    assert "room_temp_last_valid_age_s" not in proxy.state.attributes
    assert "last_valid_age_s" not in _degraded_sensor_attrs(proxy)
