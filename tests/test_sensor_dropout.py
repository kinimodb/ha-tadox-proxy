"""Exercise the real regulation cycle when the room sensor disappears."""

from __future__ import annotations

import ast
import asyncio
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace

_REGULATION_PATH = (
    Path(__file__).parent.parent
    / "custom_components"
    / "tadox_proxy"
    / "climate_regulation.py"
)


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
        "_LOGGER": logging.getLogger(__name__),
        "time": SimpleNamespace(time=lambda: clock[0]),
    }
    extracted = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    exec(compile(extracted, str(_REGULATION_PATH), "exec"), namespace)
    return namespace["_async_regulation_cycle_locked"]


class FakeRegulator:
    def __init__(self):
        self.room_temperatures = []

    def compute(self, *, setpoint_c, room_temp_c, tado_internal_c, time_delta_s, state):
        self.room_temperatures.append(room_temp_c)
        return SimpleNamespace(
            new_state=state,
            target_for_tado_c=23.0 + len(self.room_temperatures),
        )


class TestSensorDropout(unittest.TestCase):
    def test_last_value_is_used_for_grace_then_commands_stop(self):
        clock = [1000.0]
        regulator = FakeRegulator()
        sent_targets = []
        coordinator = SimpleNamespace(
            last_update_success=True,
            data={
                "room_temp": 20.0,
                "room_temp_ts": 1000.0,
                "tado_internal_temp": 22.0,
                "tado_setpoint": 20.0,
            },
        )
        entity = SimpleNamespace(
            _hvac_mode="heat",
            coordinator=coordinator,
            _last_valid_room_temp=None,
            _last_valid_room_temp_ts=0.0,
            _sensor_grace_s=300,
            _sensor_degraded=False,
            _last_regulation_ts=0.0,
            _regulator=regulator,
            _reg_state=None,
            _last_result=None,
            _last_sent_setpoint=None,
            _last_command_sent_ts=0.0,
            _overlay_refresh_s=0,
            _config=SimpleNamespace(
                min_command_interval_s=0,
                min_change_threshold_c=0.1,
            ),
            _behaviour=SimpleNamespace(urgent_decrease_threshold_c=1.0),
            _effective_setpoint=lambda: 23.0,
            _write_state_with_binary_sensor=lambda: None,
        )

        async def send_target(target_c):
            sent_targets.append(target_c)
            entity._last_sent_setpoint = target_c

        entity._async_send_to_tado = send_target
        cycle = _regulation_cycle(clock)

        asyncio.run(cycle(entity, "initial"))
        assert sent_targets == [24.0]
        assert entity._sensor_degraded is False

        coordinator.data["room_temp"] = None
        clock[0] = 1100.0
        asyncio.run(cycle(entity, "sensor_missing"))
        assert regulator.room_temperatures == [20.0, 20.0]
        assert sent_targets == [24.0, 25.0]
        assert entity._sensor_degraded is True

        clock[0] = 1301.0
        asyncio.run(cycle(entity, "grace_expired"))
        assert regulator.room_temperatures == [20.0, 20.0]
        assert sent_targets == [24.0, 25.0]
        assert entity._last_sent_setpoint == 25.0
        assert entity._last_reason == "waiting_for_sensors"
        assert entity._sensor_degraded is True


if __name__ == "__main__":
    unittest.main()
