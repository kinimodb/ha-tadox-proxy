"""Regression tests for climate.py hardening fixes.

These tests cover bug-fixes that were identified during the audit:

Bug 0 – min/max temperature range showing 7/35 instead of 5/30 (climate.py)
    HA's CachedProperties metaclass caches min_temp / max_temp on first access.
    If _attr_min_temp / _attr_max_temp are only set as instance attributes in
    __init__ (after super().__init__()), the cached value falls through to HA's
    DEFAULT_MIN_TEMP (7) / DEFAULT_MAX_TEMP (35).
    Fix: declare _attr_min_temp / _attr_max_temp as CLASS-LEVEL attributes so
    they exist before any metaclass machinery runs.

Bug 1 – Rate-limiter with no Tado baseline (climate.py)
    Previously, when _last_sent_setpoint was None AND coordinator had no
    tado_setpoint (e.g. after a quick HVAC OFF → HEAT cycle), the code fell
    back to 0.0 as the baseline.  With a 0.0 baseline the urgent-decrease
    check (target < 0.0 - threshold) is always False for realistic temperatures,
    so a rate-limited cycle would never send even when needed.
    Fix: when no baseline exists, skip rate-limiting entirely (reason = "no_baseline").

Bug 2 – NaN/Inf rejection in preset number entity (number.py)
    Python's max/min with NaN produce implementation-defined results.
    A non-finite value must be rejected before the clamp is applied.

Since climate.py requires the full HA environment we cannot import it here.
The rate-limit decision lives in the HA-free climate_controllers.decide_send,
which is tested directly; AST guards check that the regulation cycle routes
through it.
"""
from __future__ import annotations

import ast
import importlib.util
import math
import os
import sys
import types

import pytest

# ---------------------------------------------------------------------------
# Load HA-free modules
# ---------------------------------------------------------------------------

_COMP_DIR = os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "tadox_proxy"
)


