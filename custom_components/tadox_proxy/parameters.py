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
# This calculates the PID internal state.
DEFAULT_CONTROL_INTERVAL_S: int = 60  # 1 min

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
    - The Tado TRV decides whether to open based on its INTERNAL temperature.
    
    CHANGE (v0.2): Disabled by default to prevent 'sawtooth' oscillations.
    We now rely on a stronger PID (higher Kp) to overcome the internal offset naturally.
    """

    enabled: bool = False

    # If heating is requested, ensure command_target >= (tado_internal + open_margin_c).
    open_margin_c: float = 0.30

    # If heating is NOT requested, optionally ensure command_target <= (tado_internal - close_margin_c)
    close_margin_c: float = 0.10

    enforce_open_on_request: bool = True
    enforce_close_on_no_request: bool = False

    # Additional safety clamp for "open" mapping.
    max_open_target_c: float = 25.0


# ---------------------------------------------------------------------------
# PID tuning + regulation safety rails
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PidTuning:
    """PID tuning parameters.

    Units:
      - error is in °C
      - P/I/D terms are expressed as °C offsets (delta on top of proxy setpoint)
    """

    # Increased from 1.2 to 3.0 to overcome Tado internal heat offset without 'mapping'.
    # Example: Room 19, Target 20 -> Error 1. Output +3 -> Send 23.
    # If Tado internal is 22, it sees +1 diff and opens.
    kp: float = 7.0
    
    # Low integral to avoid windup during long heat-up phases.
    ki: float = 0.005
    
    # Increased massively from 8.0 to 600.0 to strictly brake when temp rises.
    # 600s = 10 minutes time constant.
    kd: float = 600.0


@dataclass(frozen=True)
class RegulationConfig:
    """Regulation parameters and safety rails (defaults)."""

    tuning: PidTuning = PidTuning()
    tadox_mapping: TadoXMappingConfig = TadoXMappingConfig()

    # If abs(error) <= deadband_c, output is driven to ~0.
    deadband_c: float = 0.20

    # Output is a delta added to the user setpoint (°C).
    # Increased max_delta to allow Kp=3.0 to work effectively (max boost 4°C).
    max_delta_c: float = 4.0

    # Absolute actuator (target) limits sent to underlying climate.
    min_target_c: float = 5.0
    max_target_c: float = 25.0

    # Rate limit: do not send new targets more often than this (seconds).
    # Increased to 5 min to save Tado battery.
    min_command_interval_s: float = 300.0

    # Anti short-cycling settings
    min_on_s: float = 300.0   # 5 min
    min_off_s: float = 300.0  # 5 min

    # Heating state thresholds on the PID output
    heat_on_threshold_delta_c: float = 0.20
    heat_off_threshold_delta_c: float = 0.05

    # Anti-windup rail: clamp on the integral TERM itself (in °C).
    integral_term_min_c: float = -2.0
    integral_term_max_c: float = 2.0

    # Derivative smoothing (EMA). 0.1-0.2 is good for slow sensors.
    derivative_ema_alpha: float = 0.20

    # Trend-based overshoot protection
    overshoot_lookahead_s: float = 300.0
    overshoot_margin_c: float = 0.10
    overshoot_brake_strength: float = 0.80
