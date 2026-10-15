"""State-change event tests for the optional window and presence sensors."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch


def _load_preset_mixin():
    component_dir = Path(__file__).parent.parent / "custom_components" / "tadox_proxy"
    package = types.ModuleType("_test_tadox_proxy_events")
    package.__path__ = [str(component_dir)]

    climate = types.ModuleType("homeassistant.components.climate")
    for preset in ("AWAY", "BOOST", "COMFORT", "ECO", "NONE"):
        setattr(climate, f"PRESET_{preset}", preset.lower())
    climate.HVACMode = object()

    core = types.ModuleType("homeassistant.core")

    def callback(function):
        return function

    core.callback = callback

    const = types.ModuleType("_test_tadox_proxy_events.const")
    for name in (
        "CONF_COMFORT_TARGET",
        "CONF_PRESENCE_AWAY_DELAY_S",
        "CONF_PRESENCE_HOME_DELAY_S",
        "CONF_PRESENCE_SENSOR_ID",
        "CONF_WINDOW_CLOSE_DELAY_S",
        "CONF_WINDOW_DELAY_S",
        "CONF_WINDOW_SENSOR_ID",
    ):
        setattr(const, name, name.lower())
    const.PRESET_FROST_PROTECTION = "frost_protection"
    const.PRESET_LIST = ()
    const.safe_float = float

    parameters = types.ModuleType("_test_tadox_proxy_events.parameters")
    parameters.FROST_PROTECT_C = 5.0

    modules = {
        "_test_tadox_proxy_events": package,
        "_test_tadox_proxy_events.const": const,
        "_test_tadox_proxy_events.parameters": parameters,
        "homeassistant": types.ModuleType("homeassistant"),
        "homeassistant.components": types.ModuleType("homeassistant.components"),
        "homeassistant.components.climate": climate,
        "homeassistant.core": core,
    }
    spec = importlib.util.spec_from_file_location(
        "_test_tadox_proxy_events.climate_presets", component_dir / "climate_presets.py"
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module.PresetMixin


PresetMixin = _load_preset_mixin()


class RecordingWindowController:
    def __init__(self):
        self.calls = []

    def handle_window_opened(self, hass, delay, action):
        self.calls.append(("open", delay))

    def handle_window_closed(self, hass, delay, action):
        self.calls.append(("close", delay))
        return False


class RecordingPresenceController:
    def __init__(self):
        self.calls = []

    def handle_presence_away(self, hass, delay, action):
        self.calls.append(("away", delay))

    def handle_presence_home(self, hass, delay, action):
        self.calls.append(("home", delay))
        return False


class ClimateUnderTest(PresetMixin):
    def __init__(self):
        self.hass = object()
        self._config_entry = types.SimpleNamespace(options={})
        self._window_ctrl = RecordingWindowController()
        self._presence_ctrl = RecordingPresenceController()


def _event(old_state, new_state):
    def state(value):
        return types.SimpleNamespace(state=value) if value is not None else None

    return types.SimpleNamespace(
        data={"old_state": state(old_state), "new_state": state(new_state)}
    )


def test_window_attribute_updates_do_not_restart_open_or_close_delay():
    climate = ClimateUnderTest()

    climate._async_window_changed(_event("off", "on"))
    climate._async_window_changed(_event("on", "on"))
    climate._async_window_changed(_event("on", "off"))
    climate._async_window_changed(_event("off", "off"))

    assert climate._window_ctrl.calls == [("open", 30), ("close", 120)]


def test_presence_attribute_updates_do_not_restart_away_or_home_delay():
    climate = ClimateUnderTest()

    climate._async_presence_changed(_event("on", "off"))
    climate._async_presence_changed(_event("off", "off"))
    climate._async_presence_changed(_event("off", "on"))
    climate._async_presence_changed(_event("on", "on"))

    assert climate._presence_ctrl.calls == [("away", 600), ("home", 30)]


def test_valid_state_after_sensor_recovery_is_processed():
    climate = ClimateUnderTest()

    climate._async_window_changed(_event("unknown", "on"))
    climate._async_presence_changed(_event("unavailable", "off"))
    climate._async_window_changed(_event(None, "off"))
    climate._async_presence_changed(_event(None, "on"))

    assert climate._window_ctrl.calls == [("open", 30), ("close", 120)]
    assert climate._presence_ctrl.calls == [("away", 600), ("home", 30)]


def test_unavailable_state_does_not_change_automation():
    climate = ClimateUnderTest()

    climate._async_window_changed(_event("on", "unavailable"))
    climate._async_presence_changed(_event("off", "unknown"))
    climate._async_window_changed(_event("on", None))
    climate._async_presence_changed(_event("off", None))

    assert climate._window_ctrl.calls == []
    assert climate._presence_ctrl.calls == []
