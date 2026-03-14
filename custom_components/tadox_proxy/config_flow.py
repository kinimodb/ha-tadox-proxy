"""Config and Options flows for Tado X Proxy."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    CONF_SOURCE_ENTITY_ID,
    CONF_NAME,
    CONF_EXTERNAL_TEMPERATURE_ENTITY_ID,
    CONF_BOOST_DURATION,
    CONF_WINDOW_SENSOR_ID,
    CONF_WINDOW_DELAY_S,
    CONF_WINDOW_CLOSE_DELAY_S,
    CONF_PRESENCE_SENSOR_ID,
    CONF_PRESENCE_AWAY_DELAY_S,
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
            # Strip empty optional sensor values so they're stored as absent
            cleaned = {k: v for k, v in user_input.items() if v not in (None, "")}
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

        options_schema = vol.Schema(
            {
                # --- Correction tuning ---
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

                # --- Boost duration ---
                vol.Required(
                    CONF_BOOST_DURATION,
                    default=opts.get(CONF_BOOST_DURATION, defaults.presets.boost_duration_min),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=120)),

                # --- External sensor ---
                vol.Required(
                    CONF_EXTERNAL_TEMPERATURE_ENTITY_ID,
                    default=current_ext_temp,
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class="temperature")
                ),

                # --- Window sensor (optional) ---
                vol.Optional(CONF_WINDOW_SENSOR_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="binary_sensor")
                ),
                vol.Required(
                    CONF_WINDOW_DELAY_S,
                    default=opts.get(CONF_WINDOW_DELAY_S, 30),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=3600)),
                vol.Required(
                    CONF_WINDOW_CLOSE_DELAY_S,
                    default=opts.get(CONF_WINDOW_CLOSE_DELAY_S, 120),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=600)),

                # --- Presence sensor (optional) ---
                vol.Optional(CONF_PRESENCE_SENSOR_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="binary_sensor")
                ),
                vol.Required(
                    CONF_PRESENCE_AWAY_DELAY_S,
                    default=opts.get(CONF_PRESENCE_AWAY_DELAY_S, 600),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=7200)),
            }
        )

        # Use add_suggested_values_to_schema for optional entity selectors.
        # Unlike manual description={"suggested_value": None}, this method
        # only sets suggested_value when a value actually exists, preventing
        # the EntitySelector frontend from receiving a confusing None value.
        suggested: dict[str, str] = {}
        window_sensor = opts.get(CONF_WINDOW_SENSOR_ID)
        if window_sensor:
            suggested[CONF_WINDOW_SENSOR_ID] = window_sensor
        presence_sensor = opts.get(CONF_PRESENCE_SENSOR_ID)
        if presence_sensor:
            suggested[CONF_PRESENCE_SENSOR_ID] = presence_sensor

        if suggested:
            options_schema = self.add_suggested_values_to_schema(
                options_schema, suggested
            )

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
        )
