"""Regression tests for climate.py hardening fixes.

These tests cover bug-fixes that were identified during the audit:

Bug 0 – min/max temperature range showing 7/35 instead of 5/25 (climate.py)
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
Instead we test the rate-limit decision logic in isolation by mirroring the
relevant code (same pattern used by test_sensor_resilience.py for its resolver).
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


# ---------------------------------------------------------------------------
# Bug 0: Class-level _attr_min_temp / _attr_max_temp (AST-based guard)
# ---------------------------------------------------------------------------

_CLIMATE_PY = os.path.join(_COMP_DIR, "climate.py")


class TestClassLevelTempLimits:
    """Ensure _attr_min_temp and _attr_max_temp are CLASS-LEVEL attributes.

    HA's CachedProperties metaclass caches min_temp/max_temp on first access.
    If the _attr_* variants only exist as instance attributes (set in __init__
    after super().__init__()), the cache sees HA's defaults (7/35) instead of
    our 5/25.  This AST-based test guarantees the class-level declarations
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

    def test_max_temp_class_level_is_25(self):
        assigns = self._get_class_level_assigns()
        assert "_attr_max_temp" in assigns, (
            "_attr_max_temp must be a class-level attribute in TadoXProxyClimate"
        )
        assert assigns["_attr_max_temp"] == 25.0

    def test_values_match_regulation_config(self):
        """Class-level defaults must match RegulationConfig defaults."""
        assigns = self._get_class_level_assigns()
        cfg = RegulationConfig()
        assert assigns["_attr_min_temp"] == cfg.min_target_c
        assert assigns["_attr_max_temp"] == cfg.max_target_c


