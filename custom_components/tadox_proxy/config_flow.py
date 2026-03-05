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
    CONF_COMFORT_TARGET,
    CONF_ECO_TARGET,
    CONF_BOOST_TARGET,
    CONF_BOOST_DURATION,
    CONF_AWAY_TARGET,
    CONF_FROST_PROTECTION_TARGET,
    CONF_WINDOW_SENSOR_ID,
    CONF_WINDOW_DELAY_S,
    CONF_PRESENCE_SENSOR_ID,
    CONF_PRESENCE_AWAY_DELAY_S,
)
from .parameters import RegulationConfig


class TadoxProxyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """UI setup for the integration (initial setup).

    Split into two steps so the HA Companion App WebView has time to
    fully initialise the EntitySelector JS component before it is
    rendered.  Step 1 collects the name (plain text), step 2 the
    entity selectors.
    """

    VERSION = 1

    def __init__(self) -> None:
        self._name: str = "Tado X Proxy"

    async def async_step_user(self, user_input=None):
        """Step 1: collect the proxy name (no EntitySelector)."""
        if user_input is not None:
            self._name = (user_input.get(CONF_NAME) or "").strip() or "Tado X Proxy"
            return await self.async_step_entities()

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default="Tado X Proxy"): str,
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_entities(self, user_input=None):
        """Step 2: select source climate entity and temperature sensor."""
        if user_input is not None:
            source_entity_id = user_input[CONF_SOURCE_ENTITY_ID]
            ext_temp_entity_id = user_input[CONF_EXTERNAL_TEMPERATURE_ENTITY_ID]

            await self.async_set_unique_id(source_entity_id)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=self._name,
                data={
                    CONF_SOURCE_ENTITY_ID: source_entity_id,
                    CONF_NAME: self._name,
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
            }
        )

        return self.async_show_form(step_id="entities", data_schema=schema)

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
            # Reload is triggered by the update_listener in __init__.py
            # AFTER HA has persisted the new options, avoiding stale data.
            return self.async_create_entry(title="", data=cleaned)

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
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=5.0)),
                vol.Required(
                    "correction_ki",
                    default=opts.get("correction_ki", defaults.tuning.ki),
                ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=0.1)),

                # --- Preset temperatures ---
                vol.Required(
                    CONF_COMFORT_TARGET,
                    default=opts.get(CONF_COMFORT_TARGET, 20.0),
                ): vol.All(vol.Coerce(float), vol.Range(min=5.0, max=30.0)),
                vol.Required(
                    CONF_ECO_TARGET,
                    default=opts.get(CONF_ECO_TARGET, defaults.presets.eco_target_c),
                ): vol.All(vol.Coerce(float), vol.Range(min=5.0, max=30.0)),
                vol.Required(
                    CONF_BOOST_TARGET,
                    default=opts.get(CONF_BOOST_TARGET, defaults.presets.boost_target_c),
                ): vol.All(vol.Coerce(float), vol.Range(min=5.0, max=30.0)),
                vol.Required(
                    CONF_BOOST_DURATION,
                    default=opts.get(CONF_BOOST_DURATION, defaults.presets.boost_duration_min),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=120)),
                vol.Required(
                    CONF_AWAY_TARGET,
                    default=opts.get(CONF_AWAY_TARGET, defaults.presets.away_target_c),
                ): vol.All(vol.Coerce(float), vol.Range(min=5.0, max=30.0)),
                vol.Required(
                    CONF_FROST_PROTECTION_TARGET,
                    default=opts.get(CONF_FROST_PROTECTION_TARGET, defaults.presets.frost_protection_target_c),
                ): vol.All(vol.Coerce(float), vol.Range(min=5.0, max=30.0)),

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

                # --- Presence sensor (optional) ---
                vol.Optional(CONF_PRESENCE_SENSOR_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="binary_sensor")
                ),
                vol.Required(
                    CONF_PRESENCE_AWAY_DELAY_S,
                    default=opts.get(CONF_PRESENCE_AWAY_DELAY_S, 1800),
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
