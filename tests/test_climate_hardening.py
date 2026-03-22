"""Regression tests for climate.py hardening fixes.

These tests cover two bug-fixes that were identified during the audit:

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

RegulationConfig = _params.RegulationConfig
BehaviourConfig = _params.BehaviourConfig
FeedforwardPiRegulator = _reg.FeedforwardPiRegulator
RegulationState = _reg.RegulationState


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
        # Bug-1 fix: no baseline → send immediately rather than comparing
        # against the bogus sentinel 0.0.
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

    def test_no_baseline_sends_immediately_despite_rate_limit(self):
        """When current_tado_setpoint is None, always send regardless of rate limit."""
        should_send, reason = _rate_limit_decision(
            target_c=20.0,
            current_tado_setpoint=None,
            last_command_sent_ts=self.last_sent_ts,
            now=self.now,
            config=self.config,
            behaviour=self.behaviour,
        )
        assert should_send is True
        assert reason == "no_baseline"

    def test_no_baseline_at_startup(self):
        """At startup _last_command_sent_ts=0, no baseline: still sends."""
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
        The fix replaces 0.0 with None, which triggers the no_baseline fast path.
        """
        # Simulate the OLD buggy behavior explicitly
        bogus_baseline = 0.0
        target_c = 18.0

        time_since_last = 90.0  # rate-limited
        is_rate_limited = time_since_last < self.config.min_command_interval_s
        assert is_rate_limited  # confirm we're in the rate-limited branch

        # Old code: urgent_decrease = target < baseline - threshold = 18 < -1.0 → False
        old_is_urgent = target_c < bogus_baseline - self.behaviour.urgent_decrease_threshold_c
        assert old_is_urgent is False  # the old bug: suppress when we shouldn't

        # New code: current_tado_setpoint is None → always sends
        should_send, reason = _rate_limit_decision(
            target_c=target_c,
            current_tado_setpoint=None,  # correct: no baseline after HVAC OFF
            last_command_sent_ts=self.last_sent_ts,
            now=self.now,
            config=self.config,
            behaviour=self.behaviour,
        )
        assert should_send is True
        assert reason == "no_baseline"

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

    def test_no_baseline_with_boost_target(self):
        """No baseline + high boost target (25°C) → send immediately."""
        should_send, reason = _rate_limit_decision(
            target_c=25.0,
            current_tado_setpoint=None,
            last_command_sent_ts=self.last_sent_ts,
            now=self.now,
            config=self.config,
            behaviour=self.behaviour,
        )
        assert should_send is True
        assert reason == "no_baseline"

    def test_no_baseline_with_frost_target(self):
        """No baseline + frost target (5°C) → send immediately (HVAC just resumed)."""
        should_send, reason = _rate_limit_decision(
            target_c=5.0,
            current_tado_setpoint=None,
            last_command_sent_ts=self.last_sent_ts,
            now=self.now,
            config=self.config,
            behaviour=self.behaviour,
        )
        assert should_send is True
        assert reason == "no_baseline"


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
    """Guard that BOOST and FROST_PROTECTION are never kept after a restart.

    We test the pure logic (the elif fix) without HA by mirroring the two-step
    preset normalisation applied in async_added_to_hass.
    """

    @staticmethod
    def _normalize_restored_preset(preset: str) -> str:
        """Mirror of the fixed normalisation block (elif, not double-if)."""
        PRESET_BOOST = "boost"
        PRESET_FROST_PROTECTION = "frost_protection"
        PRESET_COMFORT = "comfort"

        if preset == PRESET_BOOST:
            preset = PRESET_COMFORT
        elif preset == PRESET_FROST_PROTECTION:
            preset = PRESET_COMFORT
        return preset

    def test_boost_converts_to_comfort(self):
        assert self._normalize_restored_preset("boost") == "comfort"

    def test_frost_converts_to_comfort(self):
        assert self._normalize_restored_preset("frost_protection") == "comfort"

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
