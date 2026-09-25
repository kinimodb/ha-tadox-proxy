"""Regression tests: presets reset to COMFORT (frost / window / restart).

Symptom: with the user-selected ``frost_protection`` preset, thermostats
jumped back to COMFORT after
  1. an HA restart or config-entry reload (options save, number/switch change),
  2. a window open → close cycle,
  3. a repeated "window open" event while already in window mode
     (on → unavailable → on), which also affected every other preset.

Root cause: ``frost_protection`` was treated as "window-driven" wherever it
appeared, and the window/presence/boost automation state was not persisted.

Two layers of tests:
- Pure helpers in ``climate_controllers.py`` (imported directly, HA-free).
- End-to-end tests against the *real* ``TadoXProxyClimate`` / ``PresetMixin``
  code, loaded with a minimal Home Assistant stub (installed only when Home
  Assistant itself is not importable).
"""
from __future__ import annotations

import asyncio
import enum
import importlib.util
import sys
import time
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent / "custom_components" / "tadox_proxy"


# ===========================================================================
# Part 1 – pure helpers (HA-free)
# ===========================================================================

def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_ctrl = _load("climate_controllers")
SavedState = _ctrl.SavedState
Persisted = _ctrl.PersistedAutomationState
WindowAutomationController = _ctrl.WindowAutomationController
PresenceAutomationController = _ctrl.PresenceAutomationController


class _FakeCallLater:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, hass, delay, callback):
        entry = {"delay": delay, "callback": callback, "cancelled": False}
        self.calls.append(entry)

        def cancel():
            entry["cancelled"] = True

        return cancel

    @property
    def active(self) -> list[dict]:
        return [c for c in self.calls if not c["cancelled"]]


class TestWindowOpenIsIdempotent:
    """Fix A: a second "open" event in window mode must not re-arm the timer."""

    def test_open_event_while_active_schedules_nothing(self):
        cl = _FakeCallLater()
        wc = WindowAutomationController()
        wc.activate("eco", None)
        wc.handle_window_opened(None, 30, lambda _: None, call_later=cl)
        assert cl.calls == []
        assert wc.is_active
        assert wc.get_saved() == SavedState("eco", None)

    def test_reopen_during_close_delay_still_cancels_close_timer(self):
        cl = _FakeCallLater()
        wc = WindowAutomationController()
        wc.activate("frost_protection", 5.0)
        wc.handle_window_closed(None, 120, lambda _: None, call_later=cl)
        assert wc.close_delay_active
        wc.handle_window_opened(None, 30, lambda _: None, call_later=cl)
        assert not wc.close_delay_active
        assert cl.active == []
        assert wc.get_saved().preset == "frost_protection"

    def test_open_when_idle_still_schedules(self):
        cl = _FakeCallLater()
        wc = WindowAutomationController()
        wc.handle_window_opened(None, 30, lambda _: None, call_later=cl)
        assert len(cl.active) == 1

    def test_cancel_timers_keeps_active_and_saved(self):
        cl = _FakeCallLater()
        wc = WindowAutomationController()
        wc.activate("eco", 18.5)
        wc.handle_window_closed(None, 120, lambda _: None, call_later=cl)
        wc.cancel_timers()
        assert cl.active == []
        assert wc.is_active
        assert wc.get_saved() == SavedState("eco", 18.5)

    def test_cancel_all_still_resets(self):
        wc = WindowAutomationController()
        wc.activate("eco", 18.5)
        wc.cancel_all()
        assert not wc.is_active
        assert wc.get_saved() == SavedState()

    def test_presence_get_saved_returns_copy(self):
        pc = PresenceAutomationController()
        pc.activate("frost_protection", 5.0)
        saved = pc.get_saved()
        saved.preset = "x"
        assert pc.get_saved() == SavedState("frost_protection", 5.0)


