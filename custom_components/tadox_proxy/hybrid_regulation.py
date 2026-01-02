"""
Hybrid regulation logic (bias estimator + state machine) for Tado X Proxy.

This module is used by the hybrid-control branch.

Concept:
- Bias estimator: slow long-term offset learning to compensate sensor/actuator bias.
- Fast comfort response: proportional term (+ optional small I).
- State machine: BOOST / HOLD / COAST to handle non-linear heating dynamics and load changes.
- Window-open latch: force COAST when temperature drops very quickly.
- Command hygiene (rate/step limits) is handled where commands are sent (typically climate entity).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

_LOGGER = logging.getLogger(__name__)


class HybridMode(str, Enum):
    BOOST = "boost"
    HOLD = "hold"
    COAST = "coast"


@dataclass
class HybridConfig:
    """Configuration for the hybrid regulator.

    Note on BOOST:
    - boost_target_c is intentionally defined as a *relative floor* above the setpoint,
      not as an absolute target.
    - This avoids hard-forcing 25°C for small errors while still opening the valve decisively.
    """

    # Absolute limits for commands sent to the TRV / cloud
    min_target_c: float = 5.0
    max_target_c: float = 25.0

    # Relative clamp around room setpoint (prevents silly values relative to target)
    max_offset_c: float = 8.0

    # --- Comfort control (fast) ---
    kp: float = 5.0
    ki_small: float = 0.0002
    i_small_min_c: float = -2.0
    i_small_max_c: float = 2.0

    # --- Bias estimator (slow) ---
    bias_tau_s: float = 4 * 3600.0  # 4 hours
    bias_deadband_c: float = 0.1
    bias_trend_max_c_per_min: float = 0.01  # only learn when nearly stationary
    bias_rate_limit_c_per_h: float = 0.5
    bias_min_c: float = -5.0
    bias_max_c: float = 5.0

    # --- Trend / prediction ---
    dTdt_alpha: float = 0.25
    trend_drop_threshold_c_per_min: float = -0.03  # falling quickly
    trend_rise_threshold_c_per_min: float = 0.03   # rising quickly
    predict_horizon_s: float = 900.0               # 15 minutes
    overshoot_guard_c: float = 0.2

    # --- Window-open / rapid-loss handling ---
    # If the room temperature drops very quickly, it is often due to an open window/door.
    # In that case we close the valve (COAST) for a limited time to avoid wasting energy
    # and to prevent overshoot once the window is closed.
    window_open_enabled: bool = True
    window_open_drop_threshold_c_per_min: float = -0.2  # very fast drop => assume window open
    window_open_hold_minutes: float = 15.0

    # --- State thresholds ---
    hold_deadband_c: float = 0.1

    boost_error_on_c: float = 0.6
    boost_error_off_c: float = 0.2
    # Minimum raise above setpoint while in BOOST (keeps valve decisively open without hard-forcing max).
    boost_target_c: float = 3.0
    boost_max_minutes: float = 30.0

    coast_error_on_c: float = -0.3
    coast_error_off_c: float = -0.1
    coast_target_c: float = 7.0


@dataclass
class HybridState:
    """Controller state that persists between cycles."""
    mode: HybridMode = HybridMode.HOLD

    # Long-term offset (learned)
    bias_c: float = 0.0

    # Small integrator (comfort only)
    i_small_c: float = 0.0

    # Trend estimator
    dTdt_ema_c_per_s: float = 0.0
    last_room_temp_c: Optional[float] = None

    # Mode timing
    mode_entered_monotonic_s: float = field(default_factory=time.monotonic)

    # Window-open latch (monotonic deadline). While active, the controller forces COAST.
    window_open_until_monotonic_s: float = 0.0


@dataclass
class HybridResult:
    """Result of one regulation cycle."""
    target_c: float
    error_c: float
    p_term_c: float
    predicted_temp_c: Optional[float]
    mode: HybridMode
    new_state: HybridState
    debug_info: dict[str, Any]


class HybridRegulator:
    """Hybrid thermostat controller."""

    def __init__(self, config: HybridConfig) -> None:
        self.config = config

    def compute_target(
        self,
        *,
        setpoint_c: float,
        room_temp_c: float,
        time_delta_s: float,
        state: HybridState,
        heating_enabled: bool,
    ) -> HybridResult:
        cfg = self.config
        dt = float(time_delta_s or 0.0)

        # Error: positive means "too cold"
        e = float(setpoint_c - room_temp_c)

        # Update trend estimator
        dTdt_ema = self._update_trend(room_temp_c=room_temp_c, dt_s=dt, state=state)

        # Window-open detection (very fast drop): latch COAST
        now = time.monotonic()
        if cfg.window_open_enabled and dt > 0:
            window_thr = cfg.window_open_drop_threshold_c_per_min / 60.0  # °C/s
            if dTdt_ema <= window_thr:
                state.window_open_until_monotonic_s = now + (cfg.window_open_hold_minutes * 60.0)

        # Determine base mode
        mode: Optional[HybridMode] = None
        mode_reason: Optional[str] = None

        # Forced off: treat as COAST
        if not heating_enabled:
            mode = HybridMode.COAST
            mode_reason = "heating_disabled"

        # Window-open latch active -> force COAST
        if mode is None and state.window_open_until_monotonic_s > now:
            mode = HybridMode.COAST
            mode_reason = "window_open_detected"

        # Predict temperature at horizon (for overshoot guard)
        predicted = None
        if cfg.predict_horizon_s > 0:
            predicted = room_temp_c + dTdt_ema * cfg.predict_horizon_s

        # Decide mode transitions
        if mode is None:
            mode, mode_reason = self._decide_mode(
                setpoint_c=setpoint_c,
                room_temp_c=room_temp_c,
                error_c=e,
                dTdt_ema_c_per_s=dTdt_ema,
                predicted_temp_c=predicted,
                state=state,
            )

        # Build target according to mode
        bias_c = state.bias_c
        i_small_c = state.i_small_c

        # In HOLD we may update bias and i_small
        if mode == HybridMode.HOLD and dt > 0:
            bias_c = self._update_bias(
                bias_c=bias_c,
                error_c=e,
                dTdt_ema_c_per_s=dTdt_ema,
                dt_s=dt,
            )

        base = setpoint_c + bias_c
        p_term = cfg.kp * e

        if mode == HybridMode.BOOST:
            # BOOST floor is relative to setpoint (setpoint + boost_target_c), not absolute.
            raw_target = max(setpoint_c + cfg.boost_target_c, base + p_term)
            i_small_next = i_small_c  # freeze
        elif mode == HybridMode.COAST:
            raw_target = cfg.coast_target_c
            i_small_next = i_small_c  # freeze
        else:
            # HOLD: allow small I (optional)
            i_small_next = i_small_c
            if cfg.ki_small > 0 and dt > 0:
                tentative_i = i_small_c + (e * cfg.ki_small * dt)
                tentative_i = max(cfg.i_small_min_c, min(cfg.i_small_max_c, tentative_i))
            else:
                tentative_i = i_small_c
            i_small_next = tentative_i
            raw_target = base + p_term + i_small_next

        # Clamp target
        target_c = self._clamp_absolute_target(setpoint_c=setpoint_c, target_c=raw_target)

        # Update state fields
        new_state = HybridState(
            mode=mode,
            bias_c=bias_c,
            i_small_c=i_small_next,
            dTdt_ema_c_per_s=dTdt_ema,
            last_room_temp_c=room_temp_c,
            mode_entered_monotonic_s=state.mode_entered_monotonic_s,
            window_open_until_monotonic_s=state.window_open_until_monotonic_s,
        )

        # Update mode_entered if mode changed
        if state.mode != mode:
            new_state.mode_entered_monotonic_s = now

        debug = {
            "mode_reason": mode_reason,
        }

        return HybridResult(
            target_c=target_c,
            error_c=e,
            p_term_c=p_term,
            predicted_temp_c=predicted,
            mode=mode,
            new_state=new_state,
            debug_info=debug,
        )

    def _update_trend(self, *, room_temp_c: float, dt_s: float, state: HybridState) -> float:
        cfg = self.config

        if dt_s <= 0 or state.last_room_temp_c is None:
            return state.dTdt_ema_c_per_s

        raw = (room_temp_c - state.last_room_temp_c) / dt_s
        return (cfg.dTdt_alpha * raw) + ((1.0 - cfg.dTdt_alpha) * state.dTdt_ema_c_per_s)

    def _update_bias(self, *, bias_c: float, error_c: float, dTdt_ema_c_per_s: float, dt_s: float) -> float:
        """Slow offset learning, only near steady-state."""
        cfg = self.config

        # Only learn when close to target and near-stationary
        dTdt_c_per_min = dTdt_ema_c_per_s * 60.0
        if abs(error_c) > cfg.bias_deadband_c:
            return bias_c
        if abs(dTdt_c_per_min) > cfg.bias_trend_max_c_per_min:
            return bias_c
        if cfg.bias_tau_s <= 0:
            return bias_c

        # Desired bias change is proportional to error (very small per dt)
        desired_bias = bias_c + (error_c * (dt_s / cfg.bias_tau_s))

        # Clamp bias
        desired_bias = max(cfg.bias_min_c, min(cfg.bias_max_c, desired_bias))

        # Rate limit (per hour)
        max_step = (cfg.bias_rate_limit_c_per_h / 3600.0) * dt_s
        if desired_bias > bias_c + max_step:
            desired_bias = bias_c + max_step
        elif desired_bias < bias_c - max_step:
            desired_bias = bias_c - max_step

        return desired_bias

    def _decide_mode(
        self,
        *,
        setpoint_c: float,
        room_temp_c: float,
        error_c: float,
        dTdt_ema_c_per_s: float,
        predicted_temp_c: Optional[float],
        state: HybridState,
    ) -> tuple[HybridMode, str]:
        """State transitions."""
        cfg = self.config

        # Trend thresholds
        drop_thr = cfg.trend_drop_threshold_c_per_min / 60.0
        rise_thr = cfg.trend_rise_threshold_c_per_min / 60.0

        predicted_overshoot = False
        if predicted_temp_c is not None:
            if predicted_temp_c >= setpoint_c + cfg.overshoot_guard_c:
                predicted_overshoot = True

        now = time.monotonic()

        # Current mode handling
        if state.mode == HybridMode.BOOST:
            elapsed_min = (now - state.mode_entered_monotonic_s) / 60.0
            if elapsed_min >= cfg.boost_max_minutes:
                return HybridMode.HOLD, "boost_timeout"
            if error_c <= cfg.boost_error_off_c and dTdt_ema_c_per_s > drop_thr:
                return HybridMode.HOLD, "boost_recovered"
            return HybridMode.BOOST, "boost_active"

        if state.mode == HybridMode.COAST:
            if error_c >= cfg.coast_error_off_c:
                return HybridMode.HOLD, "coast_recovered"
            return HybridMode.COAST, "coast_active"

        # HOLD mode decisions
        if error_c <= cfg.coast_error_on_c or predicted_overshoot:
            return HybridMode.COAST, "coast_enter"

        if error_c >= cfg.boost_error_on_c or dTdt_ema_c_per_s <= drop_thr:
            return HybridMode.BOOST, "boost_enter"

        # Otherwise remain in HOLD
        # (Optionally: if very close to target and rising quickly, pre-empt COAST)
        if abs(error_c) <= cfg.hold_deadband_c and dTdt_ema_c_per_s >= rise_thr:
            return HybridMode.HOLD, "hold_near_target_rising"

        return HybridMode.HOLD, "hold_normal"

    def _clamp_absolute_target(self, setpoint_c: float, target_c: float) -> float:
        """Clamp absolute target respecting both absolute limits and max offset vs setpoint."""
        cfg = self.config
        # Relative clamp around room setpoint
        rel_min = setpoint_c - cfg.max_offset_c
        rel_max = setpoint_c + cfg.max_offset_c
        clamped = max(rel_min, min(rel_max, target_c))
        # Absolute clamp
        clamped = max(cfg.min_target_c, min(cfg.max_target_c, clamped))
        # Round to 0.1 °C to match HA climate precision / Tado typical resolution
        return round(clamped, 1)


# Commit: fix: make boost floor relative to setpoint (avoid hard-forced 25C)
