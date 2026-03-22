"""The Tado X Proxy integration."""
from __future__ import annotations

import logging
import math
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import CONF_EXTERNAL_TEMPERATURE_ENTITY_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)

# List the platforms that you want to support.
PLATFORMS: list[Platform] = [Platform.CLIMATE, Platform.NUMBER, Platform.SWITCH]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Tado X Proxy from a config entry."""

    # 1. Ensure DOMAIN dict exists in hass.data
    hass.data.setdefault(DOMAIN, {})

    # 2. Define the data update method
    async def async_update_data():
        """Fetch data from entities (Source & External Sensor)."""
        source_entity_id = entry.data.get("source_entity_id")

        # Priority: Options (Dynamic) > Data (Initial Config)
        external_sensor_id = entry.options.get(
            CONF_EXTERNAL_TEMPERATURE_ENTITY_ID,
            entry.data.get(CONF_EXTERNAL_TEMPERATURE_ENTITY_ID)
        )

        data = {
            "room_temp": None,
            "tado_internal_temp": None,
            "tado_setpoint": None,
            "tado_min_temp": None,
            "tado_max_temp": None,
        }

        # Get Room Temp from External Sensor
        if external_sensor_id:
            state = hass.states.get(external_sensor_id)
            if state and state.state not in ("unknown", "unavailable"):
                try:
                    value = float(state.state)
                    if math.isfinite(value):
                        data["room_temp"] = value
                    else:
                        _LOGGER.warning(
                            "Room temperature from %s is not finite: %s",
                            external_sensor_id, value,
                        )
                except (ValueError, TypeError):
                    _LOGGER.warning(
                        "Cannot parse room temperature from sensor %s: %r",
                        external_sensor_id, state.state,
                    )

        # Get Tado Internal Temp & Setpoint from Source Entity
        if source_entity_id:
            state = hass.states.get(source_entity_id)
            if state:
                # Try to get internal temperature attribute (device dependent)
                if state.attributes.get("current_temperature") is not None:
                    try:
                        value = float(state.attributes["current_temperature"])
                        if math.isfinite(value):
                            data["tado_internal_temp"] = value
                        else:
                            _LOGGER.warning(
                                "Tado internal temperature from %s is not finite: %s",
                                source_entity_id, value,
                            )
                    except (ValueError, TypeError):
                        _LOGGER.warning(
                            "Cannot parse tado internal temperature from %s: %r",
                            source_entity_id, state.attributes.get("current_temperature"),
                        )

                # Get min/max temperature limits from source entity
                for attr, key in (("min_temp", "tado_min_temp"), ("max_temp", "tado_max_temp")):
                    raw = state.attributes.get(attr)
                    if raw is not None:
                        try:
                            val = float(raw)
                            if math.isfinite(val):
                                data[key] = val
                        except (ValueError, TypeError):
                            pass

                # Get current Setpoint
                if state.attributes.get("temperature") is not None:
                    try:
                        value = float(state.attributes["temperature"])
                        if math.isfinite(value):
                            data["tado_setpoint"] = value
                        else:
                            _LOGGER.warning(
                                "Tado setpoint from %s is not finite: %s",
                                source_entity_id, value,
                            )
                    except (ValueError, TypeError):
                        _LOGGER.warning(
                            "Cannot parse tado setpoint from %s: %r",
                            source_entity_id, state.attributes.get("temperature"),
                        )

        return data

    # 3. Create the Coordinator
    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_{entry.title}",
        update_method=async_update_data,
        update_interval=timedelta(seconds=60), # Poll every 60s as backup
    )

    # 4. Attach the config entry to the coordinator
    coordinator.config_entry = entry

    # 5. Perform initial refresh
    await coordinator.async_config_entry_first_refresh()

    # 6. Store coordinator
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # 7. Load the platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # 8. Reload integration when options change (e.g. from the options flow).
    # This listener fires AFTER HA has persisted the new options, so the
    # reload always sees the up-to-date values.
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when options are changed."""
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
