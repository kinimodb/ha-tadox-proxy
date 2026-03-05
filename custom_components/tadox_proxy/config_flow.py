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


def _entity_options(
    hass, domain: str, device_class: str | None = None
) -> list[selector.SelectOptionDict]:
    """Build SelectSelector options from entities matching domain/device_class."""
    options: list[selector.SelectOptionDict] = []
    for state in hass.states.async_all(domain):
        if device_class and state.attributes.get("device_class") != device_class:
            continue
        friendly = state.attributes.get("friendly_name", state.entity_id)
        options.append(
            selector.SelectOptionDict(
                value=state.entity_id,
                label=f"{friendly} ({state.entity_id})",
            )
        )
    options.sort(key=lambda o: o["label"].lower())
    return options


class TadoxProxyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """UI setup for the integration (initial setup)."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}

        if user_input is not None:
            source_entity_id = user_input[CONF_SOURCE_ENTITY_ID]
            ext_temp_entity_id = user_input[CONF_EXTERNAL_TEMPERATURE_ENTITY_ID]
            name = (user_input.get(CONF_NAME) or "").strip() or "Tado X Proxy"

            # Validate entity existence (SelectSelector has no built-in check)
            if not self.hass.states.get(source_entity_id):
                errors[CONF_SOURCE_ENTITY_ID] = "entity_not_found"
            if not self.hass.states.get(ext_temp_entity_id):
                errors[CONF_EXTERNAL_TEMPERATURE_ENTITY_ID] = "temp_entity_not_found"

            if not errors:
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

        # Use SelectSelector instead of EntitySelector to avoid iOS WebView
        # crash (ReferenceError: elementId in ha-entity-picker component).
        schema = vol.Schema(
            {
                vol.Required(CONF_SOURCE_ENTITY_ID): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=_entity_options(self.hass, "climate"),
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_EXTERNAL_TEMPERATURE_ENTITY_ID): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=_entity_options(self.hass, "sensor", "temperature"),
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
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
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate required external temp sensor
            ext_temp = user_input.get(CONF_EXTERNAL_TEMPERATURE_ENTITY_ID, "")
            if ext_temp and not self.hass.states.get(ext_temp):
                errors[CONF_EXTERNAL_TEMPERATURE_ENTITY_ID] = "temp_entity_not_found"

            if not errors:
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

        # Build optional sensor option lists with a "none" choice
        binary_sensor_options = [
            selector.SelectOptionDict(value="", label="---"),
            *_entity_options(self.hass, "binary_sensor"),
        ]

        # Use SelectSelector instead of EntitySelector to avoid iOS WebView
        # crash (ReferenceError: elementId in ha-entity-picker component).
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
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=_entity_options(self.hass, "sensor", "temperature"),
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),

                # --- Window sensor (optional) ---
                vol.Optional(
                    CONF_WINDOW_SENSOR_ID,
                    default=opts.get(CONF_WINDOW_SENSOR_ID, ""),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=binary_sensor_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_WINDOW_DELAY_S,
                    default=opts.get(CONF_WINDOW_DELAY_S, 30),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=3600)),

                # --- Presence sensor (optional) ---
                vol.Optional(
                    CONF_PRESENCE_SENSOR_ID,
                    default=opts.get(CONF_PRESENCE_SENSOR_ID, ""),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=binary_sensor_options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_PRESENCE_AWAY_DELAY_S,
                    default=opts.get(CONF_PRESENCE_AWAY_DELAY_S, 1800),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=7200)),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
            errors=errors,
        )
