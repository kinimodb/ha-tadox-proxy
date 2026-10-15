"""Exercise config updates and HVAC commands without a Home Assistant bootstrap."""

from __future__ import annotations

import ast
import asyncio
import logging
import math
import unittest
from datetime import timedelta
from enum import Enum
from functools import partial
from pathlib import Path
from types import SimpleNamespace

_COMPONENT = Path(__file__).parent.parent / "custom_components" / "tadox_proxy"


def _load_functions(path: Path, *names: str, globals_: dict) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    functions = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    assert {node.name for node in functions} == set(names)
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
              *functions],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), "exec"), globals_)
    return globals_


class HVACMode(Enum):
    HEAT = "heat"
    OFF = "off"


class HomeAssistantError(Exception):
    pass


class FakeDataUpdateCoordinator:
    def __init__(self, hass, logger, *, name, update_method, update_interval):
        self.update_method = update_method

    async def async_config_entry_first_refresh(self):
        self.data = await self.update_method()


class FakeAsyncLock:
    def __init__(self):
        self.lock = None

    async def __aenter__(self):
        if self.lock is None:
            self.lock = asyncio.Lock()
        await self.lock.acquire()

    async def __aexit__(self, exc_type, exc_value, traceback):
        self.lock.release()


_OPTIONS = _load_functions(
    _COMPONENT / "__init__.py",
    "_listener_sources",
    "_async_options_updated",
    "async_setup_entry",
    globals_={
        "CONF_SOURCE_ENTITY_ID": "source_entity_id",
        "CONF_EXTERNAL_TEMPERATURE_ENTITY_ID": "external_temperature_entity_id",
        "CONF_WINDOW_SENSOR_ID": "window_sensor_id",
        "CONF_PRESENCE_SENSOR_ID": "presence_sensor_id",
        "DataUpdateCoordinator": FakeDataUpdateCoordinator,
        "DOMAIN": "tadox_proxy",
        "PLATFORMS": [],
        "_LOGGER": logging.getLogger(__name__),
        "math": math,
        "partial": partial,
        "timedelta": timedelta,
    },
)
_CLIMATE = _load_functions(
    _COMPONENT / "climate.py",
    "async_set_hvac_mode",
    "_async_config_entry_updated",
    globals_={
        "HVACMode": HVACMode,
        "HomeAssistantError": HomeAssistantError,
        "PRESET_COMFORT": "comfort",
        "PRESET_BOOST": "boost",
        "PRESET_FROST_PROTECTION": "frost_protection",
        "CONF_WINDOW_SENSOR_ID": "window_sensor_id",
        "CONF_WINDOW_DELAY_S": "window_delay_s",
        "CONF_SENSOR_GRACE_S": "sensor_grace_s",
        "CONF_OVERLAY_REFRESH_S": "overlay_refresh_s",
        "DEFAULT_SENSOR_GRACE_S": 300,
        "_LOGGER": logging.getLogger(__name__),
    },
)