class TestPersistedAutomationState:
    def test_round_trip(self):
        p = Persisted(
            window_active=True,
            window_saved=SavedState("frost_protection", 5.0),
            presence_active=True,
            presence_saved=SavedState("none", 19.5),
            boost_end_ts=1234.5,
            boost_saved=SavedState("eco", None),
        )
        assert Persisted.from_dict(p.as_dict()) == p

    def test_default_round_trip(self):
        assert Persisted.from_dict(Persisted().as_dict()) == Persisted()

    @pytest.mark.parametrize("data", [None, {}, [], "x", {"window_active": True}])
    def test_unusable_input_returns_none(self, data):
        assert Persisted.from_dict(data) is None

    def test_garbage_values_are_sanitised(self):
        p = Persisted.from_dict({
            "version": 1,
            "window_active": "yes",          # not a real bool → False
            "window_saved_preset": 42,       # not a str → None
            "window_saved_temp": float("nan"),
            "presence_active": 1,
            "boost_end_ts": "later",
            "boost_saved_temp": "19.5",      # numeric string accepted
        })
        assert p.window_active is False
        assert p.window_saved == SavedState(None, None)
        assert p.presence_active is False
        assert p.boost_end_ts == 0.0
        assert p.boost_saved.temp == 19.5


class TestNormalizeRestoredPreset:
    """Replaces the old rule "frost_protection is never kept after restart"."""

    n = staticmethod(_ctrl.normalize_restored_preset)

    # --- with a persisted snapshot -------------------------------------
    @pytest.mark.parametrize(
        "preset", ["comfort", "eco", "boost", "away", "frost_protection", "none"]
    )
    def test_snapshot_keeps_every_preset(self, preset):
        assert self.n(preset, Persisted()) == preset

    def test_snapshot_window_active_forces_frost(self):
        assert self.n("comfort", Persisted(window_active=True)) == "frost_protection"

    def test_user_frost_survives_restart(self):
        """The reported bug: frost at 5 °C came back as comfort."""
        assert self.n("frost_protection", Persisted()) == "frost_protection"

    # --- legacy: no snapshot (first start after upgrade) ----------------
    def test_legacy_user_frost_is_kept(self):
        assert self.n("frost_protection", None, legacy_window_active=False) == "frost_protection"

    def test_legacy_window_frost_falls_back_to_comfort(self):
        assert self.n("frost_protection", None, legacy_window_active=True) == "comfort"

    def test_legacy_boost_falls_back_to_comfort(self):
        assert self.n("boost", None) == "comfort"

    @pytest.mark.parametrize("preset", ["comfort", "eco", "away", "none"])
    def test_legacy_other_presets_unchanged(self, preset):
        assert self.n(preset, None, legacy_window_active=True) == preset


class TestResolveBoostRestore:
    r = staticmethod(_ctrl.resolve_boost_restore)

    def test_resume_when_time_left(self):
        res = self.r(Persisted(boost_end_ts=1600.0, boost_saved=SavedState("eco", None)), 1000.0)
        assert res.resume
        assert res.remaining_s == pytest.approx(600.0)
        assert res.fallback == SavedState("eco", None)

    def test_expired_reverts_to_saved(self):
        res = self.r(Persisted(boost_end_ts=900.0, boost_saved=SavedState("none", 19.0)), 1000.0)
        assert not res.resume
        assert res.fallback == SavedState("none", 19.0)

    def test_frost_is_a_valid_boost_fallback(self):
        res = self.r(Persisted(boost_end_ts=0.0, boost_saved=SavedState("frost_protection", 5.0)), 1.0)
        assert res.fallback.preset == "frost_protection"

    def test_unknown_fallback_is_comfort(self):
        assert self.r(Persisted(), 1.0).fallback.preset == "comfort"
        assert self.r(None, 1.0).fallback.preset == "comfort"


class TestStartupActions:
    w = staticmethod(_ctrl.window_startup_action)
    p = staticmethod(_ctrl.presence_startup_action)

    @pytest.mark.parametrize(
        "active,configured,state,expected",
        [
            (True, True, "on", "keep"),
            (True, True, "off", "restore"),
            (True, True, "unavailable", "keep"),
            (True, True, "unknown", "keep"),
            (True, True, None, "keep"),
            (True, False, None, "restore"),
            (False, True, "on", "arm_open"),
            (False, True, "off", "none"),
            (False, True, "unavailable", "none"),
            (False, False, None, "none"),
        ],
    )
    def test_window(self, active, configured, state, expected):
        assert self.w(active, configured, state) == expected

    @pytest.mark.parametrize(
        "active,configured,state,expected",
        [
            (True, True, "off", "keep"),
            (True, True, "on", "restore"),
            (True, True, "home", "restore"),
            (True, True, "unavailable", "keep"),
            (True, True, None, "keep"),
            (True, False, None, "restore"),
            (False, True, "on", "none"),
        ],
    )
    def test_presence(self, active, configured, state, expected):
        assert self.p(active, configured, state) == expected


