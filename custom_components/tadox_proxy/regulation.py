"""Regulation core for tadox_proxy.

This module is intentionally HA-framework-agnostic: it contains no entity code and
no coordinator code. It only computes the "actuator target temperature" that should
be sent to the underlying (real) climate entity.

Design goals (from project concept):
- Full PID (P/I/D) to reduce overshoot via derivative term and trend-based braking.
- Anti-windup to avoid integral runaway when output saturates.
- Deadband (hysteresis zone) to avoid micro-adjustments and valve/battery wear.
- Minimum on/off times to avoid short cycling.
- Command rate-limit to protect cloud APIs and reduce actuator movements.

All timings are expressed in seconds, using time.monotonic()-style timestamps.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import math
from typing import Optional


_LOGGER = logging.getLogger(__name__)


def _clamp(value: float, vmin: float, vmax: float) -> float:
    return max(vmin, min(vmax, value))


def _is_finite(x: float) -> bool:
    return not (math.isnan(x) or math.isinf(x))


@dataclass(frozen=True)
class PidTuning:
    """PID tuning parameters.

    kp: proportional gain [delta_temp / temp_error]
    ki: integral gain [delta_temp / (temp_error * second)]
    kd: derivative gain [delta_temp / (temp_per_second)]
        Note: we apply derivative on measurement to avoid derivative kick.
    """

    kp: float = 1.20
    ki: float = 0.010
    kd: float = 8.0


@dataclass(frozen=True)
class RegulationConfig:
    """Regulation parameters and safety rails."""

    tuning: PidTuning = PidTuning()

    # If abs(error) <= deadband, output is driven to 0 (with mild integral decay).
    deadband_c: float = 0.10

    # Output is a delta added to the user setpoint (°C). Clamp prevents insane values.
    max_delta_c: float = 5.0

    # Absolute actuator (target) limits sent to underlying climate.
    min_target_c: float = 5.0
    max_target_c: float = 25.0

    # Rate limit: do not send new targets more often than this.
    min_command_interval_s: float = 300.0  # 5 min

    # Anti short-cycling: once heating is considered "on", keep it for min_on_s, and
    # once "off", keep it for min_off_s.
    min_on_s: float = 300.0   # 5 min
    min_off_s: float = 300.0  # 5 min

    # Heating state thresholds on the PID output (delta above setpoint).
    # Use hysteresis to avoid chatter.
    heat_on_threshold_delta_c: float = 0.20
    heat_off_threshold_delta_c: float = 0.05

    # Integral clamp (anti-windup rail). This is a clamp on the integral *term* (°C).
    integral_term_min_c: float = -2.0
    integral_term_max_c: float = 2.0

    # Derivative smoothing (EMA). 0 disables smoothing.
    # alpha in [0..1], where 1 means "no smoothing" (use newest derivative).
    derivative_ema_alpha: float = 0.35

    # Trend-based overshoot protection: if temperature is rising and projected to
    # overshoot within lookahead_s, brake output towards 0.
    overshoot_lookahead_s: float = 240.0
    overshoot_margin_c: float = 0.05
    overshoot_brake_strength: float = 0.75  # 0..1 (fraction of output reduced)


@dataclass
class PidState:
    """Mutable controller state (persist per device)."""

    # Integral term already multiplied by ki (i.e., in °C units).
    integral_term_c: float = 0.0

    # Last measurement/time
    last_temp_c: Optional[float] = None
    last_ts_s: Optional[float] = None

    # Smoothed derivative on measurement (temp per second)
    dtemp_dt_c_per_s_ema: float = 0.0
    has_dtemp_ema: bool = False

    # Last computed (raw) output delta and last target
    last_output_delta_c: float = 0.0
    last_target_c: Optional[float] = None

    # Last time we *allowed* a new command (rate limit)
    last_sent_ts_s: Optional[float] = None

    # Heating-state latch for min on/off enforcement
    heating_on: bool = False
    heating_state_change_ts_s: Optional[float] = None


@dataclass(frozen=True)
class RegulationDecision:
    """Result of a regulation step."""

    target_c: float
    output_delta_c: float
    p_c: float
    i_c: float
    d_c: float
    error_c: float
    reason: str
    rate_limited: bool
    deadband_active: bool
    heating_on: bool
    dtemp_dt_c_per_s: float


class PidRegulator:
    """PID regulator that returns an actuator target temperature."""

    def __init__(self, config: RegulationConfig, state: Optional[PidState] = None) -> None:
        self._cfg = config
        self._st = state if state is not None else PidState()

    @property
    def state(self) -> PidState:
        return self._st

    @property
    def config(self) -> RegulationConfig:
        return self._cfg

    def reset(self) -> None:
        self._st.integral_term_c = 0.0
        self._st.last_temp_c = None
        self._st.last_ts_s = None
        self._st.dtemp_dt_c_per_s_ema = 0.0
        self._st.has_dtemp_ema = False
        self._st.last_output_delta_c = 0.0
        self._st.last_target_c = None
        self._st.last_sent_ts_s = None
        self._st.heating_on = False
        self._st.heating_state_change_ts_s = None

    def step(
        self,
        *,
        user_setpoint_c: float,
        measured_temp_c: float,
        now_ts_s: float,
        window_open: bool = False,
        force_off: bool = False,
    ) -> RegulationDecision:
        """Compute next target temperature for the underlying climate.

        - user_setpoint_c: desired room temperature (set on proxy)
        - measured_temp_c: current room temperature (external sensor preferred)
        - now_ts_s: monotonic timestamp
        - window_open: if true, force output towards "off"/hold (conservative)
        - force_off: if true, force output to minimum target (frost safe)
        """

        # Basic validation: keep the system predictable even with bad sensor values.
        if not _is_finite(user_setpoint_c) or not _is_finite(measured_temp_c):
            fallback = self._st.last_target_c
            if fallback is None:
                fallback = _clamp(user_setpoint_c, self._cfg.min_target_c, self._cfg.max_target_c)
            return RegulationDecision(
                target_c=fallback,
                output_delta_c=0.0,
                p_c=0.0,
                i_c=self._st.integral_term_c,
                d_c=0.0,
                error_c=0.0,
                reason="invalid_input_fallback",
                rate_limited=True,
                deadband_active=False,
                heating_on=self._st.heating_on,
                dtemp_dt_c_per_s=self._st.dtemp_dt_c_per_s_ema if self._st.has_dtemp_ema else 0.0,
            )

        cfg = self._cfg
        st = self._st
        tuning = cfg.tuning

        error_c = user_setpoint_c - measured_temp_c

        # Determine dt
        dt_s: float
        if st.last_ts_s is None or st.last_temp_c is None:
            dt_s = cfg.min_command_interval_s  # conservative first step
        else:
            dt_s = max(1.0, now_ts_s - st.last_ts_s)

        # Derivative on measurement
        dtemp_dt = 0.0
        if st.last_ts_s is not None and st.last_temp_c is not None:
            dtemp_dt = (measured_temp_c - st.last_temp_c) / dt_s

        # EMA smoothing for derivative
        alpha = _clamp(cfg.derivative_ema_alpha, 0.0, 1.0)
        if alpha <= 0.0:
            st.dtemp_dt_c_per_s_ema = dtemp_dt
            st.has_dtemp_ema = True
        else:
            if not st.has_dtemp_ema:
                st.dtemp_dt_c_per_s_ema = dtemp_dt
                st.has_dtemp_ema = True
            else:
                st.dtemp_dt_c_per_s_ema = alpha * dtemp_dt + (1.0 - alpha) * st.dtemp_dt_c_per_s_ema

        dtemp_dt_smooth = st.dtemp_dt_c_per_s_ema if st.has_dtemp_ema else dtemp_dt

        # Forced modes (e.g., window open / user off)
        if force_off:
            target = cfg.min_target_c
            st.last_target_c = target
            st.last_output_delta_c = 0.0
            self._update_time_and_temp(measured_temp_c, now_ts_s)
            self._set_heating_state(False, now_ts_s, reason="force_off")
            return RegulationDecision(
                target_c=target,
                output_delta_c=0.0,
                p_c=0.0,
                i_c=st.integral_term_c,
                d_c=0.0,
                error_c=error_c,
                reason="force_off",
                rate_limited=False,
                deadband_active=False,
                heating_on=st.heating_on,
                dtemp_dt_c_per_s=dtemp_dt_smooth,
            )

        if window_open:
            # Conservative: do not chase. Hold close-ish to setpoint, clamp, and decay integral.
            st.integral_term_c *= 0.90
            target = _clamp(user_setpoint_c, cfg.min_target_c, cfg.max_target_c)
            st.last_target_c = target
            st.last_output_delta_c = 0.0
            self._update_time_and_temp(measured_temp_c, now_ts_s)
            self._set_heating_state(False, now_ts_s, reason="window_open")
            return RegulationDecision(
                target_c=target,
                output_delta_c=0.0,
                p_c=0.0,
                i_c=st.integral_term_c,
                d_c=0.0,
                error_c=error_c,
                reason="window_open_hold",
                rate_limited=False,
                deadband_active=True,
                heating_on=st.heating_on,
                dtemp_dt_c_per_s=dtemp_dt_smooth,
            )

        # Deadband logic: tolerate small fluctuations
        if abs(error_c) <= cfg.deadband_c:
            # Gentle integral decay to prevent "memory" from pushing after reaching setpoint.
            st.integral_term_c *= 0.92
            p_c = 0.0
            d_c = 0.0
            output = 0.0
            reason = "deadband"
            deadband_active = True
        else:
            deadband_active = False

            p_c = tuning.kp * error_c

            # Integral update with clamp (anti-windup rail). Integral is already "term" in °C.
            st.integral_term_c += (tuning.ki * error_c * dt_s)
            st.integral_term_c = _clamp(st.integral_term_c, cfg.integral_term_min_c, cfg.integral_term_max_c)

            # Derivative on measurement (avoid derivative kick): d = -kd * dT/dt
            d_c = -tuning.kd * dtemp_dt_smooth

            output = p_c + st.integral_term_c + d_c
            output = _clamp(output, -cfg.max_delta_c, cfg.max_delta_c)
            reason = "pid"

            # Trend-based overshoot protection: if we're heating (output>0) and temp is rising,
            # and projection would overshoot soon, brake output towards 0.
            if output > 0.0 and dtemp_dt_smooth > 0.0:
                projected = measured_temp_c + dtemp_dt_smooth * cfg.overshoot_lookahead_s
                if projected >= user_setpoint_c + cfg.overshoot_margin_c:
                    brake = _clamp(cfg.overshoot_brake_strength, 0.0, 1.0)
                    output *= (1.0 - brake)
                    reason = "pid_overshoot_brake"

        # Heating-state latch and minimum on/off times (anti short-cycling)
        output, latch_reason = self._apply_min_on_off(user_setpoint_c, output, now_ts_s)
        if latch_reason:
            reason = latch_reason

        # Compute target as setpoint + delta, then clamp to safe absolute limits
        target = user_setpoint_c + output
        target = _clamp(target, cfg.min_target_c, cfg.max_target_c)

        # Rate limit sending new commands
        rate_limited = False
        if st.last_sent_ts_s is not None and st.last_target_c is not None:
            if (now_ts_s - st.last_sent_ts_s) < cfg.min_command_interval_s:
                # Hold last target (do not change actuator), but still update internal state
                target = st.last_target_c
                output = target - user_setpoint_c
                rate_limited = True
                reason = f"rate_limited({int(cfg.min_command_interval_s)}s)"

        # Persist computed values
        st.last_output_delta_c = output
        st.last_target_c = target

        # Update time/temp tracking
        self._update_time_and_temp(measured_temp_c, now_ts_s)

        # If not rate-limited, consider this a "sent" command.
        if not rate_limited:
            st.last_sent_ts_s = now_ts_s

        return RegulationDecision(
            target_c=target,
            output_delta_c=output,
            p_c=p_c,
            i_c=st.integral_term_c,
            d_c=d_c,
            error_c=error_c,
            reason=reason,
            rate_limited=rate_limited,
            deadband_active=deadband_active,
            heating_on=st.heating_on,
            dtemp_dt_c_per_s=dtemp_dt_smooth,
        )

    def _update_time_and_temp(self, measured_temp_c: float, now_ts_s: float) -> None:
        self._st.last_temp_c = measured_temp_c
        self._st.last_ts_s = now_ts_s

    def _set_heating_state(self, heating_on: bool, now_ts_s: float, *, reason: str) -> None:
        st = self._st
        if st.heating_on != heating_on:
            st.heating_on = heating_on
            st.heating_state_change_ts_s = now_ts_s
            _LOGGER.debug("Heating state -> %s (%s)", heating_on, reason)

    def _apply_min_on_off(self, user_setpoint_c: float, output_delta_c: float, now_ts_s: float) -> tuple[float, Optional[str]]:
        """Apply heating latch and min on/off constraints.

        We interpret "heating on" as output_delta >= heat_on_threshold.
        We interpret "heating off" as output_delta <= heat_off_threshold.
        """
        cfg = self._cfg
        st = self._st

        # Initialize state change timestamp if missing
        if st.heating_state_change_ts_s is None:
            st.heating_state_change_ts_s = now_ts_s

        # Determine desired state from output (with hysteresis thresholds)
        desired_on = st.heating_on
        if st.heating_on:
            if output_delta_c <= cfg.heat_off_threshold_delta_c:
                desired_on = False
        else:
            if output_delta_c >= cfg.heat_on_threshold_delta_c:
                desired_on = True

        # Enforce min on/off times
        elapsed = now_ts_s - (st.heating_state_change_ts_s or now_ts_s)

        if st.heating_on:
            # Currently on: if we want to turn off too early, keep a small heat output
            if not desired_on and elapsed < cfg.min_on_s:
                # Keep at least off-threshold + tiny epsilon
                hold = cfg.heat_off_threshold_delta_c
                return max(output_delta_c, hold), "min_on_hold"
        else:
            # Currently off: if we want to turn on too early, keep output at 0
            if desired_on and elapsed < cfg.min_off_s:
                return min(output_delta_c, 0.0), "min_off_hold"

        # If allowed to change, apply desired state change and return output
        if desired_on != st.heating_on:
            self._set_heating_state(desired_on, now_ts_s, reason="threshold_cross")

        return output_delta_c, None