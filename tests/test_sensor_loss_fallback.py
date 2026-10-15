"""Exercise the real regulation cycle when the room sensor disappears."""

from __future__ import annotations

import ast
import asyncio
import logging
import math
import unittest
from pathlib import Path
from types import SimpleNamespace

_REGULATION_PATH = (
    Path(__file__).parent.parent
    / "custom_components"
    / "tadox_proxy"
    / "climate_regulation.py"
)
_SETUP_PATH = _REGULATION_PATH.with_name("__init__.py")


def _regulation_cycle(clock):
    source = ast.parse(_REGULATION_PATH.read_text(encoding="utf-8"))
    method = next(
        node
        for node in ast.walk(source)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_async_regulation_cycle_locked"
    )
    namespace = {
        "HVACMode": SimpleNamespace(OFF="off"),
        "PRESET_FROST_PROTECTION": "frost_protection",
        "CONF_SENSOR_LOSS_ACTION": "sensor_loss_action",
        "SENSOR_LOSS_FALLBACK": "fallback_sensor",
        "SENSOR_LOSS_HOLD": "hold",
        "RegulationState": lambda: SimpleNamespace(integral_c=0.0),
        "_LOGGER": logging.getLogger(__name__),
        "time": SimpleNamespace(time=lambda: clock[0]),
    }
    extracted = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    exec(compile(extracted, str(_REGULATION_PATH), "exec"), namespace)
    return namespace["_async_regulation_cycle_locked"]


def _coordinator_update(entry, states):
    module = ast.parse(_SETUP_PATH.read_text(encoding="utf-8"))
    setup = next(
        node for node in module.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_setup_entry"
    )
    update = next(
        node for node in setup.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_update_data"
    )
    namespace = {
        "entry": entry,
        "hass": SimpleNamespace(states=SimpleNamespace(get=states.get)),
        "math": math,
        "_LOGGER": logging.getLogger(__name__),
        "CONF_EXTERNAL_TEMPERATURE_ENTITY_ID": "external_temperature_entity_id",
        "CONF_FALLBACK_TEMPERATURE_ENTITY_ID": "fallback_temperature_entity_id",
    }
    extracted = ast.fix_missing_locations(ast.Module(body=[update], type_ignores=[]))
    exec(compile(extracted, str(_SETUP_PATH), "exec"), namespace)
    return asyncio.run(namespace["async_update_data"]())


class FakeRegulator:
    def __init__(self):
        self.room_temperatures = []

    def compute(self, *, setpoint_c, room_temp_c, tado_internal_c, time_delta_s, state):
        self.room_temperatures.append(room_temp_c)
        return SimpleNamespace(
            new_state=state,
            target_for_tado_c=23.0 + len(self.room_temperatures),
        )