# ===========================================================================
# Part 2 – end-to-end against the real entity code (stubbed Home Assistant)
# ===========================================================================

try:  # pragma: no cover - depends on the environment
    import homeassistant  # noqa: F401
    _HA_INSTALLED = True
except ImportError:
    _HA_INSTALLED = False

_CALL_LATER: list[dict] = []


def _stub_call_later(hass, delay, callback):
    entry = {"delay": delay, "callback": callback, "cancelled": False}
    _CALL_LATER.append(entry)

    def cancel():
        entry["cancelled"] = True

    return cancel


def _install_ha_stubs() -> None:
    def mod(name: str, **attrs) -> types.ModuleType:
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        sys.modules[name] = m
        return m

    class HVACMode(str, enum.Enum):  # noqa: UP042 - StrEnum needs py3.11+
        OFF = "off"
        HEAT = "heat"

    class HVACAction(str, enum.Enum):  # noqa: UP042
        OFF = "off"
        HEATING = "heating"
        IDLE = "idle"

    class ClimateEntityFeature(enum.IntFlag):
        TARGET_TEMPERATURE = 1
        PRESET_MODE = 16
        TURN_OFF = 128
        TURN_ON = 256

    class _Entity:
        hass = None

        async def async_added_to_hass(self):
            return None

        async def async_will_remove_from_hass(self):
            return None

        def async_on_remove(self, func):
            return None

        def async_write_ha_state(self):
            self.state_writes = getattr(self, "state_writes", 0) + 1

    class ClimateEntity(_Entity):
        pass

    class RestoreEntity(_Entity):
        _stub_last_state = None
        _stub_last_extra = None

        async def async_get_last_state(self):
            return self._stub_last_state

        async def async_get_last_extra_data(self):
            return self._stub_last_extra

    class ExtraStoredData:
        def as_dict(self):  # pragma: no cover - abstract in HA
            raise NotImplementedError

    class CoordinatorEntity(_Entity):
        def __init__(self, coordinator):
            self.coordinator = coordinator

    class HomeAssistantError(Exception):
        pass

    mod("homeassistant")
    mod("homeassistant.components")
    mod(
        "homeassistant.components.climate",
        PRESET_AWAY="away", PRESET_BOOST="boost", PRESET_COMFORT="comfort",
        PRESET_ECO="eco", PRESET_NONE="none", HVACMode=HVACMode,
        HVACAction=HVACAction, ClimateEntity=ClimateEntity,
        ClimateEntityFeature=ClimateEntityFeature,
    )
    mod("homeassistant.config_entries", ConfigEntry=object)
    mod("homeassistant.const", ATTR_TEMPERATURE="temperature", PRECISION_TENTHS=0.1,
        UnitOfTemperature=types.SimpleNamespace(CELSIUS="°C"))
    mod("homeassistant.core", CALLBACK_TYPE=object, HomeAssistant=object,
        callback=lambda f: f)
    mod("homeassistant.exceptions", HomeAssistantError=HomeAssistantError)
    mod("homeassistant.helpers")
    mod("homeassistant.helpers.device_registry", DeviceInfo=dict)
    mod("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    mod(
        "homeassistant.helpers.event",
        async_call_later=_stub_call_later,
        async_track_state_change_event=lambda *a, **k: (lambda: None),
        async_track_time_interval=lambda *a, **k: (lambda: None),
    )
    mod("homeassistant.helpers.restore_state", ExtraStoredData=ExtraStoredData,
        RestoreEntity=RestoreEntity)
    mod("homeassistant.helpers.update_coordinator", CoordinatorEntity=CoordinatorEntity)


def _load_entity_package():
    pkg_name = "tadox_proxy_e2e"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(_ROOT)]
    sys.modules[pkg_name] = pkg
    spec = importlib.util.spec_from_file_location(f"{pkg_name}.climate", _ROOT / "climate.py")
    climate = importlib.util.module_from_spec(spec)
    sys.modules[f"{pkg_name}.climate"] = climate
    spec.loader.exec_module(climate)
    return climate


if not _HA_INSTALLED:
    _install_ha_stubs()
    _climate = _load_entity_package()
else:  # pragma: no cover
    _climate = None

e2e = pytest.mark.skipif(
    _HA_INSTALLED, reason="end-to-end stubs are only used when HA is not installed"
)

WINDOW = "binary_sensor.window"
PRESENCE = "binary_sensor.presence"


