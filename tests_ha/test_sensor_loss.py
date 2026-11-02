"""What the proxy does once the room sensor's grace period has expired."""
from __future__ import annotations

from typing import Any

from homeassistant.components.climate import HVACMode
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.data_entry_flow import FlowResultType

from .helpers import (
    DOMAIN,
    PRESENCE_SENSOR,
    ROOM_SENSOR,
    TRV,
    WINDOW_DELAY_S,
    WINDOW_SENSOR,
    async_advance,
    async_set_state,
    async_setup_proxy,
)

# DEFAULT_CONTROL_INTERVAL_S – the regulation cycle runs on this timer.
CONTROL_INTERVAL_S = 60
# DEFAULT_SENSOR_GRACE_S – how long the last valid room reading is used.
SENSOR_GRACE_S = 300
# PresetConfig defaults
COMFORT_C = 20.0
FROST_C = 7.0
# DEFAULT_WINDOW_CLOSE_DELAY_S as set in BASE_OPTIONS
WINDOW_CLOSE_DELAY_S = 120
# RegulationConfig.min_command_interval_s
MIN_COMMAND_INTERVAL_S = 180

BACKUP_SENSOR = "sensor.backup"
TEMPERATURE = {"device_class": "temperature", "unit_of_measurement": "°C"}

REGULATION_ATTRS = ("error_c", "target_for_tado_c")


async def _async_lose_room_sensor(hass, freezer) -> None:
    """Regulate once, then let the room sensor stay away past its grace period."""
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)
    await async_set_state(hass, ROOM_SENSOR, STATE_UNAVAILABLE)
    for _ in range(SENSOR_GRACE_S // CONTROL_INTERVAL_S + 2):
        await async_advance(hass, freezer, CONTROL_INTERVAL_S)


def _last_sent(proxy) -> float:
    return proxy.trv_commands("set_temperature")[-1]["temperature"]


def _degraded_attrs(proxy) -> dict[str, Any]:
    state = proxy.hass.states.get(proxy.entity_id("binary_sensor", "sensor_degraded"))
    assert state is not None
    assert state.state == STATE_ON
    return dict(state.attributes)


async def test_trv_action_sends_room_target_directly(hass, freezer):
    proxy = await async_setup_proxy(
        hass, options={"sensor_loss_action": "trv"}, trv_adopts=True
    )
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)
    assert proxy.state.attributes["active_control_mode"] == "room_sensor"

    await _async_lose_room_sensor(hass, freezer)

    assert proxy.state.attributes["active_control_mode"] == "trv"
    assert _degraded_attrs(proxy)["active_control_mode"] == "trv"
    # No feedforward or PI: the TRV gets the comfort target as it is.
    assert _last_sent(proxy) == COMFORT_C
    assert set(REGULATION_ATTRS) & set(proxy.state.attributes) == set()
    assert proxy.state.attributes["current_temperature"] is None


