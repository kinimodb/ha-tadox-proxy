"""Constants for the Tado X Proxy integration."""

DOMAIN = "tadox_proxy"

CONF_SOURCE_ENTITY_ID = "source_entity_id"
CONF_NAME = "name"

# Optional/Configurable external inputs (selected via Options Flow)
CONF_EXTERNAL_TEMPERATURE_ENTITY_ID = "external_temperature_entity_id"
CONF_EXTERNAL_HUMIDITY_ENTITY_ID = "external_humidity_entity_id"

# Window handling (sensor-based)
CONF_WINDOW_OPEN_ENABLED = "window_open_enabled"
CONF_WINDOW_SENSOR_ENTITY_ID = "window_sensor_entity_id"
CONF_WINDOW_OPEN_DELAY_MIN = "window_open_delay_min"
CONF_WINDOW_CLOSE_DELAY_MIN = "window_close_delay_min"

# Reserved for future use (presence/home/away)
CONF_PRESENCE_SENSOR_ENTITY_ID = "presence_sensor_entity_id"
