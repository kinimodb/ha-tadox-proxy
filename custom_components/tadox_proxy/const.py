"""Constants for the Tado X Proxy integration."""

from __future__ import annotations

import math
from typing import Any

from homeassistant.components.climate import (
    PRESET_AWAY,
    PRESET_BOOST,
    PRESET_COMFORT,
    PRESET_ECO,
)

DOMAIN = "tadox_proxy"

CONF_SOURCE_ENTITY_ID = "source_entity_id"
CONF_NAME = "name"
CONF_EXTERNAL_TEMPERATURE_ENTITY_ID = "external_temperature_entity_id"

# Preset configuration keys (stored in options)
CONF_COMFORT_TARGET = "comfort_target"
CONF_ECO_TARGET = "eco_target"
CONF_BOOST_TARGET = "boost_target"
CONF_BOOST_DURATION = "boost_duration"
CONF_MANUAL_MODE_ACTION = "manual_mode_action"
CONF_MANUAL_MODE_DURATION_MIN = "manual_mode_duration_min"
CONF_WEEKLY_PRESET_SCHEDULE = "weekly_preset_schedule"
CONF_AWAY_TARGET = "away_target"
CONF_FROST_PROTECTION_TARGET = "frost_protection_target"

# Optional behaviour flags (stored in options)
CONF_FOLLOW_TADO_INPUT = "follow_tado_input"

# Window sensor (binary_sensor) – optional external trigger
CONF_WINDOW_SENSOR_ID = "window_sensor_id"
CONF_WINDOW_DELAY_S = "window_delay_s"
CONF_WINDOW_CLOSE_DELAY_S = "window_close_delay_s"
# What an open window does to the TRV – one of the WINDOW_ACTIONS in
# climate_controllers (frost_protection / off).
CONF_WINDOW_ACTION = "window_action"

# Presence sensor (binary_sensor) – optional external trigger
CONF_PRESENCE_SENSOR_ID = "presence_sensor_id"
CONF_PRESENCE_AWAY_DELAY_S = "presence_away_delay_s"
CONF_PRESENCE_HOME_DELAY_S = "presence_home_delay_s"

# Custom preset name (not a HA built-in)
PRESET_FROST_PROTECTION = "frost_protection"

# Behavioural thresholds (stored in options, override BehaviourConfig defaults)
CONF_FOLLOW_THRESHOLD_C = "follow_threshold_c"
CONF_FOLLOW_GRACE_S = "follow_grace_s"
CONF_URGENT_DECREASE_THRESHOLD_C = "urgent_decrease_threshold_c"

# Sensor resilience
CONF_SENSOR_GRACE_S = "sensor_grace_s"
# What happens once the room sensor's grace period has expired – one of the
# CONTROL_* modes in climate_controllers (hold / backup_sensor / trv).
CONF_SENSOR_LOSS_ACTION = "sensor_loss_action"
CONF_BACKUP_TEMPERATURE_ENTITY_ID = "backup_temperature_entity_id"

MANUAL_MODE_KEEP = "keep"
MANUAL_MODE_TIMER = "timer"
MANUAL_MODE_SCHEDULE = "schedule"
MANUAL_MODE_ACTIONS = (MANUAL_MODE_KEEP, MANUAL_MODE_SCHEDULE, MANUAL_MODE_TIMER)
DEFAULT_MANUAL_MODE_ACTION = MANUAL_MODE_KEEP
DEFAULT_MANUAL_MODE_DURATION_MIN = 15

# Overlay refresh (for cloud-API integrations with timer-based overlays)
CONF_OVERLAY_REFRESH_S = "overlay_refresh_s"

# PI controller tuning keys (stored in options)
CONF_CORRECTION_KP = "correction_kp"
CONF_CORRECTION_KI = "correction_ki"

# Adaptive gain scheduling
CONF_GAIN_SCHEDULING = "gain_scheduling_enabled"
CONF_GAIN_FINE_MULTIPLIER = "gain_fine_multiplier"
CONF_GAIN_STARTUP_MULTIPLIER = "gain_startup_multiplier"
CONF_GAIN_STARTUP_THRESHOLD_C = "gain_startup_threshold_c"
CONF_GAIN_FINE_THRESHOLD_C = "gain_fine_threshold_c"
CONF_MIN_COMMAND_INTERVAL_S = "min_command_interval_s"
CONF_MIN_CHANGE_THRESHOLD_C = "min_change_threshold_c"
CONF_INTEGRAL_DEADBAND_C = "integral_deadband_c"

# Options written at runtime by the number/switch entities.  The climate
# entity applies them live via its update listener; they must NOT trigger a
# full integration reload (that would wipe boost timers, window/presence
# automation state and the PI integral).
LIVE_OPTION_KEYS = frozenset({
    CONF_COMFORT_TARGET,
    CONF_ECO_TARGET,
    CONF_BOOST_TARGET,
    CONF_AWAY_TARGET,
    CONF_FROST_PROTECTION_TARGET,
    CONF_FOLLOW_TADO_INPUT,
})

# Climate state attributes that are also read back on restart (RestoreEntity)
ATTR_GUEST_MODE = "guest_mode"
ATTR_MANUAL_TARGET = "manual_target_c"
ATTR_MANUAL_RETURN_PRESET = "manual_return_preset"
ATTR_MANUAL_RETURN_AT = "manual_return_at_ts"
ATTR_MANUAL_RETURN_TARGET = "manual_return_target_c"
# Preset (and base target) an open window restores when it closes
ATTR_WINDOW_RESTORE_PRESET = "window_restore_preset"
ATTR_WINDOW_RESTORE_TARGET = "window_restore_target_c"
# The window action switched the TRV off and it has not been switched on since
ATTR_WINDOW_TRV_OFF = "window_trv_off"


# ---------------------------------------------------------------------------
# Shared utilities (used by climate.py, climate_presets.py, climate_regulation.py)
# ---------------------------------------------------------------------------

def safe_float(value: Any) -> float | None:
    """Convert value to float, returning None for non-finite or unparseable values."""
    if value is None:
        return None
    try:
        f = float(value)
    except (ValueError, TypeError):
        return None
    return f if math.isfinite(f) else None


# Ordered list of presets shown in the UI.
# PRESET_NONE ("Manuell") activates when the user moves the temperature
# slider directly instead of selecting a named preset.
PRESET_LIST = [
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_BOOST,
    PRESET_AWAY,
    PRESET_FROST_PROTECTION,
]