class _State:
    def __init__(self, state, attributes=None):
        self.state = state
        self.attributes = attributes or {}


class _Hass:
    def __init__(self):
        self.states_by_id: dict[str, _State] = {}
        self.states = types.SimpleNamespace(get=self.states_by_id.get)

    def set(self, entity_id, state):
        self.states_by_id[entity_id] = _State(state)

    def async_create_task(self, coro, *args, **kwargs):
        coro.close()


class _Entry:
    def __init__(self, options):
        self.entry_id = "entry"
        self.title = "Test"
        self.data = {"source_entity_id": "climate.trv"}
        self.options = options

    def add_update_listener(self, _listener):
        return lambda: None


class _Coordinator:
    last_update_success = True

    def __init__(self):
        # No sensor data → regulation cycle stops at "waiting_for_sensors",
        # so no service calls are attempted.
        self.data = {"room_temp": None, "tado_internal_temp": None,
                     "tado_setpoint": None, "room_temp_ts": None}


_BASE_OPTIONS = {
    "comfort_target": 20.5,
    "eco_target": 18.5,
    "frost_protection_target": 5.0,
    "window_delay_s": 30,
    "window_close_delay_s": 0,
}


def _make_entity(options=None, *, window=True, presence=False):
    opts = dict(_BASE_OPTIONS)
    if window:
        opts["window_sensor_id"] = WINDOW
    if presence:
        opts["presence_sensor_id"] = PRESENCE
    opts.update(options or {})
    ent = _climate.TadoXProxyClimate(
        coordinator=_Coordinator(), unique_id="u", config_entry=_Entry(opts)
    )
    ent.hass = _Hass()
    return ent


def _run(coro):
    return asyncio.run(coro)


def _event(state):
    return types.SimpleNamespace(data={"new_state": _State(state)})


def _fire_last_timer():
    """Run the most recent non-cancelled call_later callback."""
    pending = [c for c in _CALL_LATER if not c["cancelled"]]
    assert pending, "no timer scheduled"
    entry = pending[-1]
    entry["cancelled"] = True
    result = entry["callback"](None)
    if asyncio.iscoroutine(result):
        _run(result)


def _active_timers():
    return [c for c in _CALL_LATER if not c["cancelled"]]


@pytest.fixture(autouse=True)
def _reset_timers():
    _CALL_LATER.clear()
    yield
    _CALL_LATER.clear()


async def _start(ent, *, last_state=None, extra=None):
    ent._stub_last_state = last_state
    ent._stub_last_extra = extra
    await ent.async_added_to_hass()


class _Extra:
    def __init__(self, data):
        self._data = data

    def as_dict(self):
        return self._data


def _restart_from(old, new):
    """Simulate HA persisting *old* and restoring it into *new*."""
    attrs = {
        "preset_mode": old.preset_mode,
        "temperature": old.target_temperature,
        "window_open_active": old._window_ctrl.is_active,
    }
    last_state = _State(old.hvac_mode.value, attrs)
    extra = _Extra(old.extra_restore_state_data.as_dict())
    _run(_start(new, last_state=last_state, extra=extra))


async def _select_preset(ent, preset):
    await ent.async_set_preset_mode(preset)


# --- Window cycle (Fix B) ---------------------------------------------------

