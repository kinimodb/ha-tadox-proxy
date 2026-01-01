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
    """Operating modes for the hybrid controller."""
    HOLD = "hold"
    BOOST = "boost"
    COAST = "coast"


@dataclass
class HybridConfig:
    """Tunable parameters for hybrid regulation (sane defaults)."""

    # Actuator limits (absolute target sent to Tado)
    min_target_c: float = 5.0
    max_target_c: float = 25.0

    # Relative clamp vs room setpoint (prevents extreme offsets)
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
    boost_target_c: float = 25.0
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
    mode: HybridMode
    error_c: float

    p_term_c: float
    i_small_c: float
    bias_c: float
    dTdt_ema_c_per_s: float
    predicted_temp_c: Optional[float]

    new_state: HybridState
    debug_info: dict[str, Any] = field(default_factory=dict)


class HybridRegulator:
    """Hybrid controller that outputs an absolute Tado target temperature."""

    def __init__(self, config: HybridConfig | None = None) -> None:
        self.config = config or HybridConfig()

    def compute_target(
        self,
        *,
        setpoint_c: float,
        room_temp_c: float,
        time_delta_s: float,
        state: HybridState,
        heating_enabled: bool = True,
    ) -> HybridResult:
        """Compute the desired absolute Tado target (°C).

        Args:
            setpoint_c: Desired room setpoint (°C).
            room_temp_c: Current room temperature (°C).
            time_delta_s: Time since last cycle (seconds).
            state: Persistent controller state.
            heating_enabled: False if HVAC is OFF (then we should coast to min).
        """
        cfg = self.config
        dt = max(0.0, float(time_delta_s))
        if dt <= 0.0:
            dt = 0.0  # keep deterministic; no updates to integrators

        # Error (positive => too cold)
        e = setpoint_c - room_temp_c

        # Update trend estimate
        dTdt_ema = self._update_trend(room_temp_c=room_temp_c, dt_s=dt, state=state)

        # If heating disabled, force COAST-like behavior (min target)
        if not heating_enabled:
            new_state = HybridState(
                mode=HybridMode.COAST,
                bias_c=state.bias_c,
                i_small_c=state.i_small_c,
                dTdt_ema_c_per_s=dTdt_ema,
                last_room_temp_c=room_temp_c,
                mode_entered_monotonic_s=state.mode_entered_monotonic_s,
                window_open_until_monotonic_s=state.window_open_until_monotonic_s,
            )
            return HybridResult(
                target_c=self._clamp_absolute_target(setpoint_c, cfg.coast_target_c),
                mode=new_state.mode,
                error_c=e,
                p_term_c=0.0,
                i_small_c=new_state.i_small_c,
                bias_c=new_state.bias_c,
                dTdt_ema_c_per_s=new_state.dTdt_ema_c_per_s,
                predicted_temp_c=None,
                new_state=new_state,
                debug_info={"reason": "heating_disabled"},
            )

        # Window-open detection / latch (forces COAST)
        now_mono = time.monotonic()
        window_open_until = state.window_open_until_monotonic_s
        window_open_triggered = False

        if cfg.window_open_enabled:
            # Keep COAST during the hold period
            if now_mono < window_open_until:
                mode: HybridMode | None = HybridMode.COAST
                mode_reason: str | None = "window_open_active"
            else:
                # Detect a very fast temperature drop
                window_thr = cfg.window_open_drop_threshold_c_per_min / 60.0  # °C/s
                if state.last_room_temp_c is not None and dt > 0 and dTdt_ema <= window_thr:
                    window_open_until = now_mono + (cfg.window_open_hold_minutes * 60.0)
                    window_open_triggered = True
                    mode = HybridMode.COAST
                    mode_reason = "window_open_detected"
                else:
                    mode = None  # decide via normal state machine
                    mode_reason = None
        else:
            mode = None
            mode_reason = None

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
            raw_target = max(cfg.boost_target_c, base + p_term)
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

            raw_target = base + p_term + tentative_i

            # Clamp; if saturated, freeze I (anti-windup for i_small)
            clamped = self._clamp_absolute_target(setpoint_c, raw_target)
            if clamped != raw_target:
                i_small_next = i_small_c
                raw_target = clamped
            else:
                i_small_next = tentative_i

        target = self._clamp_absolute_target(setpoint_c, raw_target)

        # Build new state
        if mode != state.mode:
            mode_entered = now_mono
        else:
            mode_entered = state.mode_entered_monotonic_s

        new_state = HybridState(
            mode=mode,
            bias_c=bias_c,
            i_small_c=i_small_next,
            dTdt_ema_c_per_s=dTdt_ema,
            last_room_temp_c=room_temp_c,
            mode_entered_monotonic_s=mode_entered,
            window_open_until_monotonic_s=window_open_until,
        )

        return HybridResult(
            target_c=target,
            mode=new_state.mode,
            error_c=e,
            p_term_c=p_term,
            i_small_c=new_state.i_small_c,
            bias_c=new_state.bias_c,
            dTdt_ema_c_per_s=new_state.dTdt_ema_c_per_s,
            predicted_temp_c=predicted,
            new_state=new_state,
            debug_info={
                "mode_reason": mode_reason,
                "base": base,
                "raw_target": raw_target,
                "window_open_triggered": window_open_triggered,
                "window_open_until_monotonic_s": window_open_until,
                "window_open_remaining_s": max(0.0, window_open_until - now_mono) if cfg.window_open_enabled else 0.0,
            },
        )

    def _update_trend(self, *, room_temp_c: float, dt_s: float, state: HybridState) -> float:
        """EMA of dT/dt in °C/s."""
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

        # First-order low-pass on bias with a rate limiter
        alpha = 0.0
        if cfg.bias_tau_s > 0 and dt_s > 0:
            alpha = min(1.0, dt_s / cfg.bias_tau_s)

        desired_bias = bias_c + (alpha * error_c)

        # Clamp absolute range
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
        now = time.monotonic()

        # Convert thresholds
        drop_thr = cfg.trend_drop_threshold_c_per_min / 60.0
        rise_thr = cfg.trend_rise_threshold_c_per_min / 60.0

        # COAST entry: too warm or predicted overshoot
        predicted_overshoot = False
        if predicted_temp_c is not None:
            predicted_overshoot = predicted_temp_c >= (setpoint_c + cfg.overshoot_guard_c)

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