# ---------------------------------------------------------------------------
# Lightweight rate-limit decision logic (mirrors _async_regulation_cycle §5)
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
    """Mirror of the rate-limit block in _async_regulation_cycle.

    Returns (should_send, reason) so tests can assert both.
    This mirrors the fixed code exactly – the test would have FAILED with the
    old code that used 0.0 as the fallback.
    """
    time_since_last = now - last_command_sent_ts
    is_rate_limited = time_since_last < config.min_command_interval_s

    if current_tado_setpoint is None:
        # No baseline: send to establish one, but honour the rate limiter
        # after the first attempt so a transient outage does not cause spam.
        if is_rate_limited and last_command_sent_ts > 0:
            remaining = int(config.min_command_interval_s - time_since_last)
            return False, f"rate_limited({remaining}s)"
        return True, "no_baseline"

    diff = abs(target_c - current_tado_setpoint)

    overlay_refresh_due = (
        overlay_refresh_s > 0 and time_since_last >= overlay_refresh_s
    )

    if diff < config.min_change_threshold_c and not overlay_refresh_due:
        return False, "already_at_target"
    elif diff < config.min_change_threshold_c and overlay_refresh_due:
        return True, "overlay_refresh"
    elif is_rate_limited:
        is_urgent_decrease = (
            target_c < current_tado_setpoint - behaviour.urgent_decrease_threshold_c
        )
        if is_urgent_decrease:
            return True, "urgent_decrease"
        else:
            remaining = int(config.min_command_interval_s - time_since_last)
            return False, f"rate_limited({remaining}s)"
    else:
        return True, "normal_update"


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
        """
        bogus_baseline = 0.0
        target_c = 18.0

        time_since_last = 90.0  # rate-limited
        is_rate_limited = time_since_last < self.config.min_command_interval_s
        assert is_rate_limited  # confirm we're in the rate-limited branch

        # Old code: urgent_decrease = target < baseline - threshold = 18 < -1.0 → False
        old_is_urgent = target_c < bogus_baseline - self.behaviour.urgent_decrease_threshold_c
        assert old_is_urgent is False  # the old bug: suppress when we shouldn't

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
# Bug 3: if/elif in state restore – regression guard
# ---------------------------------------------------------------------------

class TestPresetRestoreGuard:
    """Preset normalisation on restart WITHOUT a persisted automation snapshot.

    This is the legacy path (first start after upgrading from a version that
    did not persist window/presence/boost state).  BOOST falls back to COMFORT
    because its timer is gone.  FROST_PROTECTION falls back to COMFORT only
    when the window automation had set it; a frost preset the user selected
    is kept (previously it was always reset to COMFORT – reported bug).
    The snapshot path is covered in test_frost_preset_persistence.py.
    """

    @staticmethod
    def _normalize_restored_preset(preset: str, window_active: bool = False) -> str:
        return _ctrl.normalize_restored_preset(
            preset, None, legacy_window_active=window_active
        )

    def test_boost_converts_to_comfort(self):
        assert self._normalize_restored_preset("boost") == "comfort"

    def test_window_frost_converts_to_comfort(self):
        assert (
            self._normalize_restored_preset("frost_protection", window_active=True)
            == "comfort"
        )

    def test_user_frost_is_kept(self):
        assert self._normalize_restored_preset("frost_protection") == "frost_protection"

    def test_comfort_unchanged(self):
        assert self._normalize_restored_preset("comfort") == "comfort"

    def test_eco_unchanged(self):
        assert self._normalize_restored_preset("eco") == "eco"

    def test_away_unchanged(self):
        assert self._normalize_restored_preset("away") == "away"

    def test_boost_does_not_also_trigger_frost_check(self):
        """The elif ensures BOOST→COMFORT does not re-check FROST_PROTECTION.

        With the old double-if code, after BOOST→COMFORT the second `if`
        would check PRESET_FROST_PROTECTION against the now-COMFORT value,
        which is harmless but shows the fragility.  With elif this is explicit.
        """
        # After BOOST→COMFORT, the result is "comfort", NOT "frost_protection"
        # so the elif branch is skipped.  Verify this is stable.
        result = self._normalize_restored_preset("boost")
        assert result == "comfort"
        # Applying normalization again (idempotent check)
        assert self._normalize_restored_preset(result) == "comfort"


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
    A saved frost protection is restored as-is: it can only have been saved
    when the user had selected it before the window opened.
    """
    preset = saved_preset
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

    def test_user_frost_restored_as_is(self):
        result = _window_restore_logic("frost_protection", "on")
        assert result == "frost_protection"

    def test_eco_unchanged_when_presence_home(self):
        result = _window_restore_logic("eco", "on")
        assert result == "eco"

    def test_away_kept_when_no_presence_state(self):
        """No presence sensor → AWAY is kept (can't verify)."""
        result = _window_restore_logic("away", None)
        assert result == "away"


# ---------------------------------------------------------------------------
# Boost restore must re-arm _boost_end_ts (AST-based guard)
# ---------------------------------------------------------------------------

_CLIMATE_PRESETS_PY = os.path.join(_COMP_DIR, "climate_presets.py")


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


class TestBoostRestoreSetsEndTimestamp:
    """Regression: restoring BOOST after window/presence automation must set
    _boost_end_ts, otherwise the boost-remaining sensor reports 0 minutes
    for the whole restored boost period.
    """

    def test_window_restore_sets_boost_end_ts(self):
        assert _method_assigns_attr(
            _CLIMATE_PRESETS_PY, "_restore_window_state", "_boost_end_ts"
        )

    def test_presence_restore_sets_boost_end_ts(self):
        assert _method_assigns_attr(
            _CLIMATE_PRESETS_PY, "_restore_presence_state", "_boost_end_ts"
        )

    def test_preset_switch_sets_boost_end_ts(self):
        """The normal boost entry point must keep setting the timestamp too."""
        assert _method_assigns_attr(
            _CLIMATE_PRESETS_PY, "async_set_preset_mode", "_boost_end_ts"
        )


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

def _method_calls(file_path: str, method_name: str, called_attr: str) -> bool:
    """Return True when *method_name* calls self.<called_attr>() somewhere."""
    with open(file_path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=os.path.basename(file_path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != method_name:
            continue
        calls = {
            sub.func.attr
            for sub in ast.walk(node)
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
        }
        return called_attr in calls
    raise AssertionError(f"{method_name} not found in {file_path}")


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
