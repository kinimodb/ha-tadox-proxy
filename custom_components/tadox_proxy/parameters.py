"""Central parameter defaults for tadox_proxy."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Behavioural thresholds (climate-entity logic, independent of the PI engine)
# ---------------------------------------------------------------------------

@dataclass
class BehaviourConfig:
    """Thresholds for the follow-Tado and send-decision logic.

    These values can be overridden via config-entry options so operators can
    tune the integration's responsiveness without touching source code.
    """

    # Follow-Tado: min divergence from last-sent setpoint to treat as a
    # physical user change (°C).
    follow_threshold_c: float = 0.5

    # Follow-Tado: grace period (s) after our last command during which we
    # ignore Tado setpoint changes (Tado may still be acknowledging via
    # Thread/cloud).
    follow_grace_s: float = 20.0

    # Rate-limit bypass: immediately send if the new target is this many °C
    # below the current Tado setpoint (urgent cool-down).
    urgent_decrease_threshold_c: float = 1.0


# ---------------------------------------------------------------------------
# Integration behaviour defaults
# ---------------------------------------------------------------------------

DEFAULT_CONTROL_INTERVAL_S: int = 60   # seconds between regulation cycles
FROST_PROTECT_C: float = 5.0           # target temperature when HVAC is OFF
DEFAULT_SENSOR_GRACE_S: int = 300      # seconds to use last-valid room_temp when sensor is unavailable

# Window / presence automation delays (s), overridable via options
DEFAULT_WINDOW_DELAY_S: int = 30            # window open → frost protection
DEFAULT_WINDOW_CLOSE_DELAY_S: int = 120     # window closed → restore previous preset
DEFAULT_PRESENCE_AWAY_DELAY_S: int = 600    # nobody home → AWAY
DEFAULT_PRESENCE_HOME_DELAY_S: int = 30     # somebody home → restore previous preset
DEFAULT_MANUAL_MODE_DURATION_MIN: int = 15  # how long Manual lasts with the timer option

# Periodic resend of an unchanged setpoint for cloud integrations with
# timer-based overlays (s).  0 = disabled (correct for Matter/Thread).
DEFAULT_OVERLAY_REFRESH_S: int = 0

# Needs-heat sensor (a boiler that runs on demand), overridable via options:
# on once error_c rises above the first value, off once it falls to the
# second one or below (°C).
DEFAULT_NEEDS_HEAT_ON_C: float = 0.3
DEFAULT_NEEDS_HEAT_OFF_C: float = 0.0


# ---------------------------------------------------------------------------
# Correction tuning  (PI layer on top of feedforward)
# ---------------------------------------------------------------------------

@dataclass
class CorrectionTuning:
    """PI correction parameters applied on top of the feedforward offset.

    These are intentionally *small* – the feedforward does the heavy lifting.
    """

    kp: float = 0.8    # proportional gain for residual room-error correction
    # Integral gain for slow steady-state error correction.  The integral time
    # (1 + kp) / ki is 30 min: for a gap that stays, the integral needs that
    # long to add as much push as the gap itself already gives, and rooms
    # respond in hours (#124).  Entries saved at the previous default 0.003
    # follow this one after the migration below.
    ki: float = 0.001


# The Ki default up to v1.5.0.  The options dialog stores every field it shows,
# so an entry that was saved once holds this value without anyone having chosen
# it.  Such an entry has to follow the new default, or the change would not
# reach it (config-entry minor version 2, async_migrate_entry in __init__.py).
PREVIOUS_KI_DEFAULT: float = 0.003
_KI_TOLERANCE: float = 1e-9   # a stored float is never compared for exact equality


def options_without_previous_ki_default(
    options: Mapping[str, Any], ki_key: str
) -> dict[str, Any]:
    """Return the options of an entry saved before the Ki default changed.

    A Ki stored at the previous default (0.003) is dropped, so the entry
    follows the new default.  Every other value was a choice and stays, as
    does every other option.  *ki_key* is the option's name (const.py needs
    Home Assistant, this module must not).  The input is not changed.
    """
    migrated = dict(options)
    stored = migrated.get(ki_key)
    if isinstance(stored, (int, float)) and abs(stored - PREVIOUS_KI_DEFAULT) <= _KI_TOLERANCE:
        del migrated[ki_key]
    return migrated


# ---------------------------------------------------------------------------
# Preset defaults
# ---------------------------------------------------------------------------

@dataclass
class PresetConfig:
    """Temperature settings for each preset mode."""

    comfort_target_c: float = 20.0   # default comfort temperature
    eco_target_c: float = 17.0       # fixed eco temperature (independent of comfort)
    boost_target_c: float = 25.0     # fixed target during boost
    boost_duration_min: int = 30     # auto-revert to comfort after this many minutes
    away_target_c: float = 17.0      # fixed target when away
    frost_protection_target_c: float = 7.0   # frost protection temperature


# ---------------------------------------------------------------------------
# Full regulation config with safety rails
# ---------------------------------------------------------------------------

@dataclass
class RegulationConfig:
    """All regulation parameters and safety limits."""

    tuning: CorrectionTuning = field(default_factory=CorrectionTuning)
    presets: PresetConfig = field(default_factory=PresetConfig)

    # Absolute temperature limits for commands sent to Tado
    min_target_c: float = 5.0
    max_target_c: float = 30.0

    # Anti-windup limits for the integral correction term.  They are not
    # symmetric.  A shut valve cannot be shut further: below about -0.5 the
    # integral only delays the next heating, because it has to unwind before
    # the valve opens again (#124).  The upper limit stays wide: a TRV that
    # opens in proportion to the gap needs a lasting positive integral to
    # reach the target.
    integral_min_c: float = -0.5
    integral_max_c: float = 2.0

    # Integral deadband: only accumulate integral when |error| < this value.
    # Outside this zone the integral decays, preventing buildup during gross
    # heating/cooling transients that would cause overshoot.
    integral_deadband_c: float = 0.3

    # Decay factor applied to the integral each cycle when |error| >= deadband.
    # 0.95 means ~5% reduction per 60s cycle → drains in ~15 min.
    integral_decay: float = 0.95

    # Upper bound for the time step used by the integral (s).  Cycles skipped
    # during a sensor/coordinator outage would otherwise make the next dt
    # cover the whole gap and push the integral to its clamp in one step.
    max_integration_dt_s: float = 2 * DEFAULT_CONTROL_INTERVAL_S

    # Rate limiting: minimum seconds between commands to Tado (battery saving)
    min_command_interval_s: float = 180.0

    # Minimum difference to current Tado setpoint before sending a new command
    min_change_threshold_c: float = 0.3

    # Adaptive gain scheduling: scale Kp based on error magnitude
    gain_scheduling_enabled: bool = True
    gain_startup_multiplier: float = 1.5   # Kp multiplier when |error| > startup_threshold
    gain_fine_multiplier: float = 1.0      # Kp multiplier when |error| < fine_threshold
    gain_startup_threshold_c: float = 2.0  # error threshold for startup (aggressive) zone
    gain_fine_threshold_c: float = 0.5     # error threshold for fine (gentle) zone