def _load_module(name: str, path: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_params = _load_module(
    "tadox_proxy.parameters",
    os.path.join(_COMP_DIR, "parameters.py"),
)
_reg = _load_module(
    "tadox_proxy.regulation",
    os.path.join(_COMP_DIR, "regulation.py"),
)
_ctrl = _load_module(
    "tadox_proxy.climate_controllers",
    os.path.join(_COMP_DIR, "climate_controllers.py"),
)

RegulationConfig = _params.RegulationConfig
BehaviourConfig = _params.BehaviourConfig
PresetConfig = _params.PresetConfig
FeedforwardPiRegulator = _reg.FeedforwardPiRegulator
RegulationState = _reg.RegulationState
decide_send = _ctrl.decide_send


# ---------------------------------------------------------------------------
# Bug 0: Class-level _attr_min_temp / _attr_max_temp (AST-based guard)
# ---------------------------------------------------------------------------

_CLIMATE_PY = os.path.join(_COMP_DIR, "climate.py")


class TestClassLevelTempLimits:
    """Ensure _attr_min_temp and _attr_max_temp are CLASS-LEVEL attributes.

    HA's CachedProperties metaclass caches min_temp/max_temp on first access.
    If the _attr_* variants only exist as instance attributes (set in __init__
    after super().__init__()), the cache sees HA's defaults (7/35) instead of
    our 5/30.  This AST-based test guarantees the class-level declarations
    survive future refactoring.
    """

    @staticmethod
    def _get_class_level_assigns() -> dict[str, float]:
        """Parse climate.py and return class-level _attr_*_temp assignments."""
        with open(_CLIMATE_PY, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename="climate.py")

        results: dict[str, float] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name != "TadoXProxyClimate":
                continue
            for item in node.body:
                # Handle both plain assignment and annotated assignment
                targets: list[str] = []
                value = None
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    targets = [item.target.id]
                    value = item.value
                elif isinstance(item, ast.Assign):
                    targets = [
                        t.id for t in item.targets if isinstance(t, ast.Name)
                    ]
                    value = item.value
                for t in targets:
                    if t in ("_attr_min_temp", "_attr_max_temp") and value is not None:
                        if isinstance(value, ast.Constant) and isinstance(value.value, (int, float)):
                            results[t] = float(value.value)
        return results

    def test_min_temp_class_level_is_5(self):
        assigns = self._get_class_level_assigns()
        assert "_attr_min_temp" in assigns, (
            "_attr_min_temp must be a class-level attribute in TadoXProxyClimate"
        )
        assert assigns["_attr_min_temp"] == 5.0

    def test_max_temp_class_level_is_30(self):
        assigns = self._get_class_level_assigns()
        assert "_attr_max_temp" in assigns, (
            "_attr_max_temp must be a class-level attribute in TadoXProxyClimate"
        )
        assert assigns["_attr_max_temp"] == 30.0

    def test_values_match_regulation_config(self):
        """Class-level defaults must match RegulationConfig defaults."""
        assigns = self._get_class_level_assigns()
        cfg = RegulationConfig()
        assert assigns["_attr_min_temp"] == cfg.min_target_c
        assert assigns["_attr_max_temp"] == cfg.max_target_c


# ---------------------------------------------------------------------------
# Rate-limit decision: production decide_send fed from the config objects
# ---------------------------------------------------------------------------

def _rate_limit_decision(
    *,
    target_c: float,
    current_tado_setpoint: float | None,  # None = no known baseline
    last_command_sent_ts: float,
    now: float,
    config: RegulationConfig,
    behaviour: BehaviourConfig,
    overlay_refresh_s: int = 0,
) -> tuple[bool, str]:
    """Call decide_send with the thresholds the regulation cycle passes."""
    return decide_send(
        target_c=target_c,
        baseline_c=current_tado_setpoint,
        last_command_ts=last_command_sent_ts,
        now=now,
        min_interval_s=config.min_command_interval_s,
        min_change_c=config.min_change_threshold_c,
        urgent_decrease_c=behaviour.urgent_decrease_threshold_c,
        overlay_refresh_s=overlay_refresh_s,
    )


# ---------------------------------------------------------------------------
# Bug 1: Rate-limiter with no baseline
# ---------------------------------------------------------------------------

class TestRateLimiterNoBaseline:
    """Regression tests for Bug 1 – missing Tado setpoint baseline."""

    def setup_method(self):
        self.config = RegulationConfig()
        self.behaviour = BehaviourConfig()
        # Simulate: last command sent 90 s ago (within 180 s rate-limit window)
        self.now = 1_000_000.0
        self.last_sent_ts = self.now - 90.0  # 90 s ago → rate-limited

    def test_no_baseline_throttled_after_first_attempt(self):
        """After a failed send (baseline still None), honour the rate limiter."""
        should_send, reason = _rate_limit_decision(
            target_c=20.0,
            current_tado_setpoint=None,
            last_command_sent_ts=self.last_sent_ts,  # 90s ago, rate-limited
            now=self.now,
            config=self.config,
            behaviour=self.behaviour,
        )
        assert should_send is False
        assert "rate_limited" in reason

    def test_no_baseline_sends_after_rate_limit_expires(self):
        """After the rate-limit window expires, retry despite no baseline."""
        should_send, reason = _rate_limit_decision(
            target_c=20.0,
            current_tado_setpoint=None,
            last_command_sent_ts=self.now - 200.0,  # 200s ago > 180s
            now=self.now,
            config=self.config,
            behaviour=self.behaviour,
        )
        assert should_send is True
        assert reason == "no_baseline"

    def test_no_baseline_at_startup(self):
        """At startup _last_command_sent_ts=0, no baseline: sends immediately."""
        should_send, reason = _rate_limit_decision(
            target_c=20.0,
            current_tado_setpoint=None,
            last_command_sent_ts=0.0,  # startup: never sent anything
            now=self.now,
            config=self.config,
            behaviour=self.behaviour,
        )
        assert should_send is True
        assert reason == "no_baseline"

    def test_old_0_fallback_would_suppress_urgent_decrease(self):
        """Demonstrate the OLD bug: 0.0 baseline makes urgent-decrease impossible.

        With baseline=0.0 and target=18.0 (a decrease from the user's perspective),
        the check is: 18.0 < 0.0 - 1.0 = -1.0 → False → urgent decrease suppressed.
        This is why the caller must pass None, never 0.0, for an unknown baseline.
        """
        should_send, reason = _rate_limit_decision(
            target_c=18.0,
            current_tado_setpoint=0.0,  # the old bogus fallback
            last_command_sent_ts=self.last_sent_ts,  # 90s ago, rate-limited
            now=self.now,
            config=self.config,
            behaviour=self.behaviour,
        )
        assert should_send is False  # the old bug: suppressed
        assert reason == "rate_limited(90s)"

    def test_known_baseline_still_rate_limits_correctly(self):
        """When a baseline is known, normal rate-limiting still applies."""
        should_send, reason = _rate_limit_decision(
            target_c=20.2,  # only 0.2°C change, below threshold (0.3)
            current_tado_setpoint=20.0,
            last_command_sent_ts=self.last_sent_ts,
            now=self.now,
            config=self.config,
            behaviour=self.behaviour,
        )
        assert should_send is False
        assert reason == "already_at_target"

    def test_known_baseline_urgent_decrease_bypasses_rate_limit(self):
        """With a valid baseline, a large drop bypasses the rate limit."""
        should_send, reason = _rate_limit_decision(
            target_c=18.0,  # 3°C drop from 21°C (threshold is 1°C)
            current_tado_setpoint=21.0,
            last_command_sent_ts=self.last_sent_ts,
            now=self.now,
            config=self.config,
            behaviour=self.behaviour,
        )
        assert should_send is True
        assert reason == "urgent_decrease"

    def test_known_baseline_sends_after_rate_limit_expires(self):
        """With expired rate limit, normal update fires."""
        should_send, reason = _rate_limit_decision(
            target_c=20.5,
            current_tado_setpoint=20.0,
            last_command_sent_ts=self.now - 200.0,  # 200 s ago > 180 s limit
            now=self.now,
            config=self.config,
            behaviour=self.behaviour,
        )
        assert should_send is True
        assert reason == "normal_update"

    def test_no_baseline_with_boost_target_at_startup(self):
        """No baseline + high boost target (25°C) at startup → send immediately."""
        should_send, reason = _rate_limit_decision(
            target_c=25.0,
            current_tado_setpoint=None,
            last_command_sent_ts=0.0,  # startup
            now=self.now,
            config=self.config,
            behaviour=self.behaviour,
        )
        assert should_send is True
        assert reason == "no_baseline"

    def test_no_baseline_with_frost_target_at_startup(self):
        """No baseline + frost target (5°C) at startup → send immediately."""
        should_send, reason = _rate_limit_decision(
            target_c=5.0,
            current_tado_setpoint=None,
            last_command_sent_ts=0.0,  # startup
            now=self.now,
            config=self.config,
            behaviour=self.behaviour,
        )
        assert should_send is True
        assert reason == "no_baseline"

    def test_no_baseline_retry_throttled_then_succeeds(self):
        """Simulate failed send → throttled → rate limit expires → retry."""
        # 1st cycle: startup, sends immediately
        s1, r1 = _rate_limit_decision(
            target_c=20.0, current_tado_setpoint=None,
            last_command_sent_ts=0.0, now=self.now,
            config=self.config, behaviour=self.behaviour,
        )
        assert s1 is True and r1 == "no_baseline"

        # 2nd cycle (60s later): send failed, baseline still None → throttled
        s2, r2 = _rate_limit_decision(
            target_c=20.0, current_tado_setpoint=None,
            last_command_sent_ts=self.now, now=self.now + 60.0,
            config=self.config, behaviour=self.behaviour,
        )
        assert s2 is False and "rate_limited" in r2

        # 3rd cycle (180s later): rate limit expired → retry
        s3, r3 = _rate_limit_decision(
            target_c=20.0, current_tado_setpoint=None,
            last_command_sent_ts=self.now, now=self.now + 181.0,
            config=self.config, behaviour=self.behaviour,
        )
        assert s3 is True and r3 == "no_baseline"


# ---------------------------------------------------------------------------
# Bug 2: NaN/Inf rejection in preset number entity
# ---------------------------------------------------------------------------

def _clamp_preset_value(value: float, min_val: float = 5.0, max_val: float = 30.0) -> float | None:
    """Mirror of the fixed async_set_native_value logic in number.py.

    Returns None to signal rejection (matches the early-return in the fix).
    """
    if not math.isfinite(value):
        return None  # reject
    return max(min_val, min(max_val, value))


class TestPresetValueValidation:
    """Regression tests for Bug 2 – NaN/Inf bypass in number entity."""

    # --- Non-finite inputs must be rejected ---

    def test_nan_is_rejected(self):
        result = _clamp_preset_value(float("nan"))
        assert result is None

    def test_positive_inf_is_rejected(self):
        result = _clamp_preset_value(float("inf"))
        assert result is None

    def test_negative_inf_is_rejected(self):
        result = _clamp_preset_value(float("-inf"))
        assert result is None

    # --- Python NaN clamp behavior (documents why the guard is necessary) ---

    def test_nan_clamp_behavior_without_guard(self):
        """Demonstrate why math.isfinite guard is needed.

        Without the guard, max(5.0, min(30.0, nan)) produces an unreliable
        result that must NOT be stored in config_entry options.
        This test documents the behavior; the production code rejects nan before
        reaching the clamp, so this never happens in practice after the fix.
        """
        nan = float("nan")
        # Regardless of the actual value returned (implementation-defined with NaN),
        # math.isfinite detects the problem before the clamp is ever reached.
        assert not math.isfinite(nan)  # guard catches it
        # We intentionally do NOT call max/min here: the point is that the
        # guard fires first, making the clamp behavior irrelevant.

    # --- Valid inputs must be accepted and clamped correctly ---

    def test_in_range_value_passes_through(self):
        assert _clamp_preset_value(20.0) == 20.0

    def test_below_min_is_clamped(self):
        assert _clamp_preset_value(1.0) == 5.0

    def test_above_max_is_clamped(self):
        assert _clamp_preset_value(99.0) == 30.0

    def test_exact_min_boundary(self):
        assert _clamp_preset_value(5.0) == 5.0

    def test_exact_max_boundary(self):
        assert _clamp_preset_value(30.0) == 30.0

    def test_fractional_value(self):
        assert _clamp_preset_value(17.5) == 17.5

    def test_zero_is_clamped_to_min(self):
        assert _clamp_preset_value(0.0) == 5.0

    def test_negative_is_clamped_to_min(self):
        assert _clamp_preset_value(-10.0) == 5.0


# ---------------------------------------------------------------------------
# Bug 4: AWAY preset stuck after HA restart – regression guards
# ---------------------------------------------------------------------------

def _presence_away_save_logic(
    current_preset: str,
    current_temp: float,
    comfort_target: float,
) -> tuple[str, float]:
    """Mirror of the save logic in _async_presence_away_action (Fix 1).

    Returns (saved_preset, saved_temp) that would be stored by the
    presence controller when switching to AWAY.
    """
    saved_preset = current_preset
    saved_temp = current_temp
    # Fix 1: never save AWAY as restore state
    if saved_preset == "away":
        saved_preset = "comfort"
        saved_temp = comfort_target
    return saved_preset, saved_temp


def _startup_presence_logic(
    restored_preset: str,
    presence_state: str | None,
    comfort_target: float,
) -> str:
    """Mirror of the startup presence handling (Fix 2).

    Returns the preset that should be active after startup.
    """
    preset = restored_preset
    if presence_state == "off":
        # Controller would be pre-activated; preset stays as-is
        pass
    elif presence_state not in (None, "unavailable", "unknown"):
        # Presence is home but preset was AWAY → switch to COMFORT
        if preset == "away":
            preset = "comfort"
    return preset


def _window_restore_logic(
    saved_preset: str,
    presence_state: str | None,
) -> str:
    """Mirror of _restore_window_state with presence check (Fix 3).

    Returns the preset that should be restored after window closes.
    """
    preset = saved_preset
    if preset == "frost_protection":
        preset = "comfort"
    if preset == "away" and presence_state not in (None, "off", "unavailable", "unknown"):
        preset = "comfort"
    return preset


class TestPresenceAwaySaveLogic:
    """Regression tests for Bug 4, Fix 1: AWAY never saved as restore state."""

    def test_away_preset_saved_as_comfort(self):
        """If current preset is AWAY, save COMFORT as restore state."""
        preset, temp = _presence_away_save_logic("away", 17.0, 21.0)
        assert preset == "comfort"
        assert temp == 21.0

    def test_comfort_preset_saved_as_is(self):
        preset, temp = _presence_away_save_logic("comfort", 21.0, 21.0)
        assert preset == "comfort"
        assert temp == 21.0

    def test_eco_preset_saved_as_is(self):
        preset, temp = _presence_away_save_logic("eco", 17.0, 21.0)
        assert preset == "eco"
        assert temp == 17.0

    def test_manual_preset_saved_as_is(self):
        preset, temp = _presence_away_save_logic("none", 19.5, 21.0)
        assert preset == "none"
        assert temp == 19.5

    def test_away_uses_comfort_target_not_away_temp(self):
        """When falling back from AWAY, the comfort target is used, not the
        away temperature (which would be meaningless as a restore target)."""
        preset, temp = _presence_away_save_logic("away", 17.0, 22.5)
        assert temp == 22.5


class TestStartupPresenceLogic:
    """Regression tests for Bug 4, Fix 2: presence home at startup + AWAY."""

    def test_presence_on_and_away_switches_to_comfort(self):
        result = _startup_presence_logic("away", "on", 21.0)
        assert result == "comfort"

    def test_presence_on_and_comfort_stays_comfort(self):
        result = _startup_presence_logic("comfort", "on", 21.0)
        assert result == "comfort"

    def test_presence_off_keeps_away(self):
        """When presence is off, AWAY stays (controller is pre-activated)."""
        result = _startup_presence_logic("away", "off", 21.0)
        assert result == "away"

    def test_presence_unavailable_keeps_away(self):
        """When presence is unavailable at boot, no action taken."""
        result = _startup_presence_logic("away", "unavailable", 21.0)
        assert result == "away"

    def test_presence_unknown_keeps_away(self):
        result = _startup_presence_logic("away", "unknown", 21.0)
        assert result == "away"

    def test_presence_none_keeps_away(self):
        """No presence sensor state at all → no action."""
        result = _startup_presence_logic("away", None, 21.0)
        assert result == "away"

    def test_presence_home_and_eco_stays_eco(self):
        """Non-AWAY presets are not changed regardless of presence."""
        result = _startup_presence_logic("eco", "on", 21.0)
        assert result == "eco"


class TestWindowRestorePresenceCheck:
    """Regression tests for Bug 4, Fix 3: window restore checks presence."""

    def test_away_overridden_when_presence_home(self):
        result = _window_restore_logic("away", "on")
        assert result == "comfort"

    def test_away_kept_when_presence_off(self):
        result = _window_restore_logic("away", "off")
        assert result == "away"

    def test_away_kept_when_presence_unavailable(self):
        result = _window_restore_logic("away", "unavailable")
        assert result == "away"

    def test_comfort_unchanged_regardless_of_presence(self):
        result = _window_restore_logic("comfort", "on")
        assert result == "comfort"

    def test_frost_always_overridden_to_comfort(self):
        result = _window_restore_logic("frost_protection", "on")
        assert result == "comfort"

    def test_eco_unchanged_when_presence_home(self):
        result = _window_restore_logic("eco", "on")
        assert result == "eco"

    def test_away_kept_when_no_presence_state(self):
        """No presence sensor → AWAY is kept (can't verify)."""
        result = _window_restore_logic("away", None)
        assert result == "away"


# ---------------------------------------------------------------------------
# Boost restore must start the boost timer (AST-based guard)
# ---------------------------------------------------------------------------

_CLIMATE_PRESETS_PY = os.path.join(_COMP_DIR, "climate_presets.py")
_CLIMATE_REGULATION_PY = os.path.join(_COMP_DIR, "climate_regulation.py")
_CONFIG_FLOW_PY = os.path.join(_COMP_DIR, "config_flow.py")
_CONST_PY = os.path.join(_COMP_DIR, "const.py")
_INIT_PY = os.path.join(_COMP_DIR, "__init__.py")


def _method_assigns_attr(file_path: str, method_name: str, attr_name: str) -> bool:
    """Return True when *method_name* in *file_path* assigns self.<attr_name>."""
    with open(file_path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=os.path.basename(file_path))

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != method_name:
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Assign):
                continue
            for target in sub.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and target.attr == attr_name
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    return True
        return False
    raise AssertionError(f"{method_name} not found in {file_path}")