async def test_returning_room_sensor_resumes_regulation(hass, freezer):
    proxy = await async_setup_proxy(
        hass, options={"sensor_loss_action": "trv"}, trv_adopts=True
    )
    await _async_lose_room_sensor(hass, freezer)

    await async_set_state(hass, ROOM_SENSOR, "20.0", TEMPERATURE)
    # A rise waits for the rate limit.
    for _ in range(MIN_COMMAND_INTERVAL_S // CONTROL_INTERVAL_S + 1):
        await async_advance(hass, freezer, CONTROL_INTERVAL_S)

    assert proxy.state.attributes["active_control_mode"] == "room_sensor"
    assert set(REGULATION_ATTRS) <= set(proxy.state.attributes)
    # The TRV reads 22 °C in a 20 °C room, so it needs a higher setpoint.
    assert _last_sent(proxy) == proxy.state.attributes["target_for_tado_c"] > COMFORT_C


async def test_backup_sensor_takes_over_regulation(hass, freezer):
    hass.states.async_set(BACKUP_SENSOR, "18.0", TEMPERATURE)
    proxy = await async_setup_proxy(
        hass,
        options={"sensor_loss_action": "backup_sensor", "backup_temperature_entity_id": BACKUP_SENSOR},
        trv_adopts=True,
    )

    await _async_lose_room_sensor(hass, freezer)

    assert proxy.state.attributes["active_control_mode"] == "backup_sensor"
    assert proxy.state.attributes["current_temperature"] == 18.0
    assert proxy.state.attributes["error_c"] == COMFORT_C - 18.0


async def test_missing_backup_sensor_hands_control_to_trv(hass, freezer):
    hass.states.async_set(BACKUP_SENSOR, "18.0", TEMPERATURE)
    proxy = await async_setup_proxy(
        hass,
        options={"sensor_loss_action": "backup_sensor", "backup_temperature_entity_id": BACKUP_SENSOR},
        trv_adopts=True,
    )
    await _async_lose_room_sensor(hass, freezer)

    await async_set_state(hass, BACKUP_SENSOR, STATE_UNAVAILABLE)
    for _ in range(MIN_COMMAND_INTERVAL_S // CONTROL_INTERVAL_S + 1):
        await async_advance(hass, freezer, CONTROL_INTERVAL_S)

    assert proxy.state.attributes["active_control_mode"] == "trv"
    assert _last_sent(proxy) == COMFORT_C


async def test_entry_without_action_holds_last_command(hass, freezer):
    """Entries created before the option existed keep the old behaviour."""
    proxy = await async_setup_proxy(hass, trv_adopts=True)
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)
    sent_before = proxy.trv_commands("set_temperature")

    await async_set_state(hass, ROOM_SENSOR, STATE_UNAVAILABLE)
    for _ in range(SENSOR_GRACE_S // CONTROL_INTERVAL_S + 2):
        await async_advance(hass, freezer, CONTROL_INTERVAL_S)

    assert proxy.state.attributes["active_control_mode"] == "hold"
    assert proxy.state.attributes["regulation_reason"] == "waiting_for_sensors"
    assert proxy.trv_commands("set_temperature") == sent_before


async def test_hold_still_lowers_for_open_window_and_restores_target(hass, freezer):
    proxy = await async_setup_proxy(hass, trv_adopts=True)
    await _async_lose_room_sensor(hass, freezer)
    assert proxy.state.attributes["active_control_mode"] == "hold"

    await async_set_state(hass, WINDOW_SENSOR, STATE_ON)
    await async_advance(hass, freezer, WINDOW_DELAY_S + 1)
    assert proxy.state.attributes["active_control_mode"] == "trv"
    assert _last_sent(proxy) == FROST_C

    # Holding now would leave the TRV on the frost target.
    await async_set_state(hass, WINDOW_SENSOR, STATE_OFF)
    await async_advance(hass, freezer, WINDOW_CLOSE_DELAY_S + 1)
    for _ in range(MIN_COMMAND_INTERVAL_S // CONTROL_INTERVAL_S + 1):
        await async_advance(hass, freezer, CONTROL_INTERVAL_S)

    assert proxy.state.attributes["active_control_mode"] == "trv"
    assert _last_sent(proxy) == COMFORT_C


async def test_startup_frost_sends_without_a_room_reading(hass, freezer):
    proxy = await async_setup_proxy(
        hass, window=STATE_ON, room_state=STATE_UNAVAILABLE, trv_adopts=True
    )

    await async_advance(hass, freezer, WINDOW_DELAY_S + 1)

    assert proxy.preset == "frost_protection"
    assert proxy.state.attributes["active_control_mode"] == "trv"
    assert _last_sent(proxy) == FROST_C

    await async_set_state(hass, ROOM_SENSOR, "20.0", TEMPERATURE)
    for _ in range(2):
        await async_advance(hass, freezer, CONTROL_INTERVAL_S)

    assert proxy.state.attributes["active_control_mode"] == "room_sensor"


async def test_startup_frost_window_close_without_room_reading_restores_target(hass, freezer):
    proxy = await async_setup_proxy(
        hass, window=STATE_ON, room_state=STATE_UNAVAILABLE, trv_adopts=True
    )
    await async_advance(hass, freezer, WINDOW_DELAY_S + 1)
    assert _last_sent(proxy) == FROST_C

    await async_set_state(hass, WINDOW_SENSOR, STATE_OFF)
    await async_advance(hass, freezer, WINDOW_CLOSE_DELAY_S + 1)
    for _ in range(MIN_COMMAND_INTERVAL_S // CONTROL_INTERVAL_S + 1):
        await async_advance(hass, freezer, CONTROL_INTERVAL_S)

    assert proxy.state.attributes["active_control_mode"] == "trv"
    assert _last_sent(proxy) == COMFORT_C


async def test_hold_after_turning_back_on_sends_room_target(hass, freezer):
    """After OFF the proxy no longer knows what the TRV holds."""
    proxy = await async_setup_proxy(hass, trv_adopts=True)
    await _async_lose_room_sensor(hass, freezer)
    await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.OFF)
    # The TRV comes back with a setpoint of its own; let the coordinator poll it.
    await async_set_state(hass, TRV, "heat", {"current_temperature": 22.0, "temperature": 25.0})
    await async_advance(hass, freezer, CONTROL_INTERVAL_S)

    await proxy.call("climate", "set_hvac_mode", proxy.climate, hvac_mode=HVACMode.HEAT)

    assert proxy.state.attributes["active_control_mode"] == "trv"
    assert _last_sent(proxy) == COMFORT_C


async def test_new_entry_lets_trv_regulate(hass):
    hass.states.async_set(TRV, "heat", {"current_temperature": 22.0, "temperature": 20.0})
    hass.states.async_set(ROOM_SENSOR, "20.0", TEMPERATURE)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"source_entity_id": TRV, "external_temperature_entity_id": ROOM_SENSOR, "name": "Living"},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["sensor_loss_action"] == "trv"


async def _async_submit_sensor_loss_options(hass, entry, **top_level: Any):
    """Save the options dialog with the given top-level fields."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    user_input: dict[str, Any] = {
        **top_level,
        "window_sensor": {"window_sensor_id": WINDOW_SENSOR},
        "presence_sensor": {"presence_sensor_id": PRESENCE_SENSOR},
        "manual_mode": {},
        "pi_controller": {},
        "gain_scheduling": {},
        "trv_communication": {},
        "behaviour": {},
    }
    return await hass.config_entries.options.async_configure(result["flow_id"], user_input)


async def test_backup_action_requires_backup_sensor(hass):
    proxy = await async_setup_proxy(hass)

    result = await _async_submit_sensor_loss_options(
        hass, proxy.entry, sensor_loss_action="backup_sensor"
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"backup_temperature_entity_id": "backup_sensor_required"}
    assert "sensor_loss_action" not in proxy.entry.options


async def test_backup_sensor_must_differ_from_room_sensor(hass):
    proxy = await async_setup_proxy(hass)

    result = await _async_submit_sensor_loss_options(
        hass,
        proxy.entry,
        sensor_loss_action="backup_sensor",
        backup_temperature_entity_id=ROOM_SENSOR,
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"backup_temperature_entity_id": "backup_sensor_same"}


async def test_backup_action_is_saved(hass):
    hass.states.async_set(BACKUP_SENSOR, "18.0", TEMPERATURE)
    proxy = await async_setup_proxy(hass)

    result = await _async_submit_sensor_loss_options(
        hass,
        proxy.entry,
        sensor_loss_action="backup_sensor",
        backup_temperature_entity_id=BACKUP_SENSOR,
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert proxy.entry.options["sensor_loss_action"] == "backup_sensor"
    assert proxy.entry.options["backup_temperature_entity_id"] == BACKUP_SENSOR
