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


class WindowMode(str, Enum):
    """Simple window state enum used by the climate entity.

    The regulator itself only implements a window-open *latch* (to force COAST),
    but the proxy entity needs a stable enum for "open/closed" state tracking.
    """

    OPEN = "open"
    CLOSED = "closed"


@dataclass
class HybridConfig:
    """Configuration for the hybrid regulator.

    Many values are intentionally conservative, as this controller is meant to
    behave robustly on slow radiator systems.
    """

    # --- General bounds / mapping ---
    min_setpoint_c: float = 5.0
    max_setpoint_c: float = 30.0
    min_delta_send_c: float = 0.5

    # Optional clamps
    min_target_c: float = 5.0
    max_target_c: float = 30.0

    # Bias estimator / offset handling
    # bias_c is learned so that "effective room temp" = measured_room_temp + bias_c
    # Positive bias means "room is actually warmer than sensor says".
    bias_initial_c: float = 0.0
    bias_learn_rate_c_per_h: float = 0.1
    bias_deadband_c: float = 0.05
    max_bias_abs_c: float = 2.0

    # PI-ish control (light I)
    kp: float = 5.0
    ki_small: float = 0.005

    # Small I clamp (prevents windup)
    i_small_min_c: float = -1.5
    i_small_max_c: float = 1.5

    # Prediction (overshoot guard)
    prediction_window_s: float = 15 * 60.0
    prediction_gain: float = 0.6

    # Mode thresholds
    # BOOST when far below setpoint and rising slowly; COAST when near/above and rising quickly.
    boost_error_on_c: float = 0.6
    boost_error_off_c: float = 0.2
    # Minimum raise above setpoint while in BOOST (keeps valve decisively open without hard-forcing max).
    boost_target_c: float = 3.0
    boost_max_minutes: float = 30.0

    coast_error_on_c: float = -0.3
    coast_error_off_c: float = -0.1
    coast_target_c: float = 7.0

    # Prevent extreme setpoint drops below the user target.
    # Any value slightly below target is sufficient to close the valve; going far below
    # causes long recovery due to step-up limiting.
    max_setpoint_drop_below_target_c: float = 0.5

    # Overshoot guard
    overshoot_guard_on_c: float = 0.2
    overshoot_guard_off_c: float = 0.05

    # Window-open latch detection
    # If room temp drops faster than this, latch COAST for window_open_latch_minutes.
    window_drop_latch_c_per_min: float = 0.15
    window_open_latch_minutes: float = 20.0

    # --- Bias learning gates ---
    # Only learn bias when room temperature changes slowly (avoid learning during transients).
    bias_trend_max_c_per_min: float = 0.03