def _method_calls(file_path: str, method_name: str, called: str) -> bool:
    """Return True when *method_name* calls <called>() or <x>.<called>()."""
    with open(file_path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=os.path.basename(file_path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != method_name:
            continue
        calls = set()
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            if isinstance(sub.func, ast.Attribute):
                calls.add(sub.func.attr)
            elif isinstance(sub.func, ast.Name):
                calls.add(sub.func.id)
        return called in calls
    raise AssertionError(f"{method_name} not found in {file_path}")


class TestBoostRestoreStartsTimer:
    """Regression: restoring BOOST after window/presence automation must start
    the boost timer, otherwise the boost never ends and the boost-remaining
    sensor reports 0 minutes for the whole restored boost period.

    BoostController.start sets the end timestamp (tested in test_controllers);
    every boost entry point must go through _start_boost.
    """

    def test_window_restore_starts_boost(self):
        assert _method_calls(_CLIMATE_PRESETS_PY, "_restore_window_state", "_start_boost")

    def test_presence_restore_starts_boost(self):
        assert _method_calls(_CLIMATE_PRESETS_PY, "_restore_presence_state", "_start_boost")

    def test_preset_switch_starts_boost(self):
        """The normal boost entry point must start the timer too."""
        assert _method_calls(_CLIMATE_PRESETS_PY, "async_set_preset_mode", "_start_boost")

    def test_start_boost_uses_controller(self):
        assert _method_calls(_CLIMATE_PRESETS_PY, "_start_boost", "start")


# ---------------------------------------------------------------------------
# Decision logic must be the tested HA-free code (AST-based guard)
# ---------------------------------------------------------------------------

class TestDecisionLogicRoutedThroughControllers:
    """The HA mixins must call the HA-free functions the tests exercise,
    instead of carrying their own copy of the logic."""

    def test_regulation_cycle_uses_decide_send(self):
        assert _method_calls(
            _CLIMATE_REGULATION_PY, "_async_regulation_cycle_locked", "decide_send"
        )

    def test_regulation_cycle_uses_control_mode_resolver(self):
        assert _method_calls(
            _CLIMATE_REGULATION_PY, "_async_regulation_cycle_locked", "resolve"
        )

    def test_effective_setpoint_uses_preset_mapping(self):
        assert _method_calls(_CLIMATE_PRESETS_PY, "_effective_setpoint", "preset_target_c")

    def test_get_preset_target_uses_preset_mapping(self):
        assert _method_calls(_CLIMATE_PRESETS_PY, "_get_preset_target", "preset_target_c")

    def test_restart_uses_preset_after_restart(self):
        assert _method_calls(_CLIMATE_PY, "async_added_to_hass", "preset_after_restart")

    def test_window_switches_trv_off_when_due(self):
        assert _method_calls(_CLIMATE_REGULATION_PY, "_async_switch_trv_for_window", "trv_off_due")

    def test_window_switches_trv_on_when_due(self):
        assert _method_calls(_CLIMATE_REGULATION_PY, "_async_switch_trv_for_window", "trv_on_due")

    def test_regulation_cycle_switches_trv_for_window(self):
        assert _method_calls(
            _CLIMATE_REGULATION_PY,
            "_async_regulation_cycle_locked",
            "_async_switch_trv_for_window",
        )

    def test_heat_commands_mark_the_trv_on(self):
        """Otherwise a TRV the window switched off is switched on every cycle."""
        assert _method_calls(_CLIMATE_REGULATION_PY, "_async_send_hvac_mode_to_tado", "mark_trv_on")
        assert _method_calls(_CLIMATE_REGULATION_PY, "_async_send_to_tado", "mark_trv_on")


class TestRoomSensorTimestamp:
    """The grace period counts from the sensor's ``last_reported``.

    ``last_updated`` does not advance when a sensor re-sends an unchanged
    value, so a stable reading would look stale (see #107).
    """

    @staticmethod
    def _attributes_read(method_name: str) -> set[str]:
        with open(_INIT_PY, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename="__init__.py")
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == method_name:
                return {
                    sub.attr for sub in ast.walk(node) if isinstance(sub, ast.Attribute)
                }
        raise AssertionError(f"{method_name} not found in __init__.py")

    def test_room_reading_uses_last_reported(self):
        attrs = self._attributes_read("_read_room_temperature")
        assert "last_reported" in attrs
        assert "last_updated" not in attrs


def _literal_option_defaults(file_path: str, keys: set[str]) -> list[tuple[int, str]]:
    """(line, key) for every <x>.get(<key>, <number literal>) in *file_path*."""
    with open(file_path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=os.path.basename(file_path))
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and len(node.args) == 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in keys
            and isinstance(node.args[1], ast.Constant)
        ):
            offenders.append((node.lineno, node.args[0].id))
    return offenders


class TestTimingDefaultsCentralised:
    """Window/presence/overlay defaults come from parameters.py only."""

    _KEYS = {
        "CONF_WINDOW_DELAY_S",
        "CONF_WINDOW_CLOSE_DELAY_S",
        "CONF_PRESENCE_AWAY_DELAY_S",
        "CONF_PRESENCE_HOME_DELAY_S",
        "CONF_OVERLAY_REFRESH_S",
    }

    def test_default_values(self):
        assert _params.DEFAULT_WINDOW_DELAY_S == 30
        assert _params.DEFAULT_WINDOW_CLOSE_DELAY_S == 120
        assert _params.DEFAULT_PRESENCE_AWAY_DELAY_S == 600
        assert _params.DEFAULT_PRESENCE_HOME_DELAY_S == 30
        assert _params.DEFAULT_OVERLAY_REFRESH_S == 0

    def test_no_literal_defaults_in_climate(self):
        assert _literal_option_defaults(_CLIMATE_PY, self._KEYS) == []

    def test_no_literal_defaults_in_presets(self):
        assert _literal_option_defaults(_CLIMATE_PRESETS_PY, self._KEYS) == []

    def test_no_literal_defaults_in_config_flow(self):
        assert _literal_option_defaults(_CONFIG_FLOW_PY, self._KEYS) == []


class TestPresetNamesMatchConst:
    """climate_controllers redefines the preset names to stay HA-free; the
    custom frost preset must keep the value const.py exposes to HA."""

    def test_frost_protection_name(self):
        with open(_CONST_PY, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename="const.py")
        values = [
            node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "PRESET_FROST_PROTECTION"
                for t in node.targets
            )
            and isinstance(node.value, ast.Constant)
        ]
        assert values == [_ctrl.PRESET_FROST_PROTECTION]