class TestLiveOptions(unittest.TestCase):
    def setUp(self):
        self.entry = SimpleNamespace(
            entry_id="test-entry",
            data={"source_entity_id": "climate.trv", "external_temperature_entity_id": "sensor.room"},
            options={"window_sensor_id": "binary_sensor.window"},
        )
        self.listener_sources = _OPTIONS["_listener_sources"](self.entry)
        self.reloads = []

        async def reload_entry(entry_id):
            self.reloads.append(entry_id)

        self.hass = SimpleNamespace(config_entries=SimpleNamespace(async_reload=reload_entry))

    def update(self, **options):
        self.entry.options = {**self.entry.options, **options}
        asyncio.run(
            _OPTIONS["_async_options_updated"](
                self.hass, self.entry, listener_sources=self.listener_sources
            )
        )

    def test_preset_and_follow_changes_do_not_reload(self):
        self.update(boost_target=25.0)
        self.update(comfort_target=21.0)
        self.update(follow_tado_input=True)
        self.update(external_temperature_entity_id="sensor.room")
        assert self.reloads == []

    def test_setup_listener_keeps_running_entity_for_preset_update(self):
        callbacks = []
        self.entry.title = "Room"

        def add_update_listener(callback):
            callbacks.append(callback)
            return lambda: None

        def async_on_unload(cancel):
            return None

        self.entry.add_update_listener = add_update_listener
        self.entry.async_on_unload = async_on_unload
        self.hass.data = {}
        self.hass.states = SimpleNamespace(get=lambda entity_id: None)

        async def forward_entry_setups(entry, platforms):
            return None

        self.hass.config_entries.async_forward_entry_setups = forward_entry_setups
        asyncio.run(_OPTIONS["async_setup_entry"](self.hass, self.entry))
        coordinator = self.hass.data["tadox_proxy"][self.entry.entry_id]

        self.entry.options["boost_target"] = 25.0
        asyncio.run(callbacks[0](self.hass, self.entry))
        assert self.reloads == []
        assert self.hass.data["tadox_proxy"][self.entry.entry_id] is coordinator

        self.entry.options["presence_sensor_id"] = "binary_sensor.presence"
        asyncio.run(callbacks[0](self.hass, self.entry))
        assert self.reloads == ["test-entry"]

    def test_new_sensor_entity_reloads(self):
        self.update(window_sensor_id="binary_sensor.other_window")
        assert self.reloads == ["test-entry"]

    def test_external_sensor_change_reloads(self):
        self.update(external_temperature_entity_id="sensor.other_room")
        assert self.reloads == ["test-entry"]

    def test_live_update_preserves_boost_timer(self):
        def boost_cancel():
            return None

        original_config = SimpleNamespace(min_target_c=5.0, max_target_c=30.0)
        updated_config = SimpleNamespace(min_target_c=5.0, max_target_c=30.0)
        entity = SimpleNamespace(
            _config_entry=self.entry,
            _config=original_config,
            _regulator=SimpleNamespace(cfg=original_config),
            _preset_mode="boost",
            _target_temp=25.0,
            _boost_cancel=boost_cancel,
            _boost_end_ts=12345.0,
            _build_config=lambda entry: updated_config,
            _build_behaviour=lambda entry: object(),
            _comfort_target=lambda: 20.0,
            async_write_ha_state=lambda: None,
        )
        self.entry.options["boost_target"] = 26.0
        asyncio.run(_CLIMATE["_async_config_entry_updated"](entity, self.hass, self.entry))
        assert entity._config is updated_config
        assert entity._regulator.cfg is updated_config
        assert entity._preset_mode == "boost"
        assert entity._target_temp == 25.0
        assert entity._boost_cancel is boost_cancel
        assert entity._boost_end_ts == 12345.0