class TestFallbackModes(unittest.TestCase):
    def test_coordinator_reads_backup_only_when_available(self):
        entry = SimpleNamespace(
            data={"source_entity_id": None, "external_temperature_entity_id": "sensor.primary"},
            options={"fallback_temperature_entity_id": "sensor.backup"},
        )
        states = {
            "sensor.primary": SimpleNamespace(state="unavailable"),
            "sensor.backup": SimpleNamespace(state="19.5"),
        }
        data = _coordinator_update(entry, states)
        assert data["room_temp"] is None
        assert data["fallback_room_temp"] == 19.5

    def make_entity(self, action="native_trv", *, primary=None, fallback=None, internal=None):
        clock = [1301.0]
        sent_targets = []
        regulator = FakeRegulator()
        coordinator = SimpleNamespace(
            last_update_success=True,
            data={
                "room_temp": primary,
                "room_temp_ts": clock[0] if primary is not None else None,
                "fallback_room_temp": fallback,
                "tado_internal_temp": internal,
                "tado_setpoint": 29.0,
            },
        )
        entity = SimpleNamespace(
            _hvac_mode="heat",
            _preset_mode="comfort",
            _config_entry=SimpleNamespace(data={}, options={"sensor_loss_action": action}),
            coordinator=coordinator,
            _last_valid_room_temp=20.0,
            _last_valid_room_temp_ts=1000.0,
            _sensor_grace_s=300,
            _sensor_degraded=False,
            _active_control_mode="primary",
            _pending_mode_transition=False,
            _safety_native_until_regulated=False,
            _last_regulation_ts=1000.0,
            _regulator=regulator,
            _reg_state=SimpleNamespace(integral_c=1.5),
            _last_result=SimpleNamespace(target_for_tado_c=29.0),
            _last_sent_setpoint=29.0,
            _last_command_sent_ts=1290.0,
            _overlay_refresh_s=0,
            _config=SimpleNamespace(
                min_command_interval_s=180,
                min_change_threshold_c=0.1,
                min_target_c=5.0,
                max_target_c=30.0,
            ),
            _behaviour=SimpleNamespace(urgent_decrease_threshold_c=1.0),
            _effective_setpoint=lambda: 7.0 if entity._preset_mode == "frost_protection" else 26.0,
            _write_state_with_binary_sensor=lambda: None,
        )

        async def send_target(target_c):
            sent_targets.append(target_c)
            entity._last_sent_setpoint = target_c

        entity._async_send_to_tado = send_target
        return clock, entity, regulator, sent_targets

    def test_native_failover_sends_user_target_without_internal_temperature(self):
        clock, entity, regulator, sent_targets = self.make_entity()
        asyncio.run(_regulation_cycle(clock)(entity, "sensor_missing"))

        assert sent_targets == [26.0]
        assert regulator.room_temperatures == []
        assert entity._active_control_mode == "native_trv"
        assert entity._sensor_degraded is True
        assert entity._reg_state.integral_c == 0.0
        assert entity._last_result is None

    def test_backup_regulates_then_missing_backup_switches_to_native(self):
        clock, entity, regulator, sent_targets = self.make_entity(
            "fallback_sensor", fallback=20.0, internal=22.0
        )
        asyncio.run(_regulation_cycle(clock)(entity, "primary_missing"))
        assert entity._active_control_mode == "fallback_sensor"
        assert regulator.room_temperatures == [20.0]
        assert sent_targets == [24.0]
        assert entity._reg_state.integral_c == 0.0

        entity.coordinator.data["fallback_room_temp"] = None
        clock[0] = 1310.0
        asyncio.run(_regulation_cycle(clock)(entity, "backup_missing"))
        assert sent_targets == [24.0, 26.0]
        assert entity._active_control_mode == "native_trv"
        assert entity._last_result is None

    def test_backup_without_internal_temperature_uses_native(self):
        clock, entity, regulator, sent_targets = self.make_entity(
            "fallback_sensor", fallback=20.0
        )
        asyncio.run(_regulation_cycle(clock)(entity, "primary_missing"))

        assert entity._active_control_mode == "native_trv"
        assert regulator.room_temperatures == []
        assert sent_targets == [26.0]

    def test_primary_recovery_resets_pi_and_switches_back(self):
        clock, entity, regulator, sent_targets = self.make_entity()
        asyncio.run(_regulation_cycle(clock)(entity, "primary_missing"))
        entity.coordinator.data.update(room_temp=21.0, room_temp_ts=1310.0, tado_internal_temp=22.0)
        clock[0] = 1310.0
        asyncio.run(_regulation_cycle(clock)(entity, "primary_returned"))

        assert entity._active_control_mode == "primary"
        assert entity._sensor_degraded is False
        assert regulator.room_temperatures == [21.0]
        assert sent_targets == [26.0, 24.0]
        assert entity._reg_state.integral_c == 0.0

    def test_first_start_with_missing_primary_marks_degraded(self):
        clock, entity, regulator, sent_targets = self.make_entity()
        entity._last_valid_room_temp = None
        entity._last_valid_room_temp_ts = 0.0
        asyncio.run(_regulation_cycle(clock)(entity, "startup"))

        assert entity._sensor_degraded is True
        assert entity._active_control_mode == "native_trv"
        assert sent_targets == [26.0]

    def test_hold_still_sends_frost_and_restores_native_target(self):
        clock, entity, regulator, sent_targets = self.make_entity("hold")
        entity._preset_mode = "frost_protection"
        asyncio.run(_regulation_cycle(clock)(entity, "window_open"))
        assert sent_targets == [7.0]
        assert entity._active_control_mode == "frost"

        entity._preset_mode = "comfort"
        clock[0] = 1310.0
        asyncio.run(_regulation_cycle(clock)(entity, "window_closed"))
        assert sent_targets == [7.0, 26.0]
        assert entity._active_control_mode == "native_trv"

    def test_heat_after_off_uses_native_target_when_primary_missing(self):
        clock, entity, regulator, sent_targets = self.make_entity("hold")
        entity._active_control_mode = "hold"
        entity._safety_native_until_regulated = True
        entity._last_sent_setpoint = None
        entity.coordinator.data["tado_setpoint"] = None
        asyncio.run(_regulation_cycle(clock)(entity, "heat_after_off"))

        assert sent_targets == [26.0]
        assert entity._active_control_mode == "native_trv"

    def test_frost_restore_uses_native_when_internal_temperature_missing(self):
        clock, entity, regulator, sent_targets = self.make_entity("hold", primary=20.0)
        entity._active_control_mode = "frost"
        entity._safety_native_until_regulated = True
        asyncio.run(_regulation_cycle(clock)(entity, "window_closed"))

        assert sent_targets == [26.0]
        assert entity._active_control_mode == "native_trv"

    def test_failed_mode_switch_retries(self):
        clock, entity, regulator, sent_targets = self.make_entity()
        entity._last_sent_setpoint = 27.0
        entity._behaviour.urgent_decrease_threshold_c = 1.5
        attempts = []

        async def flaky_send(target_c):
            attempts.append(target_c)
            if len(attempts) == 1:
                raise RuntimeError("TRV unavailable")
            sent_targets.append(target_c)
            entity._last_sent_setpoint = target_c

        entity._async_send_to_tado = flaky_send
        with self.assertRaises(RuntimeError):
            asyncio.run(_regulation_cycle(clock)(entity, "primary_missing"))
        clock[0] = 1360.0
        asyncio.run(_regulation_cycle(clock)(entity, "retry"))
        assert attempts == [26.0, 26.0]
        assert sent_targets == [26.0]


if __name__ == "__main__":
    unittest.main()