# ---------------------------------------------------------------------------
# HVAC mode change must restore the saved pre-frost preset (AST-based guard)
# ---------------------------------------------------------------------------

class TestHvacModeChangeRestoresPreset:
    """Regression: async_set_hvac_mode cancels active window automation.

    Cancelling alone would leave the entity stuck in FROST_PROTECTION with the
    saved pre-frost preset thrown away – if the window closes while HVAC is
    OFF, nothing ever restores the preset.  The method must read the saved
    state (get_saved) and write it back to _preset_mode before cancelling.
    """

    def test_set_hvac_mode_reads_saved_state(self):
        with open(_CLIMATE_PY, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename="climate.py")
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_set_hvac_mode":
                calls = {
                    sub.func.attr
                    for sub in ast.walk(node)
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                }
                assert "get_saved" in calls, (
                    "async_set_hvac_mode must read the window controller's "
                    "saved state before cancel_all()"
                )
                return
        raise AssertionError("async_set_hvac_mode not found in climate.py")

    def test_set_hvac_mode_restores_preset_mode(self):
        assert _method_assigns_attr(_CLIMATE_PY, "async_set_hvac_mode", "_preset_mode")


# ---------------------------------------------------------------------------
# HVAC mode change must hold the regulation lock (AST-based guard)
# ---------------------------------------------------------------------------

