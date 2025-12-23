"""PID control core for tadox_proxy.

This module is intentionally HA-agnostic (no Home Assistant imports) so it can be
unit-tested easily and reused across entities/coordinators.

Design goals (from project notes):
- True PID (P + I + D) with derivative term to reduce overshoot
- Derivative on measurement (avoids derivative kick on setpoint changes)
- Deadband (small corridor around setpoint where we do not regulate)
- Anti-windup for the integrator when output saturates
- Optional minimum on/off timing + rate limiting helpers (actuator-friendly)

Output convention:
- controller.compute(...) returns a normalized heat demand in [0.0 .. 1.0]
  where 0.0 ~ "off" and 1.0 ~ "full heat".

The translation from demand -> "target temperature to send to Tado" is handled
elsewhere (entity/adapter layer).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


def clamp(value: float, limits: Tuple[float, float]) -> float:
    """Clamp a float to the given (min, max) limits."""
    lo, hi = limits
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


@dataclass
class PIDConfig:
    """PID configuration parameters.

    kp, ki, kd:
      - kp: proportional gain
      - ki: integral gain in 1/s (applied as ki * error * dt)
      - kd: derivative gain in s (applied as kd * d(measurement)/dt)

    deadband:
      Within +/- deadband around setpoint, error is treated as 0 (no regulation).
      This reduces actuator chatter around the setpoint.

    output_limits:
      Normalized demand range, default (0..1).

    integral_limits:
      Clamp for integral state to avoid runaway integral, in "demand units".
      (This is independent from output_limits to allow tighter control if desired.)

    d_filter_alpha:
      Optional low-pass smoothing for derivative term (0..1).
      - 0.0 => no smoothing (raw derivative)
      - closer to 1.0 => heavier smoothing
    """
    kp: float = 0.8
    ki: float = 0.0008
    kd: float = 90.0
    deadband: float = 0.10

    output_limits: Tuple[float, float] = (0.0, 1.0)
    integral_limits: Tuple[float, float] = (-0.5, 0.5)

    d_filter_alpha: float = 0.6


class PIDController:
    """Stateful PID controller.

    Notes:
    - Uses derivative on measurement: d = -kd * d(measurement)/dt
      This dampens overshoot and avoids derivative kick on setpoint steps.
    - Anti-windup: if output saturates, integral is only updated if it would
      move the output back toward the unsaturated region.
    """

    def __init__(self, config: PIDConfig) -> None:
        self._cfg = config

        self._integral: float = 0.0
        self._prev_measurement: Optional[float] = None
        self._prev_time_s: Optional[float] = None

        self._d_filt: float = 0.0  # filtered derivative (measurement rate)

    @property
    def config(self) -> PIDConfig:
        return self._cfg

    def reset(self) -> None:
        """Reset controller state (integral and derivative history)."""
        self._integral = 0.0
        self._prev_measurement = None
        self._prev_time_s = None
        self._d_filt = 0.0

    def compute(
        self,
        *,
        now_s: float,
        setpoint: float,
        measurement: float,
    ) -> float:
        """Compute normalized demand in [output_limits] for current time.

        Args:
            now_s: monotonic-ish timestamp in seconds
            setpoint: desired temperature (°C)
            measurement: measured temperature (°C)

        Returns:
            demand (float): normalized heat demand in [output_limits]
        """
        # Initialize on first run
        if self._prev_time_s is None or self._prev_measurement is None:
            self._prev_time_s = now_s
            self._prev_measurement = measurement
            self._integral = 0.0
            self._d_filt = 0.0
            return 0.0

        dt = now_s - self._prev_time_s
        if dt <= 0.0:
            # Non-advancing time: return last computable safe output
            dt = 1e-6

        # Apply deadband
        error = setpoint - measurement
        if abs(error) <= self._cfg.deadband:
            error = 0.0

        # Derivative on measurement (negative sign for damping)
        d_meas = (measurement - self._prev_measurement) / dt  # °C/s
        # Low-pass filter derivative to reduce noise
        alpha = clamp(self._cfg.d_filter_alpha, (0.0, 0.999))
        self._d_filt = alpha * self._d_filt + (1.0 - alpha) * d_meas

        p = self._cfg.kp * error
        d = -self._cfg.kd * self._d_filt

        # Propose integral update
        i_candidate = self._integral + (self._cfg.ki * error * dt)
        i_candidate = clamp(i_candidate, self._cfg.integral_limits)

        # Unsaturated output with candidate integral
        u_unsat = p + i_candidate + d
        u_sat = clamp(u_unsat, self._cfg.output_limits)

        # Anti-windup:
        # - If saturating high, only allow integral to decrease u (error <= 0)
        # - If saturating low, only allow integral to increase u (error >= 0)
        # - If not saturating, accept integral
        if u_sat != u_unsat:
            lo, hi = self._cfg.output_limits
            if u_sat >= hi and error > 0:
                # would push further into saturation -> freeze integral
                pass
            elif u_sat <= lo and error < 0:
                # would push further into saturation -> freeze integral
                pass
            else:
                self._integral = i_candidate
        else:
            self._integral = i_candidate

        # Final output with accepted integral
        u = clamp(p + self._integral + d, self._cfg.output_limits)

        # Update history
        self._prev_time_s = now_s
        self._prev_measurement = measurement

        return u

    def diagnostics(
        self,
        *,
        now_s: float,
        setpoint: float,
        measurement: float,
    ) -> dict:
        """Return a diagnostics snapshot (non-authoritative; for HA attributes/logging)."""
        if self._prev_time_s is None or self._prev_measurement is None:
            dt = None
            d_meas = None
        else:
            dt = max(now_s - self._prev_time_s, 1e-6)
            d_meas = (measurement - self._prev_measurement) / dt

        error = setpoint - measurement
        if abs(error) <= self._cfg.deadband:
            error_db = 0.0
        else:
            error_db = error

        p = self._cfg.kp * error_db
        d = None if d_meas is None else -self._cfg.kd * self._d_filt
        u = None if d is None else clamp(p + self._integral + d, self._cfg.output_limits)

        return {
            "setpoint": setpoint,
            "measurement": measurement,
            "error": error,
            "error_deadbanded": error_db,
            "integral": self._integral,
            "d_meas_raw": d_meas,
            "d_meas_filt": self._d_filt,
            "p_term": p,
            "d_term": d,
            "output": u,
            "deadband": self._cfg.deadband,
            "kp": self._cfg.kp,
            "ki": self._cfg.ki,
            "kd": self._cfg.kd,
        }


@dataclass
class RateLimiter:
    """Simple rate limiter to avoid excessive actuator commands."""
    min_interval_s: float
    _last_sent_s: Optional[float] = None

    def should_send(self, now_s: float) -> bool:
        if self._last_sent_s is None:
            return True
        return (now_s - self._last_sent_s) >= self.min_interval_s

    def mark_sent(self, now_s: float) -> None:
        self._last_sent_s = now_s


@dataclass
class MinOnOffTimer:
    """Minimum on/off time gating (anti short-cycling).

    This does not decide demand; it constrains transitions between effective
    'on' and 'off' states derived from demand_threshold.
    """
    min_on_s: float = 300.0
    min_off_s: float = 300.0
    demand_threshold: float = 0.05

    _is_on: bool = False
    _last_transition_s: Optional[float] = None

    def effective_is_on(self, *, now_s: float, demand: float) -> bool:
        desired_on = demand >= self.demand_threshold

        if self._last_transition_s is None:
            self._last_transition_s = now_s
            self._is_on = desired_on
            return self._is_on

        elapsed = now_s - self._last_transition_s
        if self._is_on:
            # Currently on: only allow off if min_on_s elapsed
            if not desired_on and elapsed < self.min_on_s:
                return True
            if not desired_on and elapsed >= self.min_on_s:
                self._is_on = False
                self._last_transition_s = now_s
                return False
            return True
        else:
            # Currently off: only allow on if min_off_s elapsed
            if desired_on and elapsed < self.min_off_s:
                return False
            if desired_on and elapsed >= self.min_off_s:
                self._is_on = True
                self._last_transition_s = now_s
                return True
            return False

    def reset(self) -> None:
        self._is_on = False
        self._last_transition_s = None