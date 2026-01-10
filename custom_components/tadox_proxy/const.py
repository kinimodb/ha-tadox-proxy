"""Constants for the Tado X Proxy integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "tadox_proxy"

# Platforms
PLATFORMS: list[Platform] = [Platform.CLIMATE]

# ---------------------------------------------------------------------------
# ConfigEntry keys (data)
# ---------------------------------------------------------------------------

# Historical naming (kept for backwards compatibility)
CONF_SOURCE_ENTITY_ID = "source_entity_id"
CONF_NAME = "name"

# External inputs (selected via Config/Options Flow)
CONF_EXTERNAL_TEMPERATURE_ENTITY_ID = "external_temperature_entity_id"
CONF_EXTERNAL_HUMIDITY_ENTITY_ID = "external_humidity_entity_id"

# Aliases expected by climate.py (map to existing stored keys to avoid breakage)
# - The underlying Tado climate entity to be controlled
CONF_TADO_CLIMATE_ENTITY_ID = CONF_SOURCE_ENTITY_ID
# - The controlled variable (external room temperature sensor)
CONF_ROOM_SENSOR_ENTITY_ID = CONF_EXTERNAL_TEMPERATURE_ENTITY_ID
# - Optional: entity providing Tado’s internal temperature (if configured)
CONF_TADO_TEMP_ENTITY_ID = "tado_temp_entity_id"

# Window handling (sensor-based)
CONF_WINDOW_OPEN_ENABLED = "window_open_enabled"
CONF_WINDOW_SENSOR_ENTITY_ID = "window_sensor_entity_id"
CONF_WINDOW_OPEN_DELAY_MIN = "window_open_delay_min"
CONF_WINDOW_CLOSE_DELAY_MIN = "window_close_delay_min"

# Reserved for future use (presence/home/away)
CONF_PRESENCE_SENSOR_ENTITY_ID = "presence_sensor_entity_id"

# ---------------------------------------------------------------------------
# Telemetry / attribute keys (exposed on the proxy climate entity)
# ---------------------------------------------------------------------------

ATTR_ROOM_TEMPERATURE = "tadox_room_temperature"
ATTR_TADO_TEMPERATURE = "tadox_tado_temperature"
ATTR_TADO_SETPOINT = "tadox_tado_setpoint"

ATTR_HYBRID_STATE = "tadox_hybrid_state"
ATTR_HYBRID_CMD = "tadox_hybrid_cmd"
ATTR_HYBRID_BIAS = "tadox_hybrid_bias"

ATTR_LAST_SENT_SETPOINT = "tadox_last_sent_setpoint"
ATTR_LAST_SENT_TS = "tadox_last_sent_ts"
ATTR_LAST_SENT_CONTEXT_ID = "tadox_last_sent_context_id"
ATTR_LAST_SENT_REASON = "tadox_last_sent_reason"

# Generic command reason (used for debugging / correlation)
ATTR_COMMAND_REASON = "tadox_command_reason"