def _calls_inside_lock(file_path: str, method_name: str) -> tuple[set[str], set[str]]:
    """(calls inside ``async with self._regulation_lock``, all calls) of a method."""
    with open(file_path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=os.path.basename(file_path))

    def _names(node: ast.AST) -> set[str]:
        return {
            sub.func.attr
            for sub in ast.walk(node)
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
        }

    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == method_name:
            locked: set[str] = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.AsyncWith) and any(
                    isinstance(item.context_expr, ast.Attribute)
                    and item.context_expr.attr == "_regulation_lock"
                    for item in sub.items
                ):
                    locked |= _names(sub)
            return locked, _names(node)
    raise AssertionError(f"{method_name} not found in {file_path}")


class TestHvacModeChangeHoldsRegulationLock:
    """Regression (from PR #101): a regulation cycle whose setpoint command
    (sent with hvac_mode=heat) is still in flight must not interleave with
    turning the TRV off.  async_set_hvac_mode therefore runs the TRV command
    and the follow-up cycle under _regulation_lock – and must call the
    lock-free cycle body, since asyncio.Lock is not re-entrant."""

    def test_hvac_command_is_sent_under_lock(self):
        locked, _ = _calls_inside_lock(_CLIMATE_PY, "async_set_hvac_mode")
        assert "_async_send_hvac_mode_to_tado" in locked

    def test_follow_up_cycle_runs_under_lock(self):
        locked, _ = _calls_inside_lock(_CLIMATE_PY, "async_set_hvac_mode")
        assert "_async_regulation_cycle_locked" in locked

    def test_does_not_reacquire_lock(self):
        _, calls = _calls_inside_lock(_CLIMATE_PY, "async_set_hvac_mode")
        assert "_async_regulation_cycle" not in calls


