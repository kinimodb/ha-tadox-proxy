"""Central parameter defaults for tadox_proxy.

Goal:
- Provide ONE single source of truth for all tuning knobs and shared constants.
- Other modules (regulation.py, climate.py, etc.) should import from here instead
  of defining their own defaults.

During the test phase, we tune values here. Later, OptionsFlow can override these
defaults, but the defaults should still live centrally in this module.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Proxy / integration behavior defaults
# ---------------------------------------------------------------------------

# How often the proxy runs a regulation cycle (timer in climate.py).
DEFAULT_CONTROL_INTERVAL_S: int = 300  # 5 min

# When proxy HVAC mode is OFF, we command a frost-safe setpoint to the source.
FROST_PROTECT_C: float = 5.0

# Epsilon used for derived booleans like "will Tado heat?"
# (command_target > tado_internal_temp + epsilon)
WILL_HEAT_EPS_C: float = 0.05

# Rate-limit nuance: even if we rate-limit updates, we allow prompt DECREASES
# (to stop heating) if we are decreasing by more than this epsilon.
RATE_LIMIT_DECREASE_EPS_C: float = 0.05


# ---------------------------------------------------------------------------
# Tado X actuator mapping (Tado-specific "make it actually heat" strategy)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TadoXMappingConfig:
    """Tado X specific mapping strategy.

    Context:
    - We regulate based on ROOM temperature (external sensor preferred).
    - The Tado TRV decides whether to open based on its INTERNAL temperature
      (often higher than room temperature).
    - Therefore, a "heating request" must typically command a setpoint ABOVE the
      internal temperature by a margin, otherwise Tado may not open even if the room
      is too cold.

    This config defines margins used when mapping a computed target to an actuator
    command temperature.
    """

    enabled: bool = True

    # If heating is requested, ensure command_target >= (tado_internal + open_margin_c).
    open_margin_c: float = 0.30

    # If heating is NOT requested, optionally ensure command_target <= (tado_internal - close_margin_c)
    # to help the valve close quickly. Keep small to avoid needless deep setbacks.
    close_margin_c: float = 0.10

    enforce_open_on_request: bool = True
    enforce_close_on_no_request: bool = False

    # Additional safety clamp for "open" mapping (never exceed this even if internal is high).
    # Keep aligned with RegulationConfig.max_target_c unless you have a reason to lower it.
    max_open_target_c: float = 25.0


# ---------------------------------------------------------------------------
# PID tuning + regulation safety rails
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PidTuning:
    """PID tuning parameters.

    Units (important for understanding):
      - error is in °C
      - P/I/D terms are expressed as °C offsets (delta on top of proxy setpoint)
      - kp is dimensionless:          P = kp * error          -> °C
      - ki is 1/second:               I += ki * error * dt    -> °C
      - kd is seconds:                D = -kd * dT/dt         -> °C
        (we use derivative on measurement to avoid derivative kick)
    """

    kp: float = 1.20
    ki: float = 0.010
    kd: float = 8.0


@dataclass(frozen=True)
class RegulationConfig:
    """Regulation parameters and safety rails (defaults).

    The regulator computes:
      target_c = clamp(proxy_setpoint_c + output_delta_c, min_target_c, max_target_c)

    where output_delta_c is PID output (P + I + D) plus additional protective logic.

    Note:
    - Tado X mapping (relative to internal TRV temperature) is defined by `tadox_mapping`
      but applied in the HA glue layer (climate.py) for now.
    """

    tuning: PidTuning = PidTuning()
    tadox_mapping: TadoXMappingConfig = TadoXMappingConfig()

    # If abs(error) <= deadband_c, output is driven to ~0 (with mild integral decay).
    deadband_c: float = 0.10

    # Output is a delta added to the user setpoint (°C). Clamp prevents insane values.
    max_delta_c: float = 5.0

    # Absolute actuator (target) limits sent to underlying climate.
    min_target_c: float = 5.0
    max_target_c: float = 25.0

    # Rate limit: do not send new targets more often than this (seconds).
    # (Decreases may bypass this; see RATE_LIMIT_DECREASE_EPS_C.)
    min_command_interval_s: float = 300.0  # 5 min

    # Anti short-cycling: once heating is considered "on", keep it for min_on_s, and
    # once "off", keep it for min_off_s (seconds).
    min_on_s: float = 300.0   # 5 min
    min_off_s: float = 300.0  # 5 min

    # Heating state thresholds on the PID output (delta above setpoint).
    # Use hysteresis to avoid chatter.
    heat_on_threshold_delta_c: float = 0.20
    heat_off_threshold_delta_c: float = 0.05

    # Anti-windup rail: clamp on the integral TERM itself (in °C).
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
