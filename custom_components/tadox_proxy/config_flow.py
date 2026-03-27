"""Config and Options flows for Tado X Proxy."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .const import (
    CONF_BOOST_DURATION,
    CONF_EXTERNAL_TEMPERATURE_ENTITY_ID,
    CONF_GAIN_FINE_MULTIPLIER,
    CONF_GAIN_SCHEDULING,
    CONF_GAIN_STARTUP_MULTIPLIER,
    CONF_INTEGRAL_DEADBAND_C,
    CONF_MIN_CHANGE_THRESHOLD_C,
    CONF_MIN_COMMAND_INTERVAL_S,
    CONF_NAME,
    CONF_OVERLAY_REFRESH_S,
    CONF_PRESENCE_AWAY_DELAY_S,
    CONF_PRESENCE_HOME_DELAY_S,
    CONF_PRESENCE_SENSOR_ID,
    CONF_SOURCE_ENTITY_ID,
    CONF_WINDOW_CLOSE_DELAY_S,
    CONF_WINDOW_DELAY_S,
    CONF_WINDOW_SENSOR_ID,
    DOMAIN,
)
from .parameters import RegulationConfig


class TadoxProxyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """UI setup for the integration (initial setup)."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}

        if user_input is not None:
            source_entity_id = user_input[CONF_SOURCE_ENTITY_ID]
            ext_temp_entity_id = user_input[CONF_EXTERNAL_TEMPERATURE_ENTITY_ID]
            name = (user_input.get(CONF_NAME) or "").strip() or "Tado X Proxy"

            # EntitySelector already validates entity existence on the frontend.
            # Redundant backend registry checks caused false negatives in the
            # HA Companion App where a WebView bug can delay selector init.
            await self.async_set_unique_id(source_entity_id)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=name,
                data={
                    CONF_SOURCE_ENTITY_ID: source_entity_id,
                    CONF_NAME: name,
                    CONF_EXTERNAL_TEMPERATURE_ENTITY_ID: ext_temp_entity_id,
                },
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_SOURCE_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="climate")
                ),
                vol.Required(CONF_EXTERNAL_TEMPERATURE_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
                ),
                vol.Required(CONF_NAME, default="Tado X Proxy"): str,
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return TadoxProxyOptionsFlow()