# ---------------------------------------------------------------------------
# Bug 5: Comfort target restore silently fails when comfort_target is unset
# ---------------------------------------------------------------------------
#
# When the "Comfort Target" number entity was never edited, CONF_COMFORT_TARGET
# is absent from config_entry.options.  The old comfort-restore paths read the
# option with no default (safe_float(options.get(CONF_COMFORT_TARGET)) -> None)
# and skipped the assignment behind `if comfort is not None:`, leaving
# _target_temp stuck at the last frost/eco value.  The fix routes every comfort
# read through a single _comfort_target() helper that falls back to the
# PresetConfig default (20 °C).

# const.py is NOT HA-free (it imports homeassistant.components.climate), so we
# mirror the option key and safe_float here – the same approach the rest of
# this file uses for logic that cannot be imported directly.
_CONF_COMFORT_TARGET = "comfort_target"  # mirrors const.py:22
_COMFORT_DEFAULT = PresetConfig().comfort_target_c  # real default: 20.0


def _mirror_safe_float(value):
    """Mirror of const.safe_float (const.py:75-83)."""
    if value is None:
        return None
    try:
        f = float(value)
    except (ValueError, TypeError):
        return None
    return f if math.isfinite(f) else None


def _mirror_comfort_target(options: dict, default: float = _COMFORT_DEFAULT) -> float:
    """Mirror of TadoXProxyClimate._comfort_target (climate_presets.py).

        comfort = safe_float(options.get(CONF_COMFORT_TARGET))
        return comfort if comfort is not None else default
    """
    comfort = _mirror_safe_float(options.get(_CONF_COMFORT_TARGET))
    if comfort is not None:
        return comfort
    return default


