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
CONF_AWAY_TARGET = "away_target"
CONF_FROST_PROTECTION_TARGET = "frost_protection_target"

# Optional behaviour flags (stored in options)
CONF_FOLLOW_TADO_INPUT = "follow_tado_input"

# Window sensor (binary_sensor) – optional external trigger
CONF_WINDOW_SENSOR_ID = "window_sensor_id"
CONF_WINDOW_DELAY_S = "window_delay_s"
CONF_WINDOW_CLOSE_DELAY_S = "window_close_delay_s"

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

# Overlay refresh (for cloud-API integrations with timer-based overlays)
CONF_OVERLAY_REFRESH_S = "overlay_refresh_s"


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