class TadoxProxyOptionsFlow(config_entries.OptionsFlow):
    """Per-entry options (gear icon) for tuning, presets & sensor selection."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            # Flatten section data (sections return nested dicts) into
            # a single flat dict for storage in config_entry.options.
            flat: dict = {}
            for key, value in user_input.items():
                if isinstance(value, dict):
                    flat.update(value)
                else:
                    flat[key] = value

            # Strip empty optional sensor values so they're stored as absent
            cleaned = {k: v for k, v in flat.items() if v not in (None, "")}
            # Preserve existing options not shown in this form (e.g. preset
            # temperatures set via Number entities, follow_tado_input flag).
            merged = dict(self.config_entry.options)
            # Remove optional sensor keys that were cleared by the user
            for key in (CONF_WINDOW_SENSOR_ID, CONF_PRESENCE_SENSOR_ID):
                if key not in cleaned:
                    merged.pop(key, None)
            merged.update(cleaned)
            # Reload is triggered by the update_listener in __init__.py
            # AFTER HA has persisted the new options, avoiding stale data.
            return self.async_create_entry(title="", data=merged)

        # Load current values (options > data > defaults)
        opts = self.config_entry.options
        data = self.config_entry.data
        defaults = RegulationConfig()

        current_ext_temp = opts.get(
            CONF_EXTERNAL_TEMPERATURE_ENTITY_ID,
            data.get(CONF_EXTERNAL_TEMPERATURE_ENTITY_ID),
        )

        # --- Build window sensor section schema ---
        window_schema_dict: dict = {
            vol.Optional(CONF_WINDOW_SENSOR_ID): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor")
            ),
            vol.Required(
                CONF_WINDOW_DELAY_S,
                default=opts.get(CONF_WINDOW_DELAY_S, 30),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=3600, step=1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            ),
            vol.Required(
                CONF_WINDOW_CLOSE_DELAY_S,
                default=opts.get(CONF_WINDOW_CLOSE_DELAY_S, 120),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=600, step=1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            ),
        }
        window_section_schema = vol.Schema(window_schema_dict)

        # Inject suggested value for optional entity selector only if set
        window_sensor = opts.get(CONF_WINDOW_SENSOR_ID)
        if window_sensor:
            window_section_schema = self.add_suggested_values_to_schema(
                window_section_schema, {CONF_WINDOW_SENSOR_ID: window_sensor}
            )

        # --- Build presence sensor section schema ---
        presence_schema_dict: dict = {
            vol.Optional(CONF_PRESENCE_SENSOR_ID): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor")
            ),
            vol.Required(
                CONF_PRESENCE_AWAY_DELAY_S,
                default=opts.get(CONF_PRESENCE_AWAY_DELAY_S, 600),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=7200, step=1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            ),
            vol.Required(
                CONF_PRESENCE_HOME_DELAY_S,
                default=opts.get(CONF_PRESENCE_HOME_DELAY_S, 30),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=600, step=1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            ),
        }
        presence_section_schema = vol.Schema(presence_schema_dict)

        presence_sensor = opts.get(CONF_PRESENCE_SENSOR_ID)
        if presence_sensor:
            presence_section_schema = self.add_suggested_values_to_schema(
                presence_section_schema, {CONF_PRESENCE_SENSOR_ID: presence_sensor}
            )

        # --- Main options schema with sections ---
        options_schema = vol.Schema(
            {
                # Top-level: external sensor + boost duration
                vol.Required(
                    CONF_EXTERNAL_TEMPERATURE_ENTITY_ID,
                    default=current_ext_temp,
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
                ),
                vol.Required(
                    CONF_BOOST_DURATION,
                    default=opts.get(CONF_BOOST_DURATION, defaults.presets.boost_duration_min),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=120, step=1,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="min",
                    )
                ),

                # Section: Window sensor
                vol.Required("window_sensor"): section(
                    window_section_schema,
                    {"collapsed": True},
                ),

                # Section: Presence sensor
                vol.Required("presence_sensor"): section(
                    presence_section_schema,
                    {"collapsed": True},
                ),

                # Section: PI Controller
                vol.Required("pi_controller"): section(
                    vol.Schema(
                        {
                            vol.Required(
                                "correction_kp",
                                default=opts.get("correction_kp", defaults.tuning.kp),
                            ): selector.NumberSelector(
                                selector.NumberSelectorConfig(
                                    min=0.0, max=5.0, step=0.1,
                                    mode=selector.NumberSelectorMode.BOX,
                                )
                            ),
                            vol.Required(
                                "correction_ki",
                                default=opts.get("correction_ki", defaults.tuning.ki),
                            ): selector.NumberSelector(
                                selector.NumberSelectorConfig(
                                    min=0.0, max=0.1, step=0.001,
                                    mode=selector.NumberSelectorMode.BOX,
                                )
                            ),
                            vol.Required(
                                CONF_INTEGRAL_DEADBAND_C,
                                default=opts.get(CONF_INTEGRAL_DEADBAND_C, defaults.integral_deadband_c),
                            ): selector.NumberSelector(
                                selector.NumberSelectorConfig(
                                    min=0.1, max=1.0, step=0.1,
                                    mode=selector.NumberSelectorMode.BOX,
                                    unit_of_measurement="°C",
                                )
                            ),
                        }
                    ),
                    {"collapsed": True},
                ),

                # Section: Gain Scheduling
                vol.Required("gain_scheduling"): section(
                    vol.Schema(
                        {
                            vol.Required(
                                CONF_GAIN_SCHEDULING,
                                default=opts.get(CONF_GAIN_SCHEDULING, defaults.gain_scheduling_enabled),
                            ): selector.BooleanSelector(),
                            vol.Required(
                                CONF_GAIN_FINE_MULTIPLIER,
                                default=opts.get(CONF_GAIN_FINE_MULTIPLIER, defaults.gain_fine_multiplier),
                            ): selector.NumberSelector(
                                selector.NumberSelectorConfig(
                                    min=0.3, max=1.5, step=0.1,
                                    mode=selector.NumberSelectorMode.BOX,
                                )
                            ),
                            vol.Required(
                                CONF_GAIN_STARTUP_MULTIPLIER,
                                default=opts.get(CONF_GAIN_STARTUP_MULTIPLIER, defaults.gain_startup_multiplier),
                            ): selector.NumberSelector(
                                selector.NumberSelectorConfig(
                                    min=1.0, max=3.0, step=0.1,
                                    mode=selector.NumberSelectorMode.BOX,
                                )
                            ),
                        }
                    ),
                    {"collapsed": True},
                ),

                # Section: TRV Communication
                vol.Required("trv_communication"): section(
                    vol.Schema(
                        {
                            vol.Required(
                                CONF_MIN_COMMAND_INTERVAL_S,
                                default=opts.get(CONF_MIN_COMMAND_INTERVAL_S, defaults.min_command_interval_s),
                            ): selector.NumberSelector(
                                selector.NumberSelectorConfig(
                                    min=60, max=600, step=30,
                                    mode=selector.NumberSelectorMode.BOX,
                                    unit_of_measurement="s",
                                )
                            ),
                            vol.Required(
                                CONF_MIN_CHANGE_THRESHOLD_C,
                                default=opts.get(CONF_MIN_CHANGE_THRESHOLD_C, defaults.min_change_threshold_c),
                            ): selector.NumberSelector(
                                selector.NumberSelectorConfig(
                                    min=0.1, max=1.0, step=0.1,
                                    mode=selector.NumberSelectorMode.BOX,
                                    unit_of_measurement="°C",
                                )
                            ),
                            vol.Required(
                                CONF_OVERLAY_REFRESH_S,
                                default=opts.get(CONF_OVERLAY_REFRESH_S, 0),
                            ): selector.NumberSelector(
                                selector.NumberSelectorConfig(
                                    min=0, max=3600, step=60,
                                    mode=selector.NumberSelectorMode.BOX,
                                    unit_of_measurement="s",
                                )
                            ),
                        }
                    ),
                    {"collapsed": True},
                ),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
        )