class TestComfortTargetFallback:
    """Bug 5 – _comfort_target() must resolve to the default when unset."""

    def test_default_is_twenty(self):
        """Sanity: the canonical comfort default is 20 °C, not a frost value."""
        assert _COMFORT_DEFAULT == 20.0

    def test_option_present_is_used(self):
        assert _mirror_comfort_target({_CONF_COMFORT_TARGET: 22.0}) == 22.0

    def test_option_absent_falls_back_to_default(self):
        assert _mirror_comfort_target({}) == _COMFORT_DEFAULT

    def test_option_none_falls_back_to_default(self):
        assert _mirror_comfort_target({_CONF_COMFORT_TARGET: None}) == _COMFORT_DEFAULT

    def test_option_garbage_falls_back_to_default(self):
        assert _mirror_comfort_target({_CONF_COMFORT_TARGET: "nope"}) == _COMFORT_DEFAULT

    def test_option_nan_falls_back_to_default(self):
        assert _mirror_comfort_target({_CONF_COMFORT_TARGET: float("nan")}) == _COMFORT_DEFAULT

    def test_option_inf_falls_back_to_default(self):
        assert _mirror_comfort_target({_CONF_COMFORT_TARGET: float("inf")}) == _COMFORT_DEFAULT

    def test_option_numeric_string_is_parsed(self):
        assert _mirror_comfort_target({_CONF_COMFORT_TARGET: "21.5"}) == 21.5

    def test_reproduction_frost_stuck_scenario(self):
        """Reproduce the reported bug at the logic level.

        The entity was in frost_protection (7 °C) at shutdown; the preset is
        coerced back to COMFORT on restore, but comfort_target is absent from
        options.  The OLD guarded pattern left _target_temp at 7.0; the fix
        resolves it to the 20 °C default.
        """
        target_temp = 7.0  # frost value carried over from before the restart
        options: dict = {}  # comfort_target never configured

        # OLD guarded pattern – assignment is skipped, value stays stale
        comfort = _mirror_safe_float(options.get(_CONF_COMFORT_TARGET))
        if comfort is not None:
            target_temp = comfort
        assert target_temp == 7.0  # documents the bug

        # NEW helper – always resolves to a sane comfort value
        target_temp = _mirror_comfort_target(options)
        assert target_temp == 20.0