@e2e
class TestWindowCycleEndToEnd:
    def _open_window(self, ent):
        ent.hass.set(WINDOW, "on")
        ent._async_window_changed(_event("on"))
        _fire_last_timer()
        assert ent.preset_mode == "frost_protection"
        assert ent._window_ctrl.is_active

    def _close_window(self, ent):
        ent.hass.set(WINDOW, "off")
        ent._async_window_changed(_event("off"))
        if ent._window_ctrl.close_delay_active:
            _fire_last_timer()

    def test_user_frost_survives_window_cycle(self):
        """Reported bug: frost 5 °C → window open/close → comfort."""
        ent = _make_entity()
        _run(_start(ent))
        _run(_select_preset(ent, "frost_protection"))
        self._open_window(ent)
        self._close_window(ent)
        assert ent.preset_mode == "frost_protection"
        assert ent.target_temperature == 5.0

    def test_user_frost_survives_window_cycle_with_close_delay(self):
        ent = _make_entity({"window_close_delay_s": 120})
        _run(_start(ent))
        _run(_select_preset(ent, "frost_protection"))
        self._open_window(ent)
        self._close_window(ent)
        assert ent.preset_mode == "frost_protection"

    def test_eco_survives_repeated_open_event(self):
        """Fix A: on → unavailable → on must not overwrite the saved preset."""
        ent = _make_entity()
        _run(_start(ent))
        _run(_select_preset(ent, "eco"))
        self._open_window(ent)
        ent._async_window_changed(_event("unavailable"))
        ent._async_window_changed(_event("on"))
        assert _active_timers() == []
        self._close_window(ent)
        assert ent.preset_mode == "eco"

    def test_stale_window_action_does_not_resnapshot(self):
        ent = _make_entity()
        _run(_start(ent))
        _run(ent.async_set_temperature(temperature=19.0))  # manual mode
        self._open_window(ent)
        _run(ent._async_window_action(None))  # e.g. a timer racing the guard
        self._close_window(ent)
        assert ent.preset_mode == "none"
        assert ent.target_temperature == 19.0

    def test_selecting_frost_during_window_open_is_kept(self):
        ent = _make_entity()
        _run(_start(ent))
        _run(_select_preset(ent, "comfort"))
        self._open_window(ent)
        _run(_select_preset(ent, "frost_protection"))
        assert ent._window_ctrl.get_saved().preset == "frost_protection"
        self._close_window(ent)
        assert ent.preset_mode == "frost_protection"

    def test_hvac_mode_change_during_window_restores_user_frost(self):
        ent = _make_entity()
        _run(_start(ent))
        _run(_select_preset(ent, "frost_protection"))
        self._open_window(ent)
        _run(ent.async_set_hvac_mode(_climate.HVACMode.HEAT))
        assert not ent._window_ctrl.is_active
        assert ent.preset_mode == "frost_protection"

    def test_hvac_mode_change_during_window_boost_falls_back_to_comfort(self):
        ent = _make_entity()
        _run(_start(ent))
        self._open_window(ent)
        ent._window_ctrl.update_saved("boost", 23.0)
        _run(ent.async_set_hvac_mode(_climate.HVACMode.HEAT))
        assert ent.preset_mode == "comfort"


# --- Restart / reload (Fix C) -----------------------------------------------