@dataclass
class HybridState:
    """Controller state that persists between cycles."""
    mode: HybridMode = HybridMode.HOLD

    bias_c: float = 0.0
    i_small_c: float = 0.0

    # History for trend / prediction
    last_room_temp_c: Optional[float] = None
    last_update_ts: Optional[float] = None

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

    def step(
        self,
        *,
        state: HybridState,
        room_temp_c: float,
        target_temp_c: float,
        now_ts: float,
    ) -> HybridResult:
        """Perform one regulation step.

        Inputs:
          - state: persistent controller state
          - room_temp_c: external room temperature (controlled variable)
          - target_temp_c: desired room temperature setpoint (user target)
          - now_ts: epoch seconds
        """

        cfg = self.config

        debug: dict[str, Any] = {}

        # ---------------------------------------------------------------------
        # Trend estimation / prediction
        # ---------------------------------------------------------------------
        dt_s: float = 0.0
        trend_c_per_min: float = 0.0
        predicted_temp_c: Optional[float] = None

        if state.last_room_temp_c is not None and state.last_update_ts is not None:
            dt_s = max(1.0, now_ts - state.last_update_ts)
            dtemp = room_temp_c - state.last_room_temp_c
            trend_c_per_min = (dtemp / dt_s) * 60.0

            # Simple prediction: project over prediction window with a gain
            predicted_temp_c = room_temp_c + (trend_c_per_min * (cfg.prediction_window_s / 60.0) * cfg.prediction_gain)

        debug["dt_s"] = dt_s
        debug["trend_c_per_min"] = trend_c_per_min
        debug["predicted_temp_c"] = predicted_temp_c

        # Window-open latch detection
        if trend_c_per_min < -cfg.window_drop_latch_c_per_min:
            state.window_open_until_monotonic_s = time.monotonic() + (cfg.window_open_latch_minutes * 60.0)
            debug["window_latch_triggered"] = True
        else:
            debug["window_latch_triggered"] = False

        window_latched = time.monotonic() < state.window_open_until_monotonic_s
        debug["window_latched"] = window_latched

        # ---------------------------------------------------------------------
        # Bias learning (slow)
        # ---------------------------------------------------------------------
        # We learn bias when near-stationary: small trend
        if abs(trend_c_per_min) <= cfg.bias_trend_max_c_per_min:
            # Error with current bias
            effective_room = room_temp_c + state.bias_c
            error_for_bias = target_temp_c - effective_room

            if abs(error_for_bias) > cfg.bias_deadband_c:
                # Convert rate to per second
                learn_rate_c_per_s = cfg.bias_learn_rate_c_per_h / 3600.0
                # If room is colder than target (error positive), we would like effective_room higher -> increase bias
                bias_delta = (error_for_bias) * learn_rate_c_per_s * dt_s if dt_s > 0 else 0.0
                state.bias_c += bias_delta
                # Clamp
                state.bias_c = max(-cfg.max_bias_abs_c, min(cfg.max_bias_abs_c, state.bias_c))
                debug["bias_delta"] = bias_delta
            else:
                debug["bias_delta"] = 0.0
        else:
            debug["bias_delta"] = 0.0
            debug["bias_learning_suppressed"] = True

        debug["bias_c"] = state.bias_c

        # ---------------------------------------------------------------------
        # Main control
        # ---------------------------------------------------------------------
        effective_room_temp_c = room_temp_c + state.bias_c
        error_c = target_temp_c - effective_room_temp_c

        debug["effective_room_temp_c"] = effective_room_temp_c
        debug["error_c"] = error_c

        # Overshoot guard uses prediction if available
        overshoot_risk = False
        if predicted_temp_c is not None:
            predicted_effective = predicted_temp_c + state.bias_c
            overshoot_risk = predicted_effective > (target_temp_c + cfg.overshoot_guard_on_c)
        debug["overshoot_risk"] = overshoot_risk

        # Mode transitions (state machine)
        new_mode = state.mode
        mode_reason = "stay"

        # Window latch forces COAST
        if window_latched:
            new_mode = HybridMode.COAST
            mode_reason = "window_latch"
        else:
            if state.mode != HybridMode.BOOST and error_c >= cfg.boost_error_on_c:
                new_mode = HybridMode.BOOST
                mode_reason = "error_high"
            elif state.mode == HybridMode.BOOST and error_c <= cfg.boost_error_off_c:
                new_mode = HybridMode.HOLD
                mode_reason = "boost_exit"
            elif state.mode != HybridMode.COAST and (error_c <= cfg.coast_error_on_c or overshoot_risk):
                new_mode = HybridMode.COAST
                mode_reason = "overshoot_or_above"
            elif state.mode == HybridMode.COAST and error_c >= cfg.coast_error_off_c and not overshoot_risk:
                new_mode = HybridMode.HOLD
                mode_reason = "coast_exit"

        # If mode changed, reset mode timer
        if new_mode != state.mode:
            state.mode = new_mode
            state.mode_entered_monotonic_s = time.monotonic()

        debug["mode_reason"] = mode_reason
        debug["mode"] = state.mode.value

        # Compute P term
        p_term_c = cfg.kp * error_c
        debug["p_term_c"] = p_term_c

        # Small integral action
        if dt_s > 0:
            state.i_small_c += cfg.ki_small * error_c * (dt_s / 60.0)  # normalize to minutes
            state.i_small_c = max(cfg.i_small_min_c, min(cfg.i_small_max_c, state.i_small_c))
        debug["i_small_c"] = state.i_small_c

        # Target mapping to setpoint:
        # - HOLD: aim near target with P+I
        # - BOOST: add a boost offset above target to open valve decisively
        # - COAST: keep setpoint slightly below the user target (close valve without extreme drops)
        desired_target_c = target_temp_c + p_term_c + state.i_small_c

        if state.mode == HybridMode.BOOST:
            desired_target_c = max(desired_target_c, target_temp_c + cfg.boost_target_c)
            # safety max time in BOOST
            if (time.monotonic() - state.mode_entered_monotonic_s) > (cfg.boost_max_minutes * 60.0):
                state.mode = HybridMode.HOLD
                state.mode_entered_monotonic_s = time.monotonic()
                debug["boost_timeout"] = True

        elif state.mode == HybridMode.COAST:
            # For heating systems, any value slightly below the user target is enough to close the valve.
            # Avoid driving the setpoint far below target (would cause slow recovery with step-up limiting).
            desired_target_c = min(desired_target_c, target_temp_c - cfg.max_setpoint_drop_below_target_c)

        # Floor: never drive the underlying setpoint far below the user target.
        below_target_floor_c = target_temp_c - cfg.max_setpoint_drop_below_target_c
        desired_target_c = max(desired_target_c, below_target_floor_c)
        debug["below_target_floor_c"] = below_target_floor_c

        # Clamp
        desired_target_c = max(cfg.min_target_c, min(cfg.max_target_c, desired_target_c))
        debug["desired_target_c_pre_clamp"] = desired_target_c

        # Update history
        state.last_room_temp_c = room_temp_c
        state.last_update_ts = now_ts

        return HybridResult(
            target_c=desired_target_c,
            error_c=error_c,
            p_term_c=p_term_c,
            predicted_temp_c=predicted_temp_c,
            mode=state.mode,
            new_state=state,
            debug_info=debug,
        )