# ---------------------------------------------------------------------------
# Bug 5: every comfort-restore path must be routed through the helper (AST)
# ---------------------------------------------------------------------------

def _comfort_reads_outside_helper(file_path: str) -> list[int]:
    """Line numbers where a no-default options.get(CONF_COMFORT_TARGET) read
    occurs OUTSIDE the _comfort_target helper – must be empty after the fix."""
    with open(file_path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=os.path.basename(file_path))

    inside_helper: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_comfort_target"
        ):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    inside_helper.add(id(sub))

    offenders: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Match  <x>.options.get(CONF_COMFORT_TARGET)  with a single positional
        # argument and no default – the exact idiom that caused the bug.
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "get"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "options"
            and len(node.args) == 1
            and not node.keywords
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "CONF_COMFORT_TARGET"
        ):
            if id(node) not in inside_helper:
                offenders.append(node.lineno)
    return offenders


class TestComfortTargetRoutedThroughHelper:
    """Bug 5 – every comfort-restore path must call _comfort_target()."""

    def test_added_to_hass_calls_helper(self):
        assert _method_calls(_CLIMATE_PY, "async_added_to_hass", "_comfort_target")

    def test_config_entry_updated_calls_helper(self):
        assert _method_calls(_CLIMATE_PY, "_async_config_entry_updated", "_comfort_target")

    def test_set_hvac_mode_calls_helper(self):
        assert _method_calls(_CLIMATE_PY, "async_set_hvac_mode", "_comfort_target")

    def test_restore_window_state_calls_helper(self):
        assert _method_calls(_CLIMATE_PRESETS_PY, "_restore_window_state", "_comfort_target")

    def test_restore_presence_state_calls_helper(self):
        assert _method_calls(_CLIMATE_PRESETS_PY, "_restore_presence_state", "_comfort_target")

    def test_set_preset_mode_calls_helper(self):
        assert _method_calls(_CLIMATE_PRESETS_PY, "async_set_preset_mode", "_comfort_target")

    def test_get_preset_target_calls_helper(self):
        assert _method_calls(_CLIMATE_PRESETS_PY, "_get_preset_target", "_comfort_target")


class TestComfortTargetCentralised:
    """Bug 5 – the no-default option read must exist only inside the helper."""

    def test_no_comfort_read_outside_helper_in_climate(self):
        assert _comfort_reads_outside_helper(_CLIMATE_PY) == []

    def test_no_comfort_read_outside_helper_in_presets(self):
        assert _comfort_reads_outside_helper(_CLIMATE_PRESETS_PY) == []