@e2e
class TestRestartEndToEnd:
    def test_user_frost_survives_restart(self):
        """Reported bug: all thermostats back to comfort after HA restart."""
        old = _make_entity(window=False)
        _run(_start(old))
        _run(_select_preset(old, "frost_protection"))
        new = _make_entity(window=False)
        _restart_from(old, new)
        assert new.preset_mode == "frost_protection"
        assert new.target_temperature == 5.0

    def test_user_frost_survives_reload_after_entity_removal(self):
        old = _make_entity()
        _run(_start(old))
        _run(_select_preset(old, "frost_protection"))
        _run(old.async_will_remove_from_hass())
        new = _make_entity()
        _restart_from(old, new)
        assert new.preset_mode == "frost_protection"

    def test_legacy_state_without_snapshot_keeps_user_frost(self):
        ent = _make_entity(window=False)
        last = _State("heat", {"preset_mode": "frost_protection", "temperature": 5.0,
                               "window_open_active": False})
        _run(_start(ent, last_state=last, extra=None))
        assert ent.preset_mode == "frost_protection"

    def test_legacy_state_window_frost_falls_back_to_comfort(self):
        ent = _make_entity(window=False)
        last = _State("heat", {"preset_mode": "frost_protection", "temperature": 5.0,
                               "window_open_active": True})
        _run(_start(ent, last_state=last, extra=None))
        assert ent.preset_mode == "comfort"
        assert ent.target_temperature == 20.5

    def _window_active_entity(self, saved_preset="eco"):
        old = _make_entity()
        _run(_start(old))
        _run(_select_preset(old, saved_preset))
        old.hass.set(WINDOW, "on")
        old._async_window_changed(_event("on"))
        _fire_last_timer()
        assert old._window_ctrl.is_active
        _run(old.async_will_remove_from_hass())
        return old

    def test_window_still_open_after_restart_stays_in_window_mode(self):
        old = self._window_active_entity("eco")
        new = _make_entity()
        new.hass.set(WINDOW, "on")
        _restart_from(old, new)
        assert new.preset_mode == "frost_protection"
        assert new._window_ctrl.is_active
        assert new._window_ctrl.get_saved().preset == "eco"
        assert _active_timers() == []  # no second open action armed
        # Window closes later → eco comes back
        new.hass.set(WINDOW, "off")
        new._async_window_changed(_event("off"))
        assert new.preset_mode == "eco"

    def test_window_closed_while_down_restores_on_startup(self):
        old = self._window_active_entity("eco")
        new = _make_entity()
        new.hass.set(WINDOW, "off")
        _restart_from(old, new)
        assert new.preset_mode == "eco"
        assert not new._window_ctrl.is_active

    def test_window_sensor_unavailable_at_boot_waits_for_listener(self):
        old = self._window_active_entity("frost_protection")
        new = _make_entity()
        new.hass.set(WINDOW, "unavailable")
        _restart_from(old, new)
        assert new._window_ctrl.is_active
        new.hass.set(WINDOW, "off")
        new._async_window_changed(_event("off"))
        assert new.preset_mode == "frost_protection"
        assert not new._window_ctrl.is_active

    def test_window_sensor_removed_from_config_restores(self):
        old = self._window_active_entity("eco")
        new = _make_entity(window=False)
        _restart_from(old, new)
        assert new.preset_mode == "eco"
        assert not new._window_ctrl.is_active

    def test_window_open_at_boot_without_active_mode_arms_open_delay(self):
        ent = _make_entity()
        ent.hass.set(WINDOW, "on")
        last = _State("heat", {"preset_mode": "frost_protection", "temperature": 5.0})
        _run(_start(ent, last_state=last, extra=_Extra(Persisted().as_dict())))
        assert len(_active_timers()) == 1
        _fire_last_timer()
        assert ent._window_ctrl.get_saved().preset == "frost_protection"

    def test_boost_resumes_after_restart(self):
        old = _make_entity(window=False)
        _run(_start(old))
        _run(_select_preset(old, "eco"))
        _run(_select_preset(old, "boost"))
        _run(old.async_will_remove_from_hass())
        _CALL_LATER.clear()
        new = _make_entity(window=False)
        _restart_from(old, new)
        assert new.preset_mode == "boost"
        assert new._boost_cancel is not None
        assert new.boost_remaining_minutes > 0
        assert _active_timers()[0]["delay"] <= 30 * 60
        _fire_last_timer()  # boost expires
        assert new.preset_mode == "eco"

    def test_boost_that_ended_while_down_reverts_to_saved(self):
        ent = _make_entity(window=False)
        extra = Persisted(boost_end_ts=time.time() - 60,
                          boost_saved=SavedState("frost_protection", 5.0))
        last = _State("heat", {"preset_mode": "boost", "temperature": 23.0})
        _run(_start(ent, last_state=last, extra=_Extra(extra.as_dict())))
        assert ent.preset_mode == "frost_protection"
        assert ent._boost_cancel is None

    def test_legacy_boost_without_snapshot_falls_back_to_comfort(self):
        ent = _make_entity(window=False)
        last = _State("heat", {"preset_mode": "boost", "temperature": 23.0})
        _run(_start(ent, last_state=last, extra=None))
        assert ent.preset_mode == "comfort"

    def test_presence_away_survives_restart(self):
        old = _make_entity(window=False, presence=True)
        _run(_start(old))
        _run(_select_preset(old, "frost_protection"))
        old.hass.set(PRESENCE, "off")
        old._async_presence_changed(_event("off"))
        _fire_last_timer()
        assert old.preset_mode == "away"
        _run(old.async_will_remove_from_hass())

        new = _make_entity(window=False, presence=True)
        new.hass.set(PRESENCE, "off")
        _restart_from(old, new)
        assert new.preset_mode == "away"
        assert new._presence_ctrl.get_saved().preset == "frost_protection"

        new2 = _make_entity(window=False, presence=True)
        new2.hass.set(PRESENCE, "on")
        _restart_from(old, new2)
        assert new2.preset_mode == "frost_protection"
        assert not new2._presence_ctrl.is_active

    def test_user_selected_away_is_kept_when_home_with_snapshot(self):
        ent = _make_entity(window=False, presence=True)
        ent.hass.set(PRESENCE, "on")
        last = _State("heat", {"preset_mode": "away", "temperature": 16.0})
        _run(_start(ent, last_state=last, extra=_Extra(Persisted().as_dict())))
        assert ent.preset_mode == "away"

    def test_snapshot_is_json_safe(self):
        import json

        ent = _make_entity()
        _run(_start(ent))
        json.dumps(ent.extra_restore_state_data.as_dict())
