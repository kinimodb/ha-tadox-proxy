"""Constants for the Tado X Proxy integration."""

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
CONF_VACATION_TARGET = "vacation_target"

# Optional behaviour flags (stored in options)
CONF_FOLLOW_TADO_INPUT = "follow_tado_input"

# Window sensor (binary_sensor) – optional external trigger
CONF_WINDOW_SENSOR_ID = "window_sensor_id"
CONF_WINDOW_DELAY_S = "window_delay_s"

# Presence sensor (binary_sensor) – optional external trigger
CONF_PRESENCE_SENSOR_ID = "presence_sensor_id"
CONF_PRESENCE_AWAY_DELAY_S = "presence_away_delay_s"

# Custom preset name (not a HA built-in)
PRESET_VACATION = "vacation"