class TestHvacCommandFailures(unittest.TestCase):
    def make_entity(self, mode, *, window_active=False, window_state="off"):
        actions = []
        saved = SimpleNamespace(preset="eco", temp=18.0)

        class WindowController:
            is_active = window_active

            def get_saved(self):
                return saved

            def cancel_all(self):
                actions.append("cancel_window")
                self.is_active = False

            def handle_window_opened(self, hass, delay, callback):
                actions.append("schedule_window")

        entity = SimpleNamespace(
            _attr_hvac_modes=[HVACMode.HEAT, HVACMode.OFF],
            _hvac_mode=mode,
            _preset_mode="frost_protection" if window_active else "comfort",
            _target_temp=5.0 if window_active else 20.0,
            _window_ctrl=WindowController(),
            _last_reason="before_command",
            _last_regulation_ts=123.0,
            _regulation_lock=FakeAsyncLock(),
            _config_entry=SimpleNamespace(
                options={"window_sensor_id": "binary_sensor.window", "window_delay_s": 30}
            ),
            hass=SimpleNamespace(
                states=SimpleNamespace(get=lambda entity_id: SimpleNamespace(state=window_state))
            ),
            _comfort_target=lambda: 20.0,
            _async_window_action=lambda now: None,
            async_write_ha_state=lambda: actions.append("write_state"),
        )

        async def regulate(*, trigger):
            actions.append("regulate")

        entity._async_regulation_cycle = regulate
        return entity, actions

    def test_failed_off_keeps_window_protection_and_saved_preset(self):
        for error in (TimeoutError, HomeAssistantError):
            with self.subTest(error=error):
                entity, actions = self.make_entity(HVACMode.HEAT, window_active=True)

                async def fail(mode):
                    actions.append("send_off")
                    raise error()

                entity._async_send_hvac_mode_to_tado = fail
                with self.assertRaises(error):
                    asyncio.run(_CLIMATE["async_set_hvac_mode"](entity, HVACMode.OFF))

                assert entity._hvac_mode == HVACMode.HEAT
                assert entity._preset_mode == "frost_protection"
                assert entity._target_temp == 5.0
                assert entity._window_ctrl.is_active
                assert entity._window_ctrl.get_saved().preset == "eco"
                assert entity._last_reason == "hvac_off_failed"
                assert actions == ["send_off", "write_state"]

    def test_failed_heat_keeps_off_and_window_state(self):
        entity, actions = self.make_entity(HVACMode.OFF, window_active=True)

        async def fail(mode):
            actions.append("send_heat")
            raise HomeAssistantError()

        entity._async_send_hvac_mode_to_tado = fail
        with self.assertRaises(HomeAssistantError):
            asyncio.run(_CLIMATE["async_set_hvac_mode"](entity, HVACMode.HEAT))

        assert entity._hvac_mode == HVACMode.OFF
        assert entity._window_ctrl.is_active
        assert entity._window_ctrl.get_saved().preset == "eco"
        assert entity._last_regulation_ts == 123.0
        assert entity._last_reason == "hvac_heat_failed"
        assert actions == ["send_heat", "write_state"]

    def test_successful_off_restores_preset_after_command(self):
        entity, actions = self.make_entity(HVACMode.HEAT, window_active=True)

        async def send(mode):
            assert mode == HVACMode.OFF
            assert entity._window_ctrl.is_active
            actions.append("send_off")

        entity._async_send_hvac_mode_to_tado = send
        asyncio.run(_CLIMATE["async_set_hvac_mode"](entity, HVACMode.OFF))

        assert entity._hvac_mode == HVACMode.OFF
        assert entity._preset_mode == "eco"
        assert entity._target_temp == 18.0
        assert not entity._window_ctrl.is_active
        assert actions == ["send_off", "cancel_window", "write_state", "write_state"]

    def test_regulation_waits_for_off_command(self):
        entity, actions = self.make_entity(HVACMode.HEAT)

        async def scenario():
            send_started = asyncio.Event()
            finish_send = asyncio.Event()

            async def send(mode):
                actions.append("send_start")
                send_started.set()
                await finish_send.wait()
                actions.append("send_done")

            async def competing_cycle():
                async with entity._regulation_lock:
                    actions.append(("cycle_mode", entity._hvac_mode))

            entity._async_send_hvac_mode_to_tado = send
            off_task = asyncio.create_task(
                _CLIMATE["async_set_hvac_mode"](entity, HVACMode.OFF)
            )
            await send_started.wait()
            cycle_task = asyncio.create_task(competing_cycle())
            await asyncio.sleep(0)
            assert not cycle_task.done()
            finish_send.set()
            await asyncio.gather(off_task, cycle_task)

        asyncio.run(scenario())
        assert actions == [
            "send_start", "send_done", "write_state", "write_state",
            ("cycle_mode", HVACMode.OFF),
        ]

    def test_successful_heat_reschedules_open_window(self):
        entity, actions = self.make_entity(HVACMode.OFF, window_active=False, window_state="on")

        async def send(mode):
            assert mode == HVACMode.HEAT
            assert entity._hvac_mode == HVACMode.OFF
            actions.append("send_heat")

        entity._async_send_hvac_mode_to_tado = send
        asyncio.run(_CLIMATE["async_set_hvac_mode"](entity, HVACMode.HEAT))

        assert entity._hvac_mode == HVACMode.HEAT
        assert entity._last_regulation_ts == 0
        assert actions == ["send_heat", "write_state", "schedule_window", "regulate"]


if __name__ == "__main__":
    unittest.main()
